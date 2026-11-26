import json
from pathlib import Path
from unittest.mock import patch

from reqguard.cli import main
from reqguard.config import LLMSettings
from reqguard.llm_client import ResilientLLMClient


def test_critic_requires_a_real_counterquote(tmp_path):
    from reqguard.critic import Reviews, review_candidates
    from reqguard.models import AtomicDefect, SourceSpan
    from reqguard.parser import parse_document
    from unittest.mock import Mock

    line = "BR-1. The deadline is thirty days."
    source = tmp_path / "spec.md"
    source.write_text(line, encoding="utf8")
    span = SourceSpan(line_start=1, line_end=1, quote=line)
    defect = AtomicDefect(defect_id="A1", req_id="R1", criterion="COMPLETE", severity="CRITICAL",
                          problem="No deadline", impact="Unknown deadline", recommendation="Specify deadline", evidence=[span])
    client = Mock()
    for quote, retained in [(line, False), ("Fabricated threshold", True)]:
        client.generate_structured.return_value = Reviews.model_validate({"reviews": [
            dict(key="A1", action="reject", reason="Deadline already given", counter_evidence=dict(line_start=1, line_end=1, quote=quote))
        ]})
        atomic, _, notes = review_candidates(parse_document(source), client, [defect], [], None)
        assert bool(atomic) == retained
        assert bool(notes) != retained


def test_complete_go_and_no_go_are_distinct(tmp_path):
    line = "BR-1. The bank must archive each receipt."
    source = tmp_path / "spec.md"
    source.write_text(line, encoding="utf8")
    requirement = dict(temp_id="r", title="archive each receipt", doc_id="BR-1", line_start=1, line_end=1, quote=line)
    finding = dict(temp_req_id="r", criterion="COMPLETE", severity="CRITICAL", blocked_decision="Какой документ архивировать?",
                   problem="Не определён документ.", impact="Нельзя выбрать объект.", recommendation="Определить объект.",
                   line_start=1, line_end=1, quote=line)
    settings = LLMSettings(provider="gemini", api_key="test", primary_model="mock", max_retries=1)
    for items, code in [([], 0), ([finding], 1)]:
        replies = [dict(requirements=[requirement], atomic_defects=items), dict(set_defects=[])]
        with patch("reqguard.cli.load_settings", return_value=settings), patch.object(
            ResilientLLMClient, "_call_provider", side_effect=list(map(json.dumps, replies))
        ):
            assert main([str(source), str(tmp_path / "report.md")]) == code


def test_owner_protocol_is_bound_to_the_source_and_finding(tmp_path):
    source = Path(__file__).resolve().parents[1] / "eval/notice.md"
    target = tmp_path / "report.json"
    args = [str(source), str(tmp_path / "report.md"), "--structural-only", "--json", str(target)]
    assert main(args) == 3
    original = json.loads(target.read_text(encoding="utf8"))
    case = next(d for d in original["set_defects"] if d["decision"])
    assert "sms" in case["decision"]["scenario"].lower() and "недел" in case["title"]
    answer = dict(question=case["question_for_analyst"], evidence=case["evidence"], status="selected",
                  selected_outcome=case["decision"]["alternatives"][1])
    protocol = dict(source_sha256=original["passport"]["source_sha256"], exported_at="2026-11-27T00:00:00Z", decisions=[answer])
    file = tmp_path / "decisions.json"
    file.write_text(json.dumps(protocol), encoding="utf8")
    assert main([*args, "--answers", str(file)]) == 3
    revised = json.loads(target.read_text(encoding="utf8"))
    assert revised["summary"]["critical_count"] == original["summary"]["critical_count"] - 1
    assert revised["accepted_decisions"][0]["outcome"] == answer["selected_outcome"]
    answer["selected_outcome"] = "Wrong outcome"
    file.write_text(json.dumps(protocol), encoding="utf8")
    assert main([*args, "--answers", str(file)]) == 2
    protocol["source_sha256"] = "0" * 64
    file.write_text(json.dumps(protocol), encoding="utf8")
    assert main([*args, "--answers", str(file)]) == 2
