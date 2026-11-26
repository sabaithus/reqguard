"""CLI-интерфейс запуска агента одной командой:
python main.py <путь_к_документу> <путь_к_отчёту.md> [--with-html] [--json report.json]
или интерактивный Web-режим:
python main.py --serve
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from reqguard.config import load_settings
from reqguard.llm_client import LLMGatewayError
from reqguard.models import OwnerProtocol
from reqguard.pipeline import run_audit_pipeline
from reqguard.reporter_html import render_html_report
from reqguard.reporter_md import render_markdown_report
from reqguard.web_ui import run_web_server

console = Console(stderr=True)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reqguard",
        description=(
            "ReqGuard — гибридный ИИ-агент статической проверки качества требований "
            "(ISO/IEC/IEEE 29148:2018 & IEEE Std 830-1998)."
        ),
    )
    parser.add_argument(
        "input_path",
        nargs="?",
        default=None,
        type=Path,
        help="Путь к входному документу с требованиями (.md, .pdf, .docx, .txt)",
    )
    parser.add_argument(
        "output_path",
        nargs="?",
        default=None,
        type=Path,
        help="Путь для сохранения итогового отчёта на русском языке (.md)",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Необязательный путь к файлу .env (по умолчанию ищется в текущей папке)",
    )
    parser.add_argument(
        "--with-html",
        action="store_true",
        help="Дополнительно сохранить интерактивный HTML-отчёт рядом с основным файлом",
    )
    parser.add_argument(
        "--json",
        dest="json_path",
        type=Path,
        default=None,
        help="Дополнительно сохранить машиночитаемый JSON-артефакт проверки",
    )
    parser.add_argument(
        "--structural-only",
        action="store_true",
        help="Выполнить только детерминированный статический анализ без сетевого вызова LLM (для автономной самопроверки)",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Запустить локальный интерактивный Web-интерфейс ReqGuard Studio",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Хост для Web-сервера --serve (по умолчанию 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Порт для Web-сервера --serve (по умолчанию 8080)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Не открывать браузер автоматически при запуске --serve",
    )
    parser.add_argument("--answers", type=Path, help="JSON-протокол решений владельца для этого входного документа")
    parser.add_argument("--critic", action="store_true", help="Дополнительный проход модели для проверки замечаний с контрцитатами")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.serve:
        url = f"http://{args.host}:{args.port}"
        console.print(
            Panel.fit(
                f"[bold cyan]ReqGuard Studio[/bold cyan] — Локальный Web-интерфейс\n"
                f"Откройте в браузере: [bold green]{url}[/bold green]\n"
                f"[dim]Нажмите Ctrl+C для остановки сервера[/dim]",
                border_style="cyan",
            )
        )
        try:
            run_web_server(
                host=args.host,
                port=args.port,
                env_file=args.env_file,
                open_browser=not args.no_browser,
            )
            return 0
        except KeyboardInterrupt:
            console.print("\n[yellow]Сервер ReqGuard Studio остановлен.[/yellow]")
            return 0

    if args.input_path is None or args.output_path is None:
        parser.error("Для запуска проверки укажите два аргумента: input_path и output_path (или используйте --serve).")

    try:
        paths = [args.input_path, args.output_path]
        if args.with_html and args.output_path.suffix.lower() != ".html":
            paths.append(args.output_path.with_suffix(".html"))
        if args.json_path:
            paths.append(args.json_path)
        if args.answers:
            paths.append(args.answers)
        for i, path in enumerate(paths):
            for previous in paths[:i]:
                if path.resolve() == previous.resolve() or (
                    path.exists() and previous.exists() and path.samefile(previous)
                ):
                    raise ValueError(f"Пути входа и результатов должны различаться: {path}")
        settings = load_settings(args.env_file)
        if args.structural_only:
            settings.provider = "none"

        console.print(
            Panel.fit(
                f"[bold cyan]ReqGuard[/bold cyan] — Статический аудит требований\n"
                f"Входной документ: [yellow]{args.input_path}[/yellow]\n"
                f"Целевой отчёт:    [green]{args.output_path}[/green]",
                border_style="cyan",
            )
        )

        report = run_audit_pipeline(
            input_path=args.input_path,
            settings=settings,
            progress_cb=lambda msg: console.print(f"[dim]•[/dim] {msg}"),
            allow_structural_only=args.structural_only,
            answers=OwnerProtocol.model_validate_json(args.answers.read_bytes()) if args.answers else None,
            critic=args.critic,
        )

        # Запись основного русского отчёта
        out_path: Path = args.output_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        md_content = render_markdown_report(report)
        out_path.write_text(md_content, encoding="utf-8")

        extra_files: list[str] = []
        if args.with_html or out_path.suffix.lower() == ".html":
            html_target = out_path if out_path.suffix.lower() == ".html" else out_path.with_suffix(".html")
            html_target.write_text(render_html_report(report), encoding="utf-8")
            extra_files.append(str(html_target))

        if args.json_path:
            args.json_path.parent.mkdir(parents=True, exist_ok=True)
            args.json_path.write_text(
                report.model_dump_json(indent=2),
                encoding="utf-8",
            )
            extra_files.append(str(args.json_path))

        s = report.summary
        tbl = Table(title="Сводка проверки", show_header=True, header_style="bold magenta")
        tbl.add_column("Показатель")
        tbl.add_column("Значение", justify="right")
        tbl.add_row("Полнота анализа", report.passport.status_label)
        tbl.add_row("Выделено требований", str(s.total_requirements))
        tbl.add_row("Критичных блокеров (CRITICAL)", f"[bold red]{s.critical_count}[/bold red]")
        tbl.add_row("Существенных (MAJOR)", f"[yellow]{s.major_count}[/yellow]")
        tbl.add_row("Незначительных (MINOR)", str(s.minor_count))
        tbl.add_row("Верифицировано дословных цитат", str(report.passport.verified_quotes_count))
        tbl.add_row("Отклонено записей", str(report.passport.hallucinations_filtered))
        tbl.add_row("Индекс готовности", f"[bold]{s.score_label}[/bold]")
        console.print(tbl)
        console.print(f"[bold green]Готово![/bold green] Отчёт сохранён: [underline]{out_path}[/underline]")
        for ef in extra_files:
            console.print(f"[dim]Дополнительный артефакт:[/dim] {ef}")
        return report.exit_code

    except (OSError, ValueError, LLMGatewayError) as exc:
        console.print(f"[bold red]Ошибка выполнения:[/bold red] {exc}")
        return 2
    except KeyboardInterrupt:
        console.print("\n[yellow]Прервано пользователем.[/yellow]")
        return 130


if __name__ == "__main__":
    sys.exit(main())
