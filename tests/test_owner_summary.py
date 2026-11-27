from pathlib import Path
from html import escape

from reqguard.config import LLMSettings
from reqguard.pipeline import run_audit_pipeline
from reqguard.reporter_html import render_html_report


def test_owner_summary_has_sources_options_and_qa():
    path = Path(__file__).resolve().parents[1] / "eval" / "notice.md"
    report = run_audit_pipeline(path, LLMSettings(provider="none", api_key="", primary_model=""), allow_structural_only=True)
    summary = render_html_report(report).split("<h2>Вопросы владельцу продукта</h2>")[1].split('<div class="filters">')[0]
    case = next(f for f in report.set_defects if f.decision)
    for text in [case.defect_id, *(s.quote for s in case.evidence), *case.decision.alternatives, case.decision.acceptance_check]:
        assert escape(text) in summary

def test_operation_identifier_prevents_false_critical(tmp_path):
    from reqguard.models import AtomicDefect, RequirementItem, RunPassport, SourceSpan
    from reqguard.parser import parse_document
    from reqguard.verifier import build_verified_audit_report

    line = "При повторной доставке результат сохраняется."
    source = tmp_path / "events.md"
    source.write_text(line + "\nХранить идентификатор операции.", encoding="utf8")
    doc = parse_document(source)
    span = SourceSpan(line_start=1, line_end=1, quote=line)
    req = RequirementItem(report_id="REQ-01", title=line, source=span)
    issue = AtomicDefect(defect_id="a", req_id="REQ-01", criterion="COMPLETE", severity="CRITICAL",
                        problem="Не указан механизм обеспечения идемпотентности (уникальный ключ или transaction_id).",
                        impact="Нельзя выбрать ключ.", recommendation="Уточнить ключ.", evidence=[span])
    passport = RunPassport(document_title="events", source_file=source.name, source_format="Markdown", source_sha256=doc.sha256,
                          total_lines=2, run_timestamp="test", provider="test", model_name="fixture", agent_version="test", duration_sec=0)
    result = build_verified_audit_report(doc, passport, [req], [issue], [])
    assert result.atomic_defects[0].severity == "MAJOR"
