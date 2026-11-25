"""Генератор официального отчёта о проверке требований на русском языке (Markdown).

Строго реализует все 6 обязательных разделов из п. 6 ТЗ хакатона:
1. Паспорт прогона
2. Как агент нарезал требования (таблица ID в отчёте — ID в документе — формулировка/цитата)
3. Проверка каждого требования (ISO/IEC/IEEE 29148:2018 п. 5.2.5)
4. Проверка всего набора требований (ISO/IEC/IEEE 29148:2018 п. 5.2.6, IEEE Std 830-1998 п. 4.3)
5. Сводка (количественные метрики и реестр критичных дефектов)
6. Итог (готовность к разработке и тестированию + первоочередной план исправлений)
"""

from __future__ import annotations

import re

from reqguard.models import (
    AuditReport,
    CATEGORY_LABELS_RU,
    CRITERION_LABELS_RU,
    SEVERITY_LABELS_RU,
)

ORIGIN_LABELS_RU = {
    "LLM": "LLM-анализатор (цитата верифицирована)",
    "STRUCTURAL": "Детерминированный анализатор AST/API",
    "HYBRID": "Подтверждено дважды (AST-анализатор + LLM)",
}


def _esc_md_cell(text: str) -> str:
    return " ".join(text.replace("|", "\\|").splitlines()).strip()


def _clean_quote_preview(text: str) -> str:
    cleaned = re.sub(r"^\s*[-*+•]\s+", "", text.strip())
    cleaned = re.sub(r"\*{1,2}|__", "", cleaned)
    return cleaned.strip()


