"""Утверждение о пропуске в документе и утверждение о описанном содержимом — разные находки.

Независимый пример: клиника, интервал напоминания. Общая строка контракта и общая тема HTTP-метода
не делают находку «не описано получение значения» дублем находки «описанный метод неверен».
"""
from reqguard.models import SetDefect, SourceSpan
from reqguard.verifier import _deduplicate_set

API = "Клиника → API напоминаний"


def _span(line, sec=API):
    return SourceSpan(line_start=line, line_end=line, quote="q", section_path=sec, verified=True)


def _sd(origin, criterion, title, problem, lines, reqs=("REQ-03",)):
    return SetDefect(defect_id="X", related_req_ids=list(reqs), criterion=criterion, severity="MAJOR", title=title,
                     problem=problem, impact="i", question_for_analyst="q", decision=None, recommendation="r",
                     evidence=[_span(n) for n in lines], origin=origin)


def _scanner_method():
    return _sd("STRUCTURAL", "INTERFACE_INTEGRITY",
               "Операция изменения интервала объявлена как POST, а пример вызывает PUT",
               "Тип метода POST, а пример запроса отправляет PUT для изменения интервала напоминания.", [12, 14])


def test_read_gap_sharing_a_contract_line_stays_apart_from_method_finding():
    gap = _sd("LLM", "COMPLETE_SET",
              "В переданной спецификации не описано получение текущего интервала напоминания",
              "Экран показывает текущий интервал (строка 4), а описанный контракт POST/PUT (строка 12) только "
              "заменяет интервал (строка 19).", [4, 12, 19])
    kept = _deduplicate_set([_scanner_method(), gap])
    assert len(kept) == 2
    assert {d.criterion for d in kept} == {"INTERFACE_INTEGRITY", "COMPLETE_SET"}


def test_read_gap_mostly_on_scanner_lines_is_not_absorbed():
    gap = _sd("LLM", "COMPLETE_SET",
              "Не описано получение текущего интервала напоминания",
              "Контракт POST/PUT только заменяет интервал; поведение, возвращающее значение, не описано.", [12, 14])
    assert len(_deduplicate_set([_scanner_method(), gap])) == 2


def test_model_restating_the_method_conflict_still_merges():
    same = _sd("LLM", "INTERFACE_INTEGRITY",
               "Несогласованный HTTP-метод изменения интервала",
               "Метод указан как POST, но пример использует PUT.", [12, 14])
    assert len(_deduplicate_set([_scanner_method(), same])) == 1


def test_two_omission_claims_of_one_gap_still_merge():
    scanner_gap = _sd("STRUCTURAL", "COMPLETE_SET", "Не описано получение интервала напоминания",
                      "Контракт не описывает получение текущего интервала.", [12])
    model_gap = _sd("LLM", "COMPLETE_SET", "В спецификации не описано получение текущего интервала напоминания",
                    "Описанный контракт только заменяет интервал, получение текущего интервала не описано.", [12, 19])
    assert len(_deduplicate_set([scanner_gap, model_gap])) == 1
