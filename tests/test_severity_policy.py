"""Предложенное исправление не вводит чисел, которых нет в источнике (правило 5 системного промпта)."""
from reqguard.models import AtomicDefect, SourceSpan
from reqguard.verifier import _document_numbers, _drop_invented_fix


def _atomic(fix=None):
    return AtomicDefect(defect_id="A", req_id="REQ-01", criterion="UNAMBIGUOUS", severity="MAJOR", problem="p",
                        impact="i", recommendation="r", suggested_fix=fix,
                        evidence=[SourceSpan(line_start=5, line_end=5, quote="q", verified=True)], origin="LLM")


NUMBERS = _document_numbers(["Склад отвечает в течение 5 минут.", "Резерв от 1 000 до 20 000 ячеек."])


def test_fix_with_a_number_absent_from_the_document_is_dropped():
    d = _atomic("Время ответа склада не должно превышать 2000 мс для 95% запросов.")
    _drop_invented_fix(d, NUMBERS)
    assert d.suggested_fix is None


def test_fix_using_document_numbers_and_requirement_ids_is_kept():
    for fix in ("С-1.1: Резерв от 1 000 до 20 000 ячеек. С-1.2: Ответ за 5 минут.", "Уточнить у владельца порог резерва."):
        d = _atomic(fix)
        _drop_invented_fix(d, NUMBERS)
        assert d.suggested_fix == fix
