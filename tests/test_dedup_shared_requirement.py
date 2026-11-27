"""Без пересечения строк одно место задаёт общее требование — только при той же теме."""
from reqguard.models import AtomicDefect, SetDefect, SourceSpan
from reqguard.verifier import _cross_deduplicate_atomic_vs_set, _deduplicate_set

SEC = "Склад → Коды ответа"


def _span(line, sec=SEC):
    return SourceSpan(line_start=line, line_end=line, quote="q", section_path=sec, verified=True)


def _sd(origin, reqs, title, problem, lines, criterion="CONSISTENT"):
    return SetDefect(defect_id="X", related_req_ids=reqs, criterion=criterion, severity="CRITICAL", title=title,
                     problem=problem, impact="i", question_for_analyst="q", decision=None, recommendation="r",
                     evidence=[_span(n) for n in lines], origin=origin)


SCANNER = _sd("STRUCTURAL", ["REQ-09"], "Отказ в отгрузке: статус 200 с ERROR против 400",
              "Пример 200 возвращает ERROR, а для неверных данных задан статус 400.", [30, 33])


def test_same_conflict_on_other_lines_of_shared_requirement_is_one_finding():
    model = _sd("LLM", ["REQ-04", "REQ-09"], "Код ответа при отказе в отгрузке",
                "Логика возвращает ERROR со статусом 200, а перечень кодов относит неверные данные к 400.", [12, 29])
    assert len(_deduplicate_set([SCANNER, model])) == 1


def test_other_topic_in_shared_requirement_stays_apart():
    other = _sd("LLM", ["REQ-09"], "Заголовок 403 описывает ответ 404",
                "Для чужого склада раздел запрета доступа возвращает 404.", [40, 41])
    assert len(_deduplicate_set([SCANNER, other])) == 2


def test_other_criterion_without_overlap_stays_apart():
    other = _sd("LLM", ["REQ-09"], "Неполный перечень кодов", "Не описан статус 400 и 200 с ERROR для повторов.",
                [45], criterion="COMPLETE_SET")
    assert len(_deduplicate_set([SCANNER, other])) == 2


def test_atomic_about_another_value_in_shared_requirement_is_kept():
    # Отдельная проблема того же требования: перечисленный код не описан. Не дубль конфликта 200/400.
    atom = AtomicDefect(defect_id="A", req_id="REQ-09", criterion="UNAMBIGUOUS", severity="MAJOR",
                        problem="Правило называет успешным статус 201, но в перечне кодов описан только 200.",
                        impact="i", recommendation="r", evidence=[_span(27)], origin="LLM")
    assert len(_cross_deduplicate_atomic_vs_set([atom], [SCANNER])) == 1
