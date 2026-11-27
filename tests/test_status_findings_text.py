"""Находки сканера о статусах строятся из текста документа и не подсказывают ответ.

Независимый пример: API склада. Ни сумм, ни карт, ни кода 422 в документе нет.
"""
from reqguard.parser import parse_document
from reqguard.structural_scanner import run_structural_scan

SPEC = """# Резерв ячейки

### Статусы
#### 200. Successful
```json
{"result":"ERROR","message":"Ячейка занята"}
```
#### 400. Unsuccessful — Bad request
```json
{"message":"Неверный номер ячейки"}
```
#### 403. Unsuccessful — Forbidden
Для ячейки другого склада возвращается 404.
"""

CASE_TERMS = ("422", "₸", "1 000", "сумм", "карт", "Created", "201", "IDOR", "сесси", "рол")


def _findings(tmp_path):
    p = tmp_path / "spec.md"
    p.write_text(SPEC, encoding="utf-8")
    doc = parse_document(p)
    return doc, run_structural_scan(doc).set_defects


def _text(d):
    return " ".join([d.title, d.problem, d.impact, d.question_for_analyst, d.recommendation])


def test_error_body_under_200_quotes_the_document_and_imposes_no_status(tmp_path):
    doc, found = _findings(tmp_path)
    d = next(x for x in found if "200" in x.title and "400" in x.title)
    for term in CASE_TERMS:
        assert term not in _text(d), term
    assert "Ячейка занята" in d.problem and "Неверный номер ячейки" in d.problem
    assert "400" not in d.question_for_analyst and "200" not in d.recommendation


def test_403_described_as_404_quotes_the_document_and_imposes_no_answer(tmp_path):
    doc, found = _findings(tmp_path)
    d = next(x for x in found if "403" in x.title)
    for term in CASE_TERMS:
        assert term not in _text(d), term
    assert "Для ячейки другого склада возвращается 404." in d.problem
    assert "например" not in d.question_for_analyst
