"""Встроенный локальный Web-сервер и интерфейс ReqGuard Studio для живой демонстрации."""

from __future__ import annotations

import base64
import json
import tempfile
import threading
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from reqguard.config import load_settings
from reqguard.models import AuditReport, OwnerProtocol
from reqguard.pipeline import run_audit_pipeline
from reqguard.reporter_html import render_empty_studio, render_html_report
from reqguard.reporter_md import render_markdown_report

def create_web_server(
    host: str = "127.0.0.1",
    port: int = 8080,
    env_file: Path | None = None,
    base_dir: Path | None = None,
) -> ThreadingHTTPServer:
    """Создаёт и настраивает локальный ThreadingHTTPServer для ReqGuard Studio."""
    root_dir = (base_dir or Path.cwd()).resolve()
    jobs_lock = threading.Lock()
    jobs: dict[str, dict[str, Any]] = {}

    def _latest_report() -> AuditReport | None:
        """Сдаваемый отчёт: комплект сдачи, последний прогон Studio или самый свежий пример в examples/."""
        candidates = [root_dir / "output/submission/report.json"]
        candidates += sorted((root_dir / "examples").glob("*/report.json"), key=lambda f: f.stat().st_mtime, reverse=True)
        candidates.append(root_dir / "runs/report.json")
        for candidate in candidates:
            if candidate.exists():
                try:
                    return AuditReport.model_validate_json(candidate.read_text(encoding="utf-8"))
                except Exception:
                    continue
        return None

    class ReqGuardHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, status: int, html_text: str) -> None:
            body = html_text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                report = _latest_report()
                self._send_html(HTTPStatus.OK, render_html_report(report, studio=True) if report else render_empty_studio())
                return

            if path == "/api/latest":
                report = _latest_report()
                if report is None:
                    self._send_json(HTTPStatus.OK, {"available": False})
                    return
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "available": True,
                        "report": report.model_dump(),
                        "markdown": render_markdown_report(report),
                        "html": render_html_report(report),
                    },
                )
                return

            if path.startswith("/report/"):
                job_id = path.removeprefix("/report/").strip()
                with jobs_lock:
                    page = (jobs.get(job_id) or {}).get("studio_html")
                if page is None:
                    self._send_html(HTTPStatus.NOT_FOUND, render_empty_studio())
                    return
                self._send_html(HTTPStatus.OK, page)
                return

            if path.startswith("/api/jobs/"):
                job_id = path.removeprefix("/api/jobs/").strip()
                with jobs_lock:
                    job = jobs.get(job_id)
                    if job is None:
                        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Задача не найдена"})
                        return
                    snapshot = dict(job)
                    snapshot["logs"] = list(job["logs"])
                self._send_json(HTTPStatus.OK, snapshot)
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Маршрут не найден"})

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path != "/api/analyze":
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Маршрут не найден"})
                return

            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16 * 1024 * 1024:
                    self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Допустимый размер запроса — до 16 МиБ."})
                    return
                raw_body = self.rfile.read(length)
                req_json = json.loads(raw_body.decode("utf-8"))
                filename = Path(str(req_json.get("filename") or "requirements.md")).name
                content_b64 = str(req_json.get("content_base64") or "")
                structural_only = bool(req_json.get("structural_only", False))
                answers = OwnerProtocol.model_validate(req_json["answers"]) if req_json.get("answers") is not None else None
                if not content_b64:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Пустое содержимое файла"})
                    return
                file_bytes = base64.b64decode(content_b64, validate=True)
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": f"Некорректный запрос: {exc}"})
                return

            job_id = uuid.uuid4().hex[:12]
            with jobs_lock:
                active = sum(entry["status"] in ("running", "queued") for entry in jobs.values())
                if active >= 2:
                    self._send_json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "Два задания уже обрабатываются."})
                    return
                completed = [key for key, entry in jobs.items() if entry["status"] in ("done", "error")]
                for key in completed[:-7]:
                    del jobs[key]
                jobs[job_id] = {
                    "status": "running",
                    "logs": ["Файл принят сервером. Подготовка анализа..."],
                    "error": None,
                }

            def _worker() -> None:
                def _log(msg: str) -> None:
                    with jobs_lock:
                        jobs[job_id]["logs"].append(msg)

                try:
                    settings = load_settings(env_file)
                    if structural_only:
                        settings.provider = "none"
                    with tempfile.TemporaryDirectory() as tmpdir:
                        in_file = Path(tmpdir) / filename
                        in_file.write_bytes(file_bytes)
                        report = run_audit_pipeline(
                            input_path=in_file,
                            settings=settings,
                            progress_cb=_log,
                            allow_structural_only=structural_only,
                            answers=answers,
                        )
                    md_text = render_markdown_report(report)
                    html_text = render_html_report(report)
                    with jobs_lock:
                        jobs[job_id].update(
                            {
                                "status": "done",
                                "report": report.model_dump(),
                                "markdown": md_text,
                                "html": html_text,
                                "studio_html": render_html_report(report, studio=True),
                            }
                        )
                except Exception as exc:
                    with jobs_lock:
                        jobs[job_id]["status"] = "error"
                        jobs[job_id]["error"] = str(exc)

            threading.Thread(target=_worker, daemon=True).start()
            self._send_json(HTTPStatus.ACCEPTED, {"job_id": job_id})

    return ThreadingHTTPServer((host, port), ReqGuardHandler)


def run_web_server(
    host: str = "127.0.0.1",
    port: int = 8080,
    env_file: Path | None = None,
    open_browser: bool = True,
    base_dir: Path | None = None,
) -> None:
    """Запускает локальный сервер ReqGuard Studio и обслуживает запросы до Ctrl+C."""
    server = create_web_server(host=host, port=port, env_file=env_file, base_dir=base_dir)
    url = f"http://{host}:{server.server_port}"
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    finally:
        server.server_close()
