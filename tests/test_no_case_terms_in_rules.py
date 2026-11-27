"""Правила сканера и дедупликации не содержат имён из учебного кейса."""
from pathlib import Path

from reqguard.structural_scanner import MUTATING_KEYWORDS_RE
from reqguard.verifier import _same_topic_pair, _tokens

SRC = Path(__file__).resolve().parents[1] / "src" / "reqguard"


def test_rule_sources_carry_no_case_method_names():
    for name in ("structural_scanner.py", "verifier.py"):
        text = (SRC / name).read_text(encoding="utf-8").lower()
        for term in ("savelimit", "setlimit"):
            assert term not in text, (name, term)


def test_mutating_names_are_recognised_generically():
    for name in ("/alerts/saveLimit", "/stock/setThreshold", "updateSlot", "Изменение интервала"):
        assert MUTATING_KEYWORDS_RE.search(name), name
    for name in ("/user/settings", "/stock/reset", "/report/offset"):
        assert not MUTATING_KEYWORDS_RE.search(name), name


def test_method_topic_pairs_without_case_names():
    a = _tokens("Изменение ячейки через GET-запрос /stock/saveSlot")
    b = _tokens("Мутирующая операция выполняется методом GET, нужен POST")
    assert _same_topic_pair(a, b)
