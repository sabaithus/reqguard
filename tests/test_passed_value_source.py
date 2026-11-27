"""E9 (детерминированно): поле, которое шаг последовательности передаёт другой системе, должно иметь описанный источник."""
from pathlib import Path

import pytest

from reqguard.parser import parse_document
from reqguard.structural_scanner import run_structural_scan

ROOT = Path(__file__).resolve().parents[1]
MARK = "не описан источник значения"


def _source_findings(path):
    doc = parse_document(path)
    return doc, [d for d in run_structural_scan(doc).set_defects if MARK in d.title]


def test_unsourced_field_of_a_passed_message_is_reported():
    doc, found = _source_findings(ROOT / "eval" / "e8e9" / "water_gap.md")
    assert [d.title for d in found] == ["В переданной спецификации не описан источник значения «адрес объекта»"]
    d = found[0]
    assert d.criterion == "COMPLETE_SET" and d.origin == "STRUCTURAL"
    step = next(dl for dl in doc.lines if "передать адрес объекта" in dl.raw_text)
    data = next(dl for dl in doc.lines if "Хранить идентификатор счётчика" in dl.raw_text)
    assert {e.line_start for e in d.evidence} == {step.line_no, data.line_no}
    assert "адрес объекта" in d.question_for_analyst
    for text in (d.problem, d.impact, d.question_for_analyst):
        assert "недоступ" not in text and "не сможет" not in text


def test_field_returned_by_described_integration_is_not_reported():
    _, found = _source_findings(ROOT / "eval" / "e8e9" / "water_described.md")
    assert found == []


@pytest.mark.parametrize("name", ["eval/e6/reminder_no_read.md", "eval/e6/reminder_with_read.md", "docs/requirements.md"])
def test_other_documents(name):
    _, found = _source_findings(ROOT / name)
    if name == "docs/requirements.md":
        assert [d.title for d in found] == ["В переданной спецификации не описан источник значения «последние четыре цифры карты»"]
    else:
        assert found == []


def _doc(tmp_path, sequence, data="Хранить идентификатор заказа и сумму заказа."):
    p = tmp_path / "spec.md"
    p.write_text("# Доставка\n\n### Sequence Diagram\n" + sequence + "\n\n### Требования к данным\n" + data + "\n", encoding="utf-8")
    return p


def test_sending_an_action_is_not_a_passed_field(tmp_path):
    _, found = _source_findings(_doc(tmp_path, "Сервис заказов → SMS-шлюз: отправить уведомление клиенту."))
    assert found == []


def test_field_carried_by_an_incoming_event_has_a_source(tmp_path):
    seq = "Касса → сервис заказов: оплачен заказ с кодом магазина. Сервис заказов → склад: передать код магазина и сумму заказа."
    _, found = _source_findings(_doc(tmp_path, seq))
    assert found == []


def test_field_absent_everywhere_is_reported_in_a_minimal_document(tmp_path):
    _, found = _source_findings(_doc(tmp_path, "Сервис заказов → склад: передать номер ячейки и сумму заказа."))
    assert [d.title for d in found] == ["В переданной спецификации не описан источник значения «номер ячейки»"]


@pytest.mark.parametrize(("section", "table", "missing"), [
    ("Требования к данным", "| Поле | Тип |\n|---|---|\n| Номер ячейки | string |", False),
    ("Требования к интеграции", "| Сервис | Вход | Выход |\n|---|---|---|\n| Склад | ID | Номер ячейки |", False),
    ("Требования к интеграции", "| Сервис | Вход | Выход |\n|---|---|---|\n| Склад | Номер ячейки | ID |", True),
    ("Ответ API", "| Parameter | Описание |\n|---|---|\n| cell | Номер ячейки |", False),
    ("Требования к данным", "| Поле | Источник |\n|---|---|\n| Номер ячейки | Не определён |", True),
])
def test_source_table_direction(tmp_path, section, table, missing):
    path = _doc(tmp_path, "Сервис заказов → склад: передать номер ячейки.")
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"\n### {section}\n{table}\n")
    _, findings = _source_findings(path)
    assert bool(findings) == missing