def render_markdown_report(report: AuditReport) -> str:
    p = report.passport
    s = report.summary
    out: list[str] = []

    # Заголовок
    out.append(f"# Отчёт о статической проверке качества требований: «{p.document_title}»")
    out.append("")
    out.append(
        "> Сформировано автоматически агентом **ReqGuard** по критериям **ISO/IEC/IEEE 29148:2018** (пп. 5.2.5, 5.2.6) "
        "и **IEEE Std 830-1998** (п. 4.3). Каждое замечание прошло детерминированную проверку подлинности цитат по исходному тексту документа."
    )
    out.append("")

    # 1. Паспорт прогона
    out.append("## 1. Паспорт прогона")
    out.append("")
    out.append("| Параметр | Значение |")
    out.append("| :--- | :--- |")
    out.append(f"| **Проверяемый документ** | `{_esc_md_cell(p.document_title)}` (`{_esc_md_cell(p.source_file)}`) |")
    out.append(f"| **Полнота анализа** | {p.status_label} |")
    pages_info = f", страниц: {p.total_pages}" if p.total_pages else ""
    out.append(f"| **Формат и объём** | {p.source_format} (строк: {p.total_lines}{pages_info}, SHA-256: `{p.source_sha256}`) |")
    out.append(f"| **Дата и время прогона** | {p.run_timestamp} |")
    out.append(f"| **Провайдер и модель LLM** | `{p.provider}` / `{p.model_name}` (ответов с текстом: {p.llm_calls_count}) |")
    out.append(f"| **Версия агента** | `{p.agent_version}` |")
    out.append(f"| **Длительность анализа** | {p.duration_sec:.2f} сек |")
    out.append(
        f"| **Контроль анти-галлюцинаций** | Привязок к источнику до объединения: **{p.verified_quotes_count}**, отклонено записей: **{p.hallucinations_filtered}** |"
    )
    out.append(f"| **Индекс готовности спецификации** | **{s.score_label}** — **{s.verdict}** |")
    out.append("")

    # 2. Как агент нарезал требования
    out.append("## 2. Нарезка и трассировка требований")
    out.append("")
    out.append(
        "Ниже приведён полный реестр выделенных единиц требований. Если в исходном документе у требования "
        "отсутствовал собственный идентификатор, агент присвоил сквозной внутренний номер и сохранил дословную цитату."
    )
    out.append("")
    out.append("| ID в отчёте | ID в документе | Категория | Локация | Краткая формулировка и цитата из источника |")
    out.append("| :--- | :--- | :--- | :--- | :--- |")
    for req in report.requirements:
        doc_id_cell = f"**{req.doc_id}**" if req.doc_id else "— *(присвоен агентом)*"
        cat_cell = CATEGORY_LABELS_RU.get(req.category, req.category)
        loc_cell = f"Строка {req.source.line_start}" if req.source.line_start == req.source.line_end else f"Строки {req.source.line_start}–{req.source.line_end}"
        if req.source.page:
            loc_cell = f"Стр. {req.source.page}, {loc_cell.lower()}"
        quote_short = _clean_quote_preview(req.source.quote)
        if len(quote_short) > 190:
            quote_short = quote_short[:187] + "..."
        compound_badge = " *(составное)*" if req.is_compound else ""
        desc_cell = f"{_esc_md_cell(req.title)}{compound_badge}<br>Цитата: «{_esc_md_cell(quote_short)}»"
        out.append(f"| `{req.report_id}` | {doc_id_cell} | {cat_cell} | {loc_cell} | {desc_cell} |")
    out.append("")

    # 3. Проверка каждого требования
    out.append("## 3. Проверка каждого требования (ISO/IEC/IEEE 29148:2018, п. 5.2.5)")
    out.append("")
    defects_by_req: dict[str, list] = {}
    for ad in report.atomic_defects:
        defects_by_req.setdefault(ad.req_id, []).append(ad)

    out.append("### 3.1. Матрица статуса индивидуальных требований")
    out.append("")
    out.append("| ID в отчёте | ID в документе | Статус индивидуальной проверки | Выявленные дефекты формулировки |")
    out.append("| :--- | :--- | :--- | :--- |")
    for req in report.requirements:
        r_defs = defects_by_req.get(req.report_id, [])
        doc_label = req.doc_id or "—"
        if not r_defs:
            status = "В отчёте нет атомарных замечаний" if req.model_reviewed else "Индивидуальная оценка модели не подтверждена"
            out.append(f"| `{req.report_id}` | {doc_label} | {status} | — |")
        else:
            max_sev = r_defs[0].severity
            sev_ru = SEVERITY_LABELS_RU[max_sev]
            codes = ", ".join(f"`{d.defect_id}` ({d.criterion})" for d in r_defs)
            out.append(f"| `{req.report_id}` | {doc_label} | **{sev_ru}** ({len(r_defs)}) | {codes} |")
    out.append("")

    out.append("### 3.2. Детальный разбор замечаний к отдельным требованиям")
    out.append("")
    if not report.atomic_defects:
        out.append("Дефектов индивидуальных формулировок не обнаружено.")
        out.append("")
    else:
        for ad in report.atomic_defects:
            tag = f"{ad.req_id}" + (f" / {ad.doc_id}" if ad.doc_id else "")
            sev_ru = SEVERITY_LABELS_RU[ad.severity]
            crit_ru = CRITERION_LABELS_RU.get(ad.criterion, ad.criterion)
            out.append(f"#### [{ad.defect_id}] Требование `{tag}` — {sev_ru}")
            out.append(f"- **Нарушенный критерий:** {crit_ru}")
            out.append(f"- **Источник обнаружения:** {ORIGIN_LABELS_RU.get(ad.origin, ad.origin)}")
            for sp in ad.evidence:
                out.append(f"- **Место в документе:** {sp.format_locator()}")
                out.append(f"  > «{sp.quote}»")
            out.append(f"- **Суть дефекта:** {ad.problem}")
            out.append(f"- **Риск при разработке / тестировании:** {ad.impact}")
            out.append(f"- **Что исправить:** {ad.recommendation}")
            if ad.suggested_fix:
                out.append(f"- **Предлагаемая формулировка / вопрос:** {ad.suggested_fix}")
            out.append("")

    # 4. Проверка всего набора требований
    out.append("## 4. Проверка всего набора требований (ISO/IEC/IEEE 29148 п. 5.2.6 и IEEE Std 830 п. 4.3)")
    out.append("")
    out.append(
        "В данном разделе приведены межсекционные противоречия, расхождения между бизнес-правилами и критериями приёмки, "
        "ошибки контрактов интеграции (Часть A ↔ Часть B REST API ↔ Модель данных ↔ UI) и пропущенные сценарии."
    )
    out.append("")
    if not report.set_defects:
        out.append("Межтребовательных противоречий и системных пропусков не обнаружено.")
        out.append("")
    else:
        for sd in report.set_defects:
            sev_ru = SEVERITY_LABELS_RU[sd.severity]
            crit_ru = CRITERION_LABELS_RU.get(sd.criterion, sd.criterion)
            rel_str = ", ".join(f"`{r}`" for r in sd.related_req_ids) if sd.related_req_ids else "Документ в целом"
            out.append(f"### [{sd.defect_id}] {sd.title} — {sev_ru}")
            out.append(f"- **Критерий качества набора:** {crit_ru}")
            out.append(f"- **Затронутые требования:** {rel_str}")
            out.append(f"- **Источник обнаружения:** {ORIGIN_LABELS_RU.get(sd.origin, sd.origin)}")
            out.append("- **Привязка к тексту документа (дословные цитаты):**")
            for idx, sp in enumerate(sd.evidence, start=1):
                out.append(f"  {idx}. **{sp.format_locator()}:**")
                for q_line in sp.quote.splitlines():
                    out.append(f"     > {q_line}")
            out.append(f"- **Описание проблемы / противоречия:** {sd.problem}")
            out.append(f"- **Последствия для бизнеса, разработки и QA:** {sd.impact}")
            out.append(f"- **Вопрос системному аналитику / Product Owner:** *{sd.question_for_analyst}*")
            out.append(f"- **Рекомендация по устранению:** {sd.recommendation}")
            out.append("")

    # 5. Сводка
    out.append("## 5. Сводка результатов проверки")
    out.append("")
    out.append("| Показатель | Значение |")
    out.append("| :--- | :--- |")
    out.append(f"| Всего выделено требований | **{s.total_requirements}** |")
    out.append(f"| Требований, затронутых замечаниями | **{s.requirements_with_defects}** |")
    out.append(f"| Всего выявлено нарушений | **{s.total_defects}** (индивидуальных: {s.total_atomic_defects}, системных по набору: {s.total_set_defects}) |")
    out.append(f"| Критичных дефектов (`CRITICAL` — блокеры старта) | **{s.critical_count}** |")
    out.append(f"| Существенных дефектов (`MAJOR`) | **{s.major_count}** |")
    out.append(f"| Незначительных замечаний (`MINOR`) | **{s.minor_count}** |")
    out.append("")

    if s.by_criterion:
        out.append("### Распределение нарушений по критериям стандартов")
        out.append("")
        out.append("| Критерий (ISO/IEC/IEEE 29148 / IEEE 830) | Количество нарушений |")
        out.append("| :--- | :--- |")
        for crit_name, cnt in sorted(s.by_criterion.items(), key=lambda x: -x[1]):
            out.append(f"| {crit_name} | {cnt} |")
        out.append("")

    critical_all: list[tuple[str, str, str, str]] = []
    for sd in report.set_defects:
        if sd.severity == "CRITICAL":
            locs = "; ".join(sp.format_locator() for sp in sd.evidence)
            critical_all.append((sd.defect_id, "Набор требований", locs, sd.title))
    for ad in report.atomic_defects:
        if ad.severity == "CRITICAL":
            locs = "; ".join(sp.format_locator() for sp in ad.evidence)
            tag = f"{ad.req_id}" + (f" ({ad.doc_id})" if ad.doc_id else "")
            critical_all.append((ad.defect_id, tag, locs, ad.problem))

    if critical_all:
        out.append("### Реестр критичных дефектов (блокирующих разработку и тестирование)")
        out.append("")
        out.append("| ID | Объект | Локация в документе | Краткая суть блокера |")
        out.append("| :--- | :--- | :--- | :--- |")
        for cid, obj, loc, desc in critical_all:
            out.append(f"| `{cid}` | {obj} | {_esc_md_cell(loc)} | {_esc_md_cell(desc)} |")
        out.append("")

    # 6. Итог
    out.append("## 6. Итог и заключение о готовности к разработке и тестированию")
    out.append("")
    dev_status = "ДА (можно начинать)" if s.can_start_development else "НЕТ (нельзя начинать до устранения блокеров)"
    qa_status = "ДА (можно начинать)" if s.can_start_testing else "НЕТ (критерии приёмки и контракты противоречивы)"
    if s.can_start_development is None:
        dev_status = qa_status = "НЕДОСТАТОЧНО ДАННЫХ"
    out.append(f"- **Итоговый статус документа:** **{s.verdict}** (Индекс качества: **{s.score_label}**)")
    out.append(f"- **Готовность к передаче в разработку:** **{dev_status}**")
    out.append(f"- **Готовность к проектированию приёмочных тестов (QA):** **{qa_status}**")
    out.append(f"- **Обоснование:** {s.verdict_rationale}")
    out.append("")

    if s.priority_fixes:
        out.append("### Что необходимо исправить в первую очередь (до старта разработки)")
        out.append("")
        for idx, fix in enumerate(s.priority_fixes, start=1):
            out.append(f"{idx}. {fix}")
        out.append("")

    return "\n".join(out)
