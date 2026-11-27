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
