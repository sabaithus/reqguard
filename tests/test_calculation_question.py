from pathlib import Path

from reqguard.models import AuditReport
from reqguard.parser import parse_document
from reqguard.verifier import build_verified_audit_report


ROOT = Path(__file__).resolve().parents[1]


def recheck(fix=None, blocked=None, source=None):
    # Preserve the actual model finding, including the decision and its source.
    report = AuditReport.model_validate_json((ROOT / "examples/c1f6e62/report.json").read_bytes())
    issue = report.atomic_defects[0]
    if fix is not None:
        issue.suggested_fix = fix
    if blocked is not None:
        issue.impact = "Не согласован результат. Блокируемое решение: " + blocked
    doc = parse_document(source or ROOT / "docs/requirements.pdf")
    if source:
        span = issue.evidence[0].model_copy(update=dict(line_start=1, line_end=1, quote=doc.get_line(1).raw_text,
                                                        page=None, verified=False))
        issue.evidence = [span]
        next(req for req in report.requirements if req.report_id == issue.req_id).source = span
    return build_verified_audit_report(doc, report.passport, report.requirements, [issue], []).atomic_defects[0]


def test_historical_question_changes_the_recalculated_quantity():
    issue = recheck()
    assert not issue.suggested_fix
    assert issue.severity == "MAJOR"
    assert "Блокируемое решение:" not in issue.impact
    assert issue.evidence and issue.recommendation


def test_supported_quantity_and_concrete_release_decision_are_preserved():
    question = "Как пересчитывать расходы при поздней обработке?"
    issue = recheck(question, "Пересчитать расходы сразу или после закрытия периода?")
    assert issue.suggested_fix == question and issue.severity == "CRITICAL"


def test_bad_question_does_not_remove_independent_concrete_blocker():
    issue = recheck(blocked="При отмене требуется перерасчёт расходов сразу или в конце периода?")
    assert not issue.suggested_fix and issue.severity == "CRITICAL"


def test_unrelated_inventory_document(tmp_path):
    source = tmp_path / "warehouse.md"
    source.write_text("Списание уменьшает остаток на складе.\nТариф задаёт диспетчер.", encoding="utf-8")
    decision = "Порядок перерасчёта остатка при списании"
    bad = recheck("Как пересчитать тариф при списании?", decision, source)
    good = recheck("Как пересчитать остаток при списании?", decision, source)
    assert bad.suggested_fix is None
    assert good.suggested_fix == "Как пересчитать остаток при списании?"
