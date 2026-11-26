"""Build a Russian demo deck from a saved ReqGuard report. Requires python-pptx."""
import argparse
from html import escape
from pathlib import Path
from textwrap import shorten

from main import ROOT_DIR
from reqguard.models import AuditReport


def slides(report):
    p, s = report.passport, report.summary
    yield "Можно ли передать ТЗ в разработку?", [
        p.document_title, f"ReqGuard · {p.agent_version}", p.status_label,
        f"{p.source_file} · {p.run_timestamp}",
    ]
    yield "Результат проверки", [
        s.verdict, f"{s.total_requirements} требований · {s.total_defects} замечаний · {s.critical_count} критичных",
        f"{p.verified_quotes_count} привязок к источнику до объединения · {p.hallucinations_filtered} отклонённых записей",
        f"{p.model_name} · {p.llm_calls_count} ответов модели · {p.duration_sec:g} с",
    ]
    for finding in [d for d in report.set_defects if d.decision][:2]:
        yield finding.title, [shorten(f"«{e.quote}»", width=185, placeholder="…") + f" (строка {e.line_start})"
                              for e in finding.evidence[:2]] + [finding.question_for_analyst]
        yield "Какой результат проверит QA?", [finding.decision.scenario, *finding.decision.alternatives,
                                                finding.decision.acceptance_check]
    yield "Повторная проверка с владельцем", [
        "Аналитик сверяет замечание с цитатами. Владелец выбирает исход и экспортирует протокол.",
        "При повторном запуске агент сверяет отпечаток документа, вопрос, вариант и доказательства.",
        "Отчёт сохраняет принятые решения отдельно. Открытые замечания определяют итоговую готовность.",
    ]
    yield "Как воспроизвести результат", [
        "python main.py docs/requirements.pdf output/report.md --with-html",
        "python main.py docs/requirements.pdf output/v2.md --answers decisions.json",
        "Коды CI: 0 — GO, 1 — NO-GO, 2 — ошибка, 3 — неполная проверка.",
        "github.com/sabaithus/reqguard",
    ]


def build(source, destination):
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.util import Inches, Pt

    report = AuditReport.model_validate_json(source.read_bytes())
    deck = Presentation()
    deck.slide_width, deck.slide_height = Inches(13.33), Inches(7.5)
    pages = []
    for title, lines in slides(report):
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = RGBColor.from_string("102438")
        for i, line in enumerate([title, *lines]):
            y = .48 if i == 0 else 1.65 + (i-1)*1.15
            box = slide.shapes.add_textbox(Inches(.7), Inches(y), Inches(11.9), Inches(1.15))
            box.text_frame.word_wrap = True
            para = box.text_frame.paragraphs[0]
            para.text = line
            para.font.name = "Arial"
            para.font.size = Pt(30 if i == 0 else 20)
            para.font.color.rgb = RGBColor.from_string("73DFC7" if i == 0 else "F3F7FA")
        pages.append("<section><h1>" + escape(title) + "</h1>" +
                     "".join("<p>" + escape(v) + "</p>" for v in lines) + "</section>")
    destination.mkdir(parents=True, exist_ok=True)
    deck.save(destination / "ReqGuard_Presentation.pptx")
    css = "body{margin:0;background:#102438;color:#f3f7fa;font:24px Arial}section{padding:7vh 8vw;min-height:86vh;break-after:page}h1{color:#73dfc7}p{max-width:70ch}"
    (destination / "ReqGuard_Presentation.html").write_text(
        '<!doctype html><html lang="ru"><meta charset="utf-8"><title>ReqGuard</title><style>' +
        css + "</style>" + "".join(pages) + "</html>", encoding="utf8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("destination", type=Path, default=ROOT_DIR / "output", nargs="?")
    args = parser.parse_args()
    build(args.report, args.destination)
