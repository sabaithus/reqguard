import json
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from reqguard.cli import main
from reqguard.config import LLMSettings
from reqguard.llm_client import ResilientLLMClient
from reqguard.pipeline import _Pass1Response, _Pass2Response, run_audit_pipeline
from reqguard.parser import parse_document
from reqguard.structural_scanner import run_structural_scan


@pytest.mark.parametrize("suffix,content", [("bin", b"abc"), ("pdf", b"not a PDF"), ("docx", b"x"), ("txt", b""), ("txt", b"\xff")])
def test_invalid_input_returns_cli_error(tmp_path, suffix, content):
    source = tmp_path / f"source.{suffix}"
    source.write_bytes(content)
    out = tmp_path / "report.md"
    assert main([str(source), str(out), "--structural-only"]) == 2
    assert not out.exists()


def test_no_requirements_never_authorize_development(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("банан", encoding="utf8")
    out = tmp_path / "report.json"
    assert main([str(source), str(tmp_path / "report.md"), "--json", str(out), "--structural-only"]) == 0
    result = json.loads(out.read_text(encoding="utf8"))
    assert result["passport"]["analysis_status"] == "insufficient_input"
    assert result["summary"]["readiness_score"] is None
    assert result["summary"]["can_start_development"] is None
    assert result["summary"]["can_start_testing"] is None


@pytest.mark.parametrize("schema", [_Pass1Response, _Pass2Response])
def test_missing_phase_payload_is_invalid(schema):
    with pytest.raises(ValidationError):
        schema.model_validate({})


def test_unsubstantiated_conflict(tmp_path):
    source = tmp_path / "archive.txt"
    line = "Документ хранится в архиве."
    source.write_text(line, encoding="utf8")
    requirement = dict(temp_id="R1", title="Хранение документа", line_start=1, line_end=1, quote=line)
    quotes = [dict(line_start=1, line_end=1, quote=q) for q in (line, "Документ немедленно уничтожается.")]
    finding = dict(title="Несовместимые правила", criterion="CONSISTENT", severity="CRITICAL",
                   problem="Противоположные действия.", impact="Неясное поведение.",
                   question_for_analyst="Как обработать документ?", recommendation="Уточнить правило.", evidence=quotes)
    replies = [dict(requirements=[requirement], atomic_defects=[]), dict(set_defects=[finding])]
    with patch.object(ResilientLLMClient, "_call_provider", side_effect=[json.dumps(r) for r in replies]):
        report = run_audit_pipeline(source, LLMSettings(provider="gemini", api_key="test", primary_model="mock"))
    assert not report.set_defects
    assert report.passport.hallucinations_filtered == 1
    assert report.summary.can_start_development is not True


@pytest.mark.parametrize("expected,conflicts", [("Апрельский", 1), ("Мартовский", 0)])
def test_accounting_month_changes_with_acceptance_result(tmp_path, expected, conflicts):
    source = tmp_path / "input.md"
    source.write_text(
        "Месяц определяется по дате совершения покупки.\n\n### Критерии приёмки\n"
        "| Условия | Действие | Результат |\n| --- | --- | --- |\n"
        f"| Покупка 31 марта | Провести 1 апреля | {expected} счётчик вырос на 10 |\n", encoding="utf8"
    )
    issues = [d for d in run_structural_scan(parse_document(source)).set_defects if d.defect_id == "calendar"]
    assert len(issues) == conflicts
    assert all(len(d.evidence) == 2 for d in issues)


def test_web_upload_and_report_without_network(tmp_path):
    import base64
    import io
    import time
    from types import SimpleNamespace
    from reqguard.web_ui import create_web_server

    # Instantiate the real HTTP handler on byte streams. No TCP port is required.
    with patch("reqguard.web_ui.ThreadingHTTPServer", side_effect=lambda address, handler: handler):
        handler = create_web_server(base_dir=tmp_path)

    def request(method, url, payload=None):
        body = json.dumps(payload).encode() if payload is not None else b""
        raw = f"{method} {url} HTTP/1.0\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body
        response = bytearray()
        connection = SimpleNamespace(makefile=lambda *_: io.BytesIO(raw), sendall=response.extend)
        handler(connection, ("127.0.0.1", 0), SimpleNamespace())
        headers, content = bytes(response).split(b"\r\n\r\n", 1)
        return int(headers.split()[1]), content

    assert request("GET", "/")[0] == 200
    status, content = request("POST", "/api/analyze", {
        "filename": "input.txt", "content_base64": base64.b64encode(b"sample").decode(), "structural_only": True,
    })
    assert status == 202
    job_url = "/api/jobs/" + json.loads(content)["job_id"]
    deadline = time.monotonic() + 5
    while True:
        status, body = request("GET", job_url)
        result = json.loads(body)
        if result["status"] != "running" or time.monotonic() > deadline:
            break
        time.sleep(0.02)
    assert status == 200 and result["status"] == "done"
    assert result["report"]["summary"]["can_start_development"] is None
    assert "ГОТОВНОСТЬ НЕ УСТАНОВЛЕНА" in result["html"]


def test_failed_phase_two_is_visible_in_saved_report(tmp_path):
    source = tmp_path / "input.md"
    line = "Сервис должен сохранять отчёт в архив."
    source.write_text(line, encoding="utf8")
    first = {"requirements": [{"temp_id": "R1", "line_start": 1, "line_end": 1, "quote": line, "title": "Архив"}], "atomic_defects": []}
    settings = LLMSettings(provider="gemini", api_key="test", primary_model="mock", max_retries=1)
    with patch.object(ResilientLLMClient, "_call_provider", side_effect=[json.dumps(first), "invalid"]):
        report = run_audit_pipeline(source, settings)
    assert report.passport.analysis_status == "phase2_failed"
    assert report.summary.can_start_development is None
    assert report.summary.readiness_score is None

def test_submission_bundle_is_atomic_and_requires_complete_run(tmp_path):
    import importlib.util
    import hashlib

    script = Path(__file__).resolve().parents[1] / "prepare_submission.py"
    # Load the same entry point used by the CLI from the project root.
    import sys
    with patch.object(sys, "path", [str(script.parent), *sys.path]):
        spec = importlib.util.spec_from_file_location("prepare_submission", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

    source = tmp_path / "archive.txt"
    line = "Документ хранится в архиве."
    source.write_text(line, encoding="utf8")
    first = {"requirements": [{"temp_id": "R1", "line_start": 1, "line_end": 1,
                              "quote": line, "title": "Архив"}], "atomic_defects": []}
    settings = LLMSettings(provider="gemini", api_key="test", primary_model="mock", max_retries=1)
    destination = tmp_path / "bundle"
    with patch("reqguard.cli.load_settings", return_value=settings):
        with patch.object(ResilientLLMClient, "_call_provider", side_effect=[json.dumps(first), "invalid"]):
            with pytest.raises(ValueError, match="неполная"):
                module.prepare(source, destination)
            assert not destination.exists()
        with patch.object(ResilientLLMClient, "_call_provider", side_effect=[json.dumps(first), '{"set_defects": []}']):
            module.prepare(source, destination)
    report = json.loads((destination / "report.json").read_text(encoding="utf8"))
    assert report["passport"]["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert (destination / "report.html").exists()
    assert (destination / "input" / source.name).read_bytes() == source.read_bytes()
    with pytest.raises(ValueError, match="существует"):
        module.prepare(source, destination)
