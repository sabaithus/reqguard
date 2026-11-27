"""Ссылки на строки в скобках не считаются пунктами нумерованного перечня."""
from reqguard.models import RunPassport, SetDefect, SourceSpan
from reqguard.parser import parse_document
from reqguard.verifier import build_verified_audit_report


def _report(tmp_path, problem):
    lines = ["BR-1. Курьер видит текущий адрес доставки.", "BR-2. Сервис только заменяет адрес и возвращает статус."]
    src = tmp_path / "spec.md"
    src.write_text("\n".join(lines), encoding="utf8")
    doc = parse_document(src)
    finding = SetDefect(defect_id="S1", related_req_ids=[], criterion="COMPLETE_SET", severity="MAJOR", title="t",
                        problem=problem, impact="i", question_for_analyst="q", decision=None, recommendation="r",
                        evidence=[SourceSpan(line_start=1, line_end=1, quote=lines[0]),
                                  SourceSpan(line_start=2, line_end=2, quote=lines[1])], origin="LLM")
    passport = RunPassport(document_title="t", source_file="spec.md", source_format="MD", source_sha256=doc.sha256,
                           total_lines=2, run_timestamp="-", provider="p", model_name="m", agent_version="v", duration_sec=0.0)
    return build_verified_audit_report(doc=doc, passport=passport, requirements=[], candidate_atomic=[], candidate_set=[finding])


def test_parenthesised_line_references_are_not_an_enumeration(tmp_path):
    problem = "Требование (строка 1) обязывает показывать адрес, а контракт (строка 2) описывает только замену."
    assert len(_report(tmp_path, problem).set_defects) == 1


def test_enumerated_causes_are_still_rejected(tmp_path):
    problem = "Две причины: 1) адрес не возвращается 2) статус не описан."
    assert _report(tmp_path, problem).set_defects == []
