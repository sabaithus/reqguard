"""E6: обязательство получить значение проверяется отдельно от способа его изменения."""
import json
import re
from pathlib import Path
from unittest.mock import patch

from reqguard.config import LLMSettings
from reqguard.llm_client import ResilientLLMClient
from reqguard.parser import parse_document
from reqguard.pipeline import run_audit_pipeline
from reqguard.prompts import SYSTEM_PROMPT_AUDITOR, build_pass1_atomic_prompt, build_pass2_set_prompt
from reqguard.structural_scanner import run_structural_scan

ROOT = Path(__file__).resolve().parents[1]
PAIR = ROOT / "eval" / "e6" / "reminder_no_read.md"


def test_set_prompt_separates_read_obligation_by_described_behaviour():
    prompt = build_pass2_set_prompt("[L001] x", "- REQ-01")
    assert "по описанной логике и ответу, а не по названию, описанию или HTTP-методу" in prompt
    assert "Не утверждай, что операция отсутствует в системе" in prompt
    assert "не называй метод, HTTP-глагол, эндпоинт или маршрут" in prompt
    assert "уже возвращают действующее значение" in prompt


def test_prompts_carry_no_case_specific_terms():
    text = SYSTEM_PROMPT_AUDITOR + build_pass1_atomic_prompt("") + build_pass2_set_prompt("", "")
    for term in ("Ф-3", "Ф-4", "saveLimit", "savelimit", "E6", "порог", "тиын", "/alerts"):
        assert term not in text, term


def _line(doc, needle):
    return next(dl for dl in doc.lines if needle in dl.raw_text)


def test_read_gap_survives_next_to_change_finding(tmp_path):
    doc = parse_document(PAIR)
    duty = _line(doc, "получает и изменяет")
    logic = _line(doc, "Записать новый интервал")
    desc = _line(doc, "| **Description** |")
    span = lambda dl: dict(line_start=dl.line_no, line_end=dl.line_no, quote=dl.raw_text.strip())
    reviewed = [r.report_id for r in run_structural_scan(doc).baseline_requirements]
    change = dict(related_req_ids=[], criterion="INTERFACE_INTEGRITY", severity="MAJOR",
                  title="Ответ изменения не возвращает записанное значение",
                  problem="Операция изменения возвращает только статус; записанное значение в ответе не описано.",
                  impact="Клиент не сверяет результат изменения.", question_for_analyst="Нужно ли вернуть записанное значение?",
                  decision=None, recommendation="Согласовать ответ операции изменения.", evidence=[span(desc), span(logic)])
    read_gap = dict(related_req_ids=[], criterion="COMPLETE_SET", severity="MAJOR",
                    title="Обязательство получения значения не обслужено описанным поведением контракта",
                    problem="Интеграция обязывает получать значение, а единственная описанная логика контракта записывает новое значение и возвращает статус.",
                    impact="Нельзя реализовать отображение действующего значения по документу.",
                    question_for_analyst="Каким описанным контрактом клиент получает действующее значение?",
                    decision=None, recommendation="-", evidence=[span(duty), span(logic)])
    replies = [dict(requirements=[], atomic_defects=[]),
               dict(set_defects=[change, read_gap], reviewed_req_ids=reviewed, additional_atomic_defects=[])]
    settings = LLMSettings(provider="gemini", api_key="test", primary_model="mock", max_retries=1)
    with patch.object(ResilientLLMClient, "_call_provider", side_effect=list(map(json.dumps, replies))):
        report = run_audit_pipeline(PAIR, settings)
    titles = {d.title for d in report.set_defects}
    assert read_gap["title"] in titles and change["title"] in titles
    kept = next(d for d in report.set_defects if d.title == read_gap["title"])
    assert {e.line_start for e in kept.evidence} == {duty.line_no, logic.line_no}
    assert all(e.verified for e in kept.evidence)
