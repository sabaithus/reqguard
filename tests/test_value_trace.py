"""E8/E9: единицы сравниваемых величин и источник значения, которое передаёт шаг процесса."""
import json
from pathlib import Path
from unittest.mock import patch

from reqguard.config import LLMSettings
from reqguard.llm_client import ResilientLLMClient
from reqguard.parser import parse_document
from reqguard.pipeline import run_audit_pipeline
from reqguard.prompts import SYSTEM_PROMPT_AUDITOR, build_pass1_atomic_prompt, build_pass2_set_prompt
from reqguard.structural_scanner import run_structural_scan

ROOT = Path(__file__).resolve().parents[1]
GAP = ROOT / "eval" / "e8e9" / "water_gap.md"


def test_set_prompt_traces_units_of_compared_values():
    prompt = build_pass2_set_prompt("[L001] x", "- REQ-01")
    assert "сравнивает, суммирует или вычитает величины" in prompt
    assert "правило приведения не описано" in prompt
    assert "Не требуй округления, если приведение между единицами точное" in prompt


def test_set_prompt_traces_source_of_passed_value():
    prompt = build_pass2_set_prompt("[L001] x", "- REQ-01")
    assert "передаёт, отправляет, показывает или использует" in prompt
    assert "хранимое поле, данные входящего события или описанное получение" in prompt
    assert "Не утверждай, что значение недоступно" in prompt
    # Описанное получение в любом разделе снимает находку (ложная находка на described, 28909b7).
    assert "возвращается другой системой; тогда записи нет" in prompt
    assert "impact называй решением, которое нельзя принять по документу. Не утверждай" in prompt


def test_prompts_carry_no_case_or_pair_terms():
    text = SYSTEM_PROMPT_AUDITOR + build_pass1_atomic_prompt("") + build_pass2_set_prompt("", "")
    for term in ("тиын", "тенге", "четыре цифры", "последние", "литр", "кубометр", "адрес объекта", "E8", "E9"):
        assert term not in text, term


def _line(doc, needle):
    return next(dl for dl in doc.lines if needle in dl.raw_text)


def test_units_and_source_findings_on_shared_data_line_stay_apart():
    doc = parse_document(GAP)
    rule = _line(doc, "**В-3.**")
    step = _line(doc, "передать адрес объекта")
    data = _line(doc, "Хранить идентификатор счётчика")
    span = lambda dl: dict(line_start=dl.line_no, line_end=dl.line_no, quote=dl.raw_text.strip(" *"))
    reviewed = [r.report_id for r in run_structural_scan(doc).baseline_requirements]
    units = dict(related_req_ids=[], criterion="COMPLETE_SET", severity="MAJOR",
                 title="Не описано приведение единиц при сравнении расхода с лимитом",
                 problem="Правило сравнивает расход за месяц с лимитом, а данные задают расход в литрах и лимит в кубометрах.",
                 impact="Нельзя однозначно реализовать проверку достижения лимита.",
                 question_for_analyst="В каких единицах сравниваются расход за месяц и лимит и как приводятся величины?",
                 decision=None, recommendation="-", evidence=[span(rule), span(data)])
    source = dict(related_req_ids=[], criterion="COMPLETE_SET", severity="MAJOR",
                  title="Не описан источник адреса объекта для SMS",
                  problem="Шаг передаёт адрес объекта в SMS-шлюз, но среди хранимых и получаемых данных адрес не указан.",
                  impact="Нельзя определить, откуда сервис учёта берёт адрес.",
                  question_for_analyst="Откуда сервис учёта получает адрес объекта, который передаёт в SMS-шлюз?",
                  decision=None, recommendation="-", evidence=[span(step), span(data)])
    replies = [dict(requirements=[], atomic_defects=[]),
               dict(set_defects=[units, source], reviewed_req_ids=reviewed, additional_atomic_defects=[])]
    settings = LLMSettings(provider="gemini", api_key="test", primary_model="mock", max_retries=1)
    with patch.object(ResilientLLMClient, "_call_provider", side_effect=list(map(json.dumps, replies))):
        report = run_audit_pipeline(GAP, settings)
    titles = {d.title for d in report.set_defects}
    assert units["title"] in titles and source["title"] in titles
