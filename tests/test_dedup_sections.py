"""Раздел и его подраздел — одно место для дедупликации; разные ветви — разные."""
from reqguard.models import SetDefect, SourceSpan
from reqguard.verifier import _deduplicate_set

ROOT = "Склад → Коды ответа"


def _sd(origin, title, problem, spans):
    return SetDefect(defect_id="X", related_req_ids=[], criterion="CONSISTENT", severity="CRITICAL", title=title,
                     problem=problem, impact="i", question_for_analyst="q", decision=None, recommendation="r",
                     evidence=[SourceSpan(line_start=a, line_end=b, quote="q", section_path=s, verified=True) for a, b, s in spans],
                     origin=origin)


def test_parent_and_child_section_are_one_place():
    scanner = _sd("STRUCTURAL", "Ошибка отгрузки: статус 200 с ERROR против 400",
                  "Пример 200 возвращает ERROR, а для неверных данных задан 400.",
                  [(20, 20, ROOT + " → 200"), (25, 25, ROOT + " → 400")])
    model = _sd("LLM", "Противоречие кода ответа при отказе в отгрузке",
                "Для отказа в отгрузке описан статус 200 с ERROR в теле, а раздел 400 описывает другой формат ошибки.",
                [(18, 20, ROOT), (24, 25, ROOT + " → 400")])
    assert len(_deduplicate_set([scanner, model])) == 1


def test_different_branches_without_overlap_stay_apart():
    a = _sd("STRUCTURAL", "Ошибка отгрузки: статус 200 с ERROR против 400",
            "Пример 200 возвращает ERROR, а для неверных данных задан 400.", [(20, 20, ROOT + " → 200")])
    b = _sd("LLM", "Статус 403 описан как 404",
            "Для чужого склада раздел 403 возвращает 404.", [(40, 41, "Склад → Доступ → 403")])
    assert len(_deduplicate_set([a, b])) == 2
