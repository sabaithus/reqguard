"""CLI-интерфейс запуска агента одной командой:
python main.py <путь_к_документу> <путь_к_отчёту.md> [--with-html] [--json report.json]
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
from reqguard.pipeline import run_audit_pipeline
from reqguard.reporter_html import render_html_report
from reqguard.reporter_md import render_markdown_report

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
        type=Path,
        help="Путь к входному документу с требованиями (.md, .pdf, .docx, .txt)",
    )
    parser.add_argument(
        "output_path",
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
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
        tbl.add_row("Выделено требований", str(s.total_requirements))
        tbl.add_row("Критичных блокеров (CRITICAL)", f"[bold red]{s.critical_count}[/bold red]")
        tbl.add_row("Существенных (MAJOR)", f"[yellow]{s.major_count}[/yellow]")
        tbl.add_row("Незначительных (MINOR)", str(s.minor_count))
        tbl.add_row("Верифицировано дословных цитат", str(report.passport.verified_quotes_count))
        tbl.add_row("Отсеяно галлюцинаций", str(report.passport.hallucinations_filtered))
        tbl.add_row("Индекс готовности", f"[bold]{s.readiness_score}/100[/bold]")
        console.print(tbl)
        console.print(f"[bold green]Готово![/bold green] Отчёт сохранён: [underline]{out_path}[/underline]")
        for ef in extra_files:
            console.print(f"[dim]Дополнительный артефакт:[/dim] {ef}")
        return 0

    except (FileNotFoundError, ValueError, LLMGatewayError) as exc:
        console.print(f"[bold red]Ошибка выполнения:[/bold red] {exc}")
        return 2
    except KeyboardInterrupt:
        console.print("\n[yellow]Прервано пользователем.[/yellow]")
        return 130


if __name__ == "__main__":
    sys.exit(main())
