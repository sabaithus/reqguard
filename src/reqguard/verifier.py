"""Детерминированный анти-галлюцинационный верификатор цитат, дедупликатор и скоринг готовности ТЗ."""

from __future__ import annotations

import re

from reqguard.models import (
    AtomicDefect,
    AuditReport,
    AuditSummary,
    CRITERION_LABELS_RU,
    RequirementItem,
    RunPassport,
    SetDefect,
    Severity,
    SourceSpan,
)
from reqguard.parser import ParsedDocument

SEVERITY_RANK: dict[Severity, int] = {"CRITICAL": 0, "MAJOR": 1, "MINOR": 2}
WORD_TOKEN_RE = re.compile(r"[a-zа-яё0-9]{3,}", re.IGNORECASE)


def _normalize_for_match(text: str) -> str:
    """Нормализует строку для устойчивого сопоставления без потери смысловых символов."""
    cleaned = text.replace("\xa0", " ").replace("ё", "е").replace("Ё", "Е")
    # Убираем маркеры форматирования Markdown, оставляя буквы, цифры и знаки
    cleaned = re.sub(r"[*_`#|•]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned


def _tokens(text: str) -> set[str]:
    return {t.lower().replace("ё", "е") for t in WORD_TOKEN_RE.findall(text)}


def verify_and_anchor_span(doc: ParsedDocument, candidate: SourceSpan) -> SourceSpan | None:
    """Проверяет, что цитата реально присутствует в исходном документе, и уточняет номера строк.

    Возвращает верифицированный SourceSpan с точной локацией в файле либо None, если цитата выдумана (галлюцинация).
    """
    raw_quote = (candidate.quote or "").strip()
    if not raw_quote:
        return None

    # Поддержка составных цитат с многоточием ("фрагмент 1 ... фрагмент 2")
    fragments = [
        _normalize_for_match(part)
        for part in re.split(r"\.{3,}|…", raw_quote)
        if len(_normalize_for_match(part)) >= 5
    ]
    if not fragments:
        norm_single = _normalize_for_match(raw_quote)
        if len(norm_single) < 5:
            return None
        fragments = [norm_single]

    def ordered_match(text: str) -> bool:
        offset = 0
        for fragment in fragments:
            pos = text.find(fragment, offset)
            if pos < 0:
                return False
            offset = pos + len(fragment)
        return True

    norm_full = " ".join(fragments)
    quote_tokens = _tokens(raw_quote)

    total = doc.total_lines
    if total == 0:
        return None

    norm_lines = [_normalize_for_match(dl.raw_text) for dl in doc.lines]

    def _check_window(start_1b: int, end_1b: int, allow_short_exact: bool = False) -> SourceSpan | None:
        s = max(1, start_1b)
        e = min(total, max(s, end_1b))
        combined_norm = " ".join(norm_lines[i - 1] for i in range(s, e + 1) if norm_lines[i - 1])
        if not combined_norm:
            return None
        if ordered_match(combined_norm):
            # Сужаем окно сверху и снизу до минимально необходимого диапазона строк
            while s < e:
                shrunk = " ".join(norm_lines[i - 1] for i in range(s + 1, e + 1) if norm_lines[i - 1])
                if ordered_match(shrunk):
                    s += 1
                else:
                    break
            while e > s:
                shrunk = " ".join(norm_lines[i - 1] for i in range(s, e) if norm_lines[i - 1])
                if ordered_match(shrunk):
                    e -= 1
                else:
                    break
            if allow_short_exact and len(norm_full) < 10:
                matched_norm = " ".join(norm_lines[i - 1] for i in range(s, e + 1) if norm_lines[i - 1])
                if matched_norm != norm_full:
                    return None
            first_dl = doc.lines[s - 1]
            authentic_quote = doc.get_span_text(s, e)
            return SourceSpan(
                line_start=s,
                line_end=e,
                page=first_dl.page,
                section_path=first_dl.section_path,
                quote=authentic_quote,
                verified=True,
            )
        return None

    # 1. Проверяем указанный диапазон строк (и соседние ±3 строки на случай смещения)
    hint_s = max(1, min(total, candidate.line_start))
    hint_e = max(hint_s, min(total, candidate.line_end))
    if len(norm_full) >= 5:
        exact_hit = _check_window(hint_s, hint_e, allow_short_exact=(len(norm_full) < 10))
        if exact_hit:
            return exact_hit

        near_hit = _check_window(
            max(1, hint_s - 3),
            min(total, hint_e + 3),
            allow_short_exact=(len(norm_full) < 10),
        )
        if near_hit:
            return near_hit

    # 2. Глобальный поиск по всему документу — строгий порог против коротких псевдо-цитат
    if len(norm_full) < 18 or len(quote_tokens) < 3:
        return None

    max_win = min(12, total)
    for start_ln in range(1, total + 1):
        if not norm_lines[start_ln - 1]:
            continue
        hit = _check_window(start_ln, min(total, start_ln + max_win - 1))
        if hit:
            return hit

    return None


def _spans_overlap(a: list[SourceSpan], b: list[SourceSpan]) -> bool:
    lines_a = {ln for sp in a for ln in range(sp.line_start, sp.line_end + 1)}
    lines_b = {ln for sp in b for ln in range(sp.line_start, sp.line_end + 1)}
    return bool(lines_a & lines_b)


def _jaccard(t1: set[str], t2: set[str]) -> float:
    if not t1 or not t2:
        return 0.0
    return len(t1 & t2) / len(t1 | t2)


def _deduplicate_atomic(defects: list[AtomicDefect]) -> list[AtomicDefect]:
    kept: list[AtomicDefect] = []
    for cand in defects:
        cand_tokens = _tokens(f"{cand.problem} {cand.recommendation}")
        merged = False
        for ex in kept:
            same_target = ex.req_id == cand.req_id or _spans_overlap(ex.evidence, cand.evidence)
            if not same_target:
                continue
            ex_tokens = _tokens(f"{ex.problem} {ex.recommendation}")
            same_crit_and_overlap = ex.criterion == cand.criterion and _spans_overlap(ex.evidence, cand.evidence)
            if same_crit_and_overlap or _jaccard(cand_tokens, ex_tokens) >= 0.42:
                # Оставляем более высокий приоритет и объединяем источники
                if SEVERITY_RANK[cand.severity] < SEVERITY_RANK[ex.severity]:
                    ex.severity = cand.severity
                if len(cand.problem) > len(ex.problem) and cand.origin == "LLM":
                    ex.problem = cand.problem
                    ex.impact = cand.impact
                    ex.recommendation = cand.recommendation
                    ex.suggested_fix = cand.suggested_fix or ex.suggested_fix
                if ex.origin != cand.origin:
                    ex.origin = "HYBRID"
                merged = True
                break
        if not merged:
            kept.append(cand)
    return kept


def _deduplicate_set(defects: list[SetDefect]) -> list[SetDefect]:
    kept: list[SetDefect] = []
    # Сначала пропускаем STRUCTURAL, затем LLM, чтобы составной LLM-дефект корректно сливался с точечными структурными
    ordered = sorted(defects, key=lambda d: 0 if d.origin == "STRUCTURAL" else 1)
    for cand in ordered:
        cand_tokens = _tokens(f"{cand.title} {cand.problem} {cand.recommendation}")
        cand_lines = {ln for sp in cand.evidence for ln in range(sp.line_start, sp.line_end + 1)}

        # Проверка покрытия составного LLM-дефекта несколькими уже существующими STRUCTURAL-дефектами
        if cand.origin == "LLM" and cand_lines:
            cand_title_low = cand.title.lower()
            cand_is_missing_read = "чтен" in cand_title_low and not ("мутир" in cand_title_low)
            covering_structural: list[SetDefect] = []
            covered_lines_union: set[int] = set()
            for ex in kept:
                if ex.origin not in ("STRUCTURAL", "HYBRID"):
                    continue
                ex_title_low = ex.title.lower()
                if ("http get" in ex_title_low or "мутирующ" in ex_title_low) and cand_is_missing_read:
                    continue
                ex_lines = {ln for sp in ex.evidence for ln in range(sp.line_start, sp.line_end + 1)}
                overlap_lines = cand_lines & ex_lines
                if not overlap_lines:
                    continue
                ex_tokens = _tokens(f"{ex.title} {ex.problem} {ex.recommendation}")
                if len(cand_tokens & ex_tokens) >= 2:
                    covering_structural.append(ex)
                    covered_lines_union |= overlap_lines
            if len(covering_structural) >= 1 and (len(covered_lines_union) / len(cand_lines)) >= 0.75:
                for ex in covering_structural:
                    ex.origin = "HYBRID"
                    ex.related_req_ids = sorted(set(ex.related_req_ids) | set(cand.related_req_ids))
                continue

        merged = False
        for ex in kept:
            ex_title_low = ex.title.lower()
            cand_title_low = cand.title.lower()
            # Не сливаем дефект про мутирующий HTTP GET с дефектом про отсутствие отдельного GET-метода чтения
            ex_is_mutating_get = "http get" in ex_title_low or "мутирующ" in ex_title_low
            cand_is_missing_read = "чтен" in cand_title_low and not ("мутир" in cand_title_low)
            if ex_is_mutating_get and cand_is_missing_read:
                continue

            ex_tokens = _tokens(f"{ex.title} {ex.problem} {ex.recommendation}")
            overlap = _spans_overlap(ex.evidence, cand.evidence)
            sim = _jaccard(cand_tokens, ex_tokens)

            ex_secs = {sp.section_path for sp in ex.evidence if sp.section_path}
            cand_secs = {sp.section_path for sp in cand.evidence if sp.section_path}
            if ex_secs and cand_secs and not (ex_secs <= cand_secs or cand_secs <= ex_secs) and sim < 0.55:
                continue

            # Если оба дефекта ссылаются на одни и те же строки и описывают одну проблему
            if (
                (overlap and sim >= 0.30)
                or (overlap and ex.criterion == cand.criterion and sim >= 0.26)
                or (overlap and _same_topic_pair(cand_tokens, ex_tokens))
                or sim >= 0.50
            ):
                if SEVERITY_RANK[cand.severity] < SEVERITY_RANK[ex.severity]:
                    ex.severity = cand.severity
                if ex.origin != cand.origin:
                    ex.origin = "HYBRID"
                existing_line_pairs = {(sp.line_start, sp.line_end) for sp in ex.evidence}
                for sp in cand.evidence:
                    if (sp.line_start, sp.line_end) not in existing_line_pairs:
                        ex.evidence.append(sp)
                        existing_line_pairs.add((sp.line_start, sp.line_end))
                ex.related_req_ids = sorted(set(ex.related_req_ids) | set(cand.related_req_ids))
                if len(cand.problem) > len(ex.problem) and cand.origin == "LLM":
                    ex.title = cand.title
                    ex.problem = cand.problem
                    ex.impact = cand.impact
                    ex.question_for_analyst = cand.question_for_analyst
                    ex.recommendation = cand.recommendation
                merged = True
                break
        if not merged:
            kept.append(cand)
    return kept


_TOPIC_SIGNATURES: tuple[tuple[str, ...], ...] = (
    ("get", "мутир", "savelimit", "post", "put"),
    ("200", "error", "400", "статус"),
    ("возврат", "минус", "отрицательн", "прошл"),
    ("момент", "покупк", "клиринг", "авторизац", "проведен"),
    ("выключ", "включ", "переключател", "тумблер", "enabled"),
)


def _same_topic_pair(t1: set[str], t2: set[str]) -> bool:
    for group in _TOPIC_SIGNATURES:
        hits_1 = sum(1 for kw in group if any(kw in tok for tok in t1))
        hits_2 = sum(1 for kw in group if any(kw in tok for tok in t2))
        if hits_1 >= 2 and hits_2 >= 2:
            return True
    return False


def _cross_deduplicate_atomic_vs_set(
    atomic_list: list[AtomicDefect],
    set_list: list[SetDefect],
) -> list[AtomicDefect]:
    """Удаляет атомарные дефекты, которые полностью дублируют уже выявленный системный дефект набора."""
    kept: list[AtomicDefect] = []
    for ad in atomic_list:
        if ad.criterion == "SINGULAR":
            kept.append(ad)
            continue
        ad_tokens = _tokens(f"{ad.problem} {ad.recommendation}")
        is_dup = False
        for sd in set_list:
            if not _spans_overlap(ad.evidence, sd.evidence):
                continue
            sd_tokens = _tokens(f"{sd.title} {sd.problem} {sd.recommendation}")
            if _jaccard(ad_tokens, sd_tokens) >= 0.20 or _same_topic_pair(ad_tokens, sd_tokens):
                if ad.req_id and ad.req_id not in sd.related_req_ids:
                    sd.related_req_ids = sorted(set(sd.related_req_ids) | {ad.req_id})
                if sd.origin != ad.origin:
                    sd.origin = "HYBRID"
                if SEVERITY_RANK[ad.severity] < SEVERITY_RANK[sd.severity]:
                    sd.severity = ad.severity
                is_dup = True
                break
        if not is_dup:
            kept.append(ad)
    return kept


def build_verified_audit_report(
    doc: ParsedDocument,
    passport: RunPassport,
    requirements: list[RequirementItem],
    candidate_atomic: list[AtomicDefect],
    candidate_set: list[SetDefect],
    initial_filtered_count: int = 0,
) -> AuditReport:
    """Прогоняет все замечания через верификатор цитат, удаляет дубликаты и формирует сводку."""
    filtered_hallucinations = initial_filtered_count
    verified_quotes = sum(1 for r in requirements if r.source.verified)

    req_sources = {r.report_id: r.source for r in requirements}
    verified_atomic: list[AtomicDefect] = []
    for ad in candidate_atomic:
        valid_spans: list[SourceSpan] = []
        for sp in ad.evidence:
            v_sp = sp if sp.verified else verify_and_anchor_span(doc, sp)
            if v_sp is not None:
                valid_spans.append(v_sp)
                verified_quotes += 1
        target = req_sources.get(ad.req_id)
        if not valid_spans or target is None or not _spans_overlap([target], valid_spans):
            filtered_hallucinations += 1
            continue
        ad.evidence = valid_spans
        verified_atomic.append(ad)

    verified_set: list[SetDefect] = []
    for sd in candidate_set:
        valid_spans = []
        seen_locs: set[tuple[int, int]] = set()
        for sp in sd.evidence:
            v_sp = sp if sp.verified else verify_and_anchor_span(doc, sp)
            if v_sp is not None:
                loc = (v_sp.line_start, v_sp.line_end)
                if loc not in seen_locs:
                    valid_spans.append(v_sp)
                    seen_locs.add(loc)
                    verified_quotes += 1
        rejected = len(sd.evidence) - len(seen_locs)
        incomplete = sd.origin == "LLM" and (
            rejected > 0 or (sd.criterion in ("CONSISTENT", "VERIFIABLE_SET") and len(valid_spans) < 2)
        )
        bundled = sd.origin == "LLM" and len(re.findall(r"(?:^|\s)\d+\)\s", sd.problem)) > 1
        if not valid_spans or incomplete or bundled:
            filtered_hallucinations += 1
            continue
        sd.evidence = valid_spans
        verified_set.append(sd)

    # Дедупликация: сначала Set, затем кросс-дедупликация Atomic ↔ Set и сортировка по критичности
    dedup_set = _deduplicate_set(verified_set)
    dedup_atomic = _cross_deduplicate_atomic_vs_set(
        _deduplicate_atomic(verified_atomic),
        dedup_set,
    )

    dedup_atomic.sort(key=lambda d: (SEVERITY_RANK[d.severity], d.evidence[0].line_start))
    for i, ad in enumerate(dedup_atomic, start=1):
        ad.defect_id = f"DEF-A{i:02d}"

    dedup_set.sort(key=lambda d: (SEVERITY_RANK[d.severity], d.evidence[0].line_start))
    for i, sd in enumerate(dedup_set, start=1):
        sd.defect_id = f"DEF-S{i:02d}"

    passport.verified_quotes_count = verified_quotes
    passport.hallucinations_filtered = filtered_hallucinations

    # Подсчёт метрик и вердикта
    all_severities = [d.severity for d in dedup_atomic] + [d.severity for d in dedup_set]
    crit_n = sum(1 for s in all_severities if s == "CRITICAL")
    maj_n = sum(1 for s in all_severities if s == "MAJOR")
    min_n = sum(1 for s in all_severities if s == "MINOR")

    by_crit: dict[str, int] = {}
    for d in dedup_atomic:
        label = CRITERION_LABELS_RU.get(d.criterion, d.criterion)
        by_crit[label] = by_crit.get(label, 0) + 1
    for d in dedup_set:
        label = CRITERION_LABELS_RU.get(d.criterion, d.criterion)
        by_crit[label] = by_crit.get(label, 0) + 1

    reqs_with_issues: set[str] = {d.req_id for d in dedup_atomic}
    for sd in dedup_set:
        reqs_with_issues.update(sd.related_req_ids)

    total_defects = len(dedup_atomic) + len(dedup_set)
    if total_defects == 0:
        readiness_score = 100
    else:
        penalty = crit_n * 18 + maj_n * 7 + min_n * 2
        readiness_score = max(5, round(100 * 60 / (60 + penalty)))

    if crit_n > 0:
        can_dev = False
        can_qa = False
        verdict = "НЕ ГОТОВО К РАЗРАБОТКЕ И ТЕСТИРОВАНИЮ (ЕСТЬ КРИТИЧНЫЕ БЛОКЕРЫ)"
        blockers = [d.defect_id for d in [*dedup_set, *dedup_atomic] if d.severity == "CRITICAL"]
        rationale = "До начала работ согласуйте блокирующие замечания: " + ", ".join(blockers) + "."
    elif maj_n >= 3:
        can_dev = False
        can_qa = False
        verdict = "УСЛОВНО ГОТОВО (ТРЕБУЕТСЯ УТОЧНЕНИЕ СУЩЕСТВЕННЫХ ЗАМЕЧАНИЙ ДО СТАРТА)"
        rationale = (
            f"Прямых критичных блокеров нет, однако выявлено {maj_n} существенных замечаний в требованиях "
            "и контрактах. Рекомендуется закрыть вопросы аналитику до фиксации спринта."
        )
    else:
        can_dev = True
        can_qa = True
        verdict = "ГОТОВО К РАЗРАБОТКЕ И ТЕСТИРОВАНИЮ"
        rationale = (
            "Критичных противоречий и блокирующих пропусков не выявлено; незначительные замечания могут быть "
            "уточнены в рабочем порядке."
        )

    if filtered_hallucinations and not crit_n and passport.analysis_status == "complete":
        passport.analysis_status = "unverified_findings"
    if not requirements or passport.analysis_status in ("unverified_findings", "structural_only", "phase2_failed", "coverage_incomplete", "insufficient_input"):
        reasons = {
            "unverified_findings": "Отброшены неподтверждённые утверждения модели. Требуется повторная проверка.",
            "structural_only": "Выполнена только статическая проверка.",
            "phase2_failed": "Модель не завершила проверку всего набора.",
            "coverage_incomplete": "Индивидуальная оценка модели охватила только часть выделенных требований.",
        }
        reason = reasons.get(passport.analysis_status, "Проверяемые требования не выделены.")
        if not requirements:
            reason = "Проверяемые требования не выделены."
        if not crit_n or not requirements:
            readiness_score = None
            can_dev = can_qa = None
            verdict = "ГОТОВНОСТЬ НЕ УСТАНОВЛЕНА"
            rationale = reason
        else:
            verdict = "ОБНАРУЖЕНЫ БЛОКЕРЫ. ПРОВЕРКА НЕПОЛНАЯ"
            rationale = reason + " " + rationale

    priority_fixes: list[str] = []
    for sd in dedup_set:
        if sd.severity == "CRITICAL":
            priority_fixes.append(f"[{sd.defect_id}] {sd.title}: {sd.recommendation}")
    for ad in dedup_atomic:
        if ad.severity == "CRITICAL":
            tag = f"{ad.req_id}" + (f" ({ad.doc_id})" if ad.doc_id else "")
            priority_fixes.append(f"[{ad.defect_id} / {tag}] {ad.recommendation}")
    if len(priority_fixes) < 5:
        for sd in dedup_set:
            if sd.severity == "MAJOR" and len(priority_fixes) < 7:
                priority_fixes.append(f"[{sd.defect_id}] {sd.title}: {sd.recommendation}")
        for ad in dedup_atomic:
            if ad.severity == "MAJOR" and len(priority_fixes) < 7:
                tag = f"{ad.req_id}" + (f" ({ad.doc_id})" if ad.doc_id else "")
                priority_fixes.append(f"[{ad.defect_id} / {tag}] {ad.recommendation}")

    summary = AuditSummary(
        total_requirements=len(requirements),
        requirements_with_defects=len(reqs_with_issues),
        total_atomic_defects=len(dedup_atomic),
        total_set_defects=len(dedup_set),
        total_defects=len(dedup_atomic) + len(dedup_set),
        critical_count=crit_n,
        major_count=maj_n,
        minor_count=min_n,
        by_criterion=by_crit,
        readiness_score=readiness_score,
        verdict=verdict,
        can_start_development=can_dev,
        can_start_testing=can_qa,
        verdict_rationale=rationale,
        priority_fixes=priority_fixes,
    )

    return AuditReport(
        passport=passport,
        requirements=requirements,
        atomic_defects=dedup_atomic,
        set_defects=dedup_set,
        summary=summary,
    )
