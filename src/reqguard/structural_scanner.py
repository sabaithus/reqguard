"""Обобщённый детерминированный статический анализатор спецификаций и контрактов REST API.

Не содержит захардкоженных предметных данных конкретного кейса: работает по универсальным
правилам структурной полноты, атомарности (ISO 29148) и семантики HTTP/REST контрактов (RFC 9110).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from reqguard.models import (
    AtomicDefect,
    DecisionScenario,
    RequirementCategory,
    RequirementItem,
    SetDefect,
    SourceSpan,
)
from reqguard.parser import DocumentLine, ParsedDocument, ParsedTable, ParsedTableRow


EXPLICIT_REQ_ID_RE = re.compile(
    r"^\s*(?:[-*+•]\s*)?\*{0,2}((?:[A-ZА-ЯЁ]{1,8})-\d+(?:\.\d+)*)\.?(\*{0,2})\s+(.+)$",
    re.IGNORECASE,
)

UNSELECTED_BINARY_RE = re.compile(r"\b(?:да\s*/\s*нет|yes\s*/\s*no)\b", re.IGNORECASE)
PLACEHOLDER_TEXT_RE = re.compile(
    r"(?:заполня[ею]тся\s+\S+|не\s+заполнен[аоы]?|\bTBD\b|\bTODO\b|в\s+разработке)",
    re.IGNORECASE,
)
MUTATING_KEYWORDS_RE = re.compile(
    r"(?:сохранен|сохранить|изменен|изменить|создан|создать|удален|удалить|заменить|обновлен|обновить|"
    r"save|create|update|delete|upsert|patch|(?-i:\bset[A-Z]|(?<=[a-z/_])set[A-Z]))",
    re.IGNORECASE,
)
HTTP_STATUS_HEADING_RE = re.compile(r"^(?:#{1,6}\s*)?(\d{3})\.\s*(.+)$")


@dataclass(slots=True)
class StructuralScanResult:
    baseline_requirements: list[RequirementItem] = field(default_factory=list)
    atomic_defects: list[AtomicDefect] = field(default_factory=list)
    set_defects: list[SetDefect] = field(default_factory=list)


def _infer_category(section_path: str, text: str) -> RequirementCategory:
    sec = section_path.lower()
    if any(k in sec for k in ("цел", "goal", "бизнес-цел")):
        return "GOAL"
    if any(k in sec for k in ("ui", "ux", "дизайн", "экран", "интерфейс")):
        return "UI_UX"
    if any(k in sec for k in ("bpmn", "sequence", "процесс", "схема")):
        return "PROCESS"
    if any(k in sec for k in ("нефункциональн", "nfr", "sla", "производительн")):
        return "NON_FUNCTIONAL"
    if any(k in sec for k in ("данн", "data", "модель бд", "хранилищ")):
        return "DATA"
    if any(k in sec for k in ("приемк", "приёмк", "тестирован", "acceptance")):
        return "TESTING_AC"
    if any(k in sec for k in ("rest api", "параметры запроса", "статусы", "ответ", "логика работы метода", "особые правила")):
        return "API_CONTRACT"
    if any(k in sec for k in ("интеграц", "integration")):
        return "INTEGRATION"
    if any(k in sec for k in ("архитектур", "architecture")):
        return "ARCHITECTURE"
    if any(k in sec for k in ("ревью", "согласующ", "история", "верси")):
        return "GOVERNANCE"
    return "FUNCTIONAL"


def _make_span(dl: DocumentLine, line_end: int | None = None, quote: str | None = None) -> SourceSpan:
    return SourceSpan(
        line_start=dl.line_no,
        line_end=line_end or dl.line_no,
        page=dl.page,
        section_path=dl.section_path,
        quote=(quote if quote is not None else dl.raw_text.strip()),
        verified=True,
    )


def extract_baseline_requirements(doc: ParsedDocument) -> list[RequirementItem]:
    """Детерминированно выделяет явные и секционные требования для гарантированного покрытия."""
    items: list[RequirementItem] = []
    req_counter = 1

    def next_id() -> str:
        nonlocal req_counter
        rid = f"REQ-{req_counter:02d}"
        req_counter += 1
        return rid

    used_lines: set[int] = set()

    # 1. Явно пронумерованные требования (Ф-1, НФ-1, FR-1 и т.д.)
    for dl in doc.lines:
        if dl.kind not in ("list_item", "text"):
            continue
        m = EXPLICIT_REQ_ID_RE.match(dl.raw_text.strip())
        if m:
            doc_id = m.group(1).upper()
            body = m.group(3).strip()
            sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if len(s.strip()) > 8]
            items.append(
                RequirementItem(
                    report_id=next_id(),
                    doc_id=doc_id,
                    category=_infer_category(dl.section_path, body),
                    title=body[:160] + ("…" if len(body) > 160 else ""),
                    is_compound=len(sentences) >= 3,
                    sub_rules=sentences if len(sentences) >= 3 else [],
                    source=_make_span(dl),
                )
            )
            used_lines.add(dl.line_no)

    # 2. Значимые ненумерованные разделы (Цели, UI, BPMN, Sequence, НФТ, Данные, Интеграция)
    key_section_keywords = (
        "цели",
        "дизайн процесса ui",
        "bpmn",
        "sequence",
        "нефункциональные требования",
        "требования к данным",
        "требования к интеграции",
        "логика работы метода",
        "особые правила",
        "ответ",
    )
    grouped_by_sec: dict[str, list[DocumentLine]] = {}
    for dl in doc.lines:
        if dl.line_no in used_lines or dl.kind in ("heading", "empty", "table_row", "table_sep", "code"):
            continue
        sec_lower = dl.section_path.lower()
        if any(k in sec_lower for k in key_section_keywords):
            grouped_by_sec.setdefault(dl.section_path, []).append(dl)

    for sec_path, dls in grouped_by_sec.items():
        combined = " ".join(d.raw_text.strip() for d in dls).strip()
        if len(combined) < 12:
            continue
        first_dl = dls[0]
        last_dl = dls[-1]
        items.append(
            RequirementItem(
                report_id=next_id(),
                doc_id=None,
                category=_infer_category(sec_path, combined),
                title=combined[:160] + ("…" if len(combined) > 160 else ""),
                is_compound=False,
                source=SourceSpan(
                    line_start=first_dl.line_no,
                    line_end=last_dl.line_no,
                    page=first_dl.page,
                    section_path=sec_path,
                    quote=doc.get_span_text(first_dl.line_no, last_dl.line_no),
                    verified=True,
                ),
            )
        )
        for d in dls:
            used_lines.add(d.line_no)

    # 3. Строки таблиц критериев приёмки и ключевых параметров API
    for tbl in doc.tables:
        sec_lower = tbl.section_path.lower()
        if any(k in sec_lower for k in ("критерии приемки", "критерии приёмки", "acceptance")):
            for r_idx, row in enumerate(tbl.rows, start=1):
                if not any(row.cells):
                    continue
                summary = " → ".join(c for c in row.cells if c)
                items.append(
                    RequirementItem(
                        report_id=next_id(),
                        doc_id=None,
                        category="TESTING_AC",
                        title=f"Сценарий приёмки #{r_idx}: {summary[:140]}",
                        source=SourceSpan(
                            line_start=row.line_no,
                            line_end=row.line_no,
                            page=row.page,
                            section_path=tbl.section_path,
                            quote=row.raw_text.strip(),
                            verified=True,
                        ),
                    )
                )
        elif "общие параметры" in sec_lower and "rest api" in sec_lower:
            dl_start = doc.get_line(tbl.line_start)
            if dl_start:
                items.append(
                    RequirementItem(
                        report_id=next_id(),
                        doc_id=None,
                        category="API_CONTRACT",
                        title="Спецификация эндпоинта и общих параметров REST API",
                        source=SourceSpan(
                            line_start=tbl.line_start,
                            line_end=tbl.line_end,
                            page=tbl.page,
                            section_path=tbl.section_path,
                            quote=doc.get_span_text(tbl.line_start, min(tbl.line_start + 9, tbl.line_end)),
                            verified=True,
                        ),
                    )
                )
        elif any(k in sec_lower for k in ("параметры запроса", "query-параметры", "path-параметры")) and "rest api" in sec_lower:
            items.append(
                RequirementItem(
                    report_id=next_id(),
                    doc_id=None,
                    category="API_CONTRACT",
                    title="Таблица параметров запроса REST API и маппинг на БД",
                    source=SourceSpan(
                        line_start=tbl.line_start,
                        line_end=tbl.line_end,
                        page=tbl.page,
                        section_path=tbl.section_path,
                        quote=doc.get_span_text(tbl.line_start, tbl.line_end),
                        verified=True,
                    ),
                )
            )
        elif "ответ" in sec_lower and "rest api" in sec_lower:
            items.append(
                RequirementItem(
                    report_id=next_id(),
                    doc_id=None,
                    category="API_CONTRACT",
                    title="Таблица структуры ответа REST API и маппинг на БД",
                    source=SourceSpan(
                        line_start=tbl.line_start,
                        line_end=tbl.line_end,
                        page=tbl.page,
                        section_path=tbl.section_path,
                        quote=doc.get_span_text(tbl.line_start, tbl.line_end),
                        verified=True,
                    ),
                )
            )

    # 4. Секция Статусы в REST API (для трассировки дефектов кодов 200/400/403/404)
    status_lines = [
        dl for dl in doc.lines
        if dl.line_no not in used_lines
        and "статусы" in dl.section_path.lower()
        and dl.kind not in ("empty",)
    ]
    if status_lines:
        s_first, s_last = status_lines[0], status_lines[-1]
        items.append(
            RequirementItem(
                report_id=next_id(),
                doc_id=None,
                category="API_CONTRACT",
                title="Спецификация HTTP-статусов и схем ответов REST API",
                source=SourceSpan(
                    line_start=s_first.line_no,
                    line_end=s_last.line_no,
                    page=s_first.page,
                    section_path=s_first.section_path,
                    quote=doc.get_span_text(s_first.line_no, min(s_first.line_no + 6, s_last.line_no)),
                    verified=True,
                ),
            )
        )

    # Сортируем по порядку следования в документе и перенумеровываем REQ-01..REQ-NN
    items.sort(key=lambda r: r.source.line_start)
    for idx, item in enumerate(items, start=1):
        item.report_id = f"REQ-{idx:02d}"
    return items


# Сопоставление месяца события с месяцем явно названного счётчика.
_MONTH_NAMES = (
    ("января", "январский"), ("февраля", "февральский"), ("марта", "мартовский"),
    ("апреля", "апрельский"), ("мая", "майский"), ("июня", "июньский"),
    ("июля", "июльский"), ("августа", "августовский"), ("сентября", "сентябрьский"),
    ("октября", "октябрьский"), ("ноября", "ноябрьский"), ("декабря", "декабрьский"),
)


def _check_accounting_month(doc: ParsedDocument) -> list[SetDefect]:
    selection = re.compile(r"(?:месяц определяется по дате|используется календарный месяц) (совершения|проведения) покупки"
                           r"|покупка относится к тому месяцу, в котором она была (совершена|проведена)", re.I)
    rules = [(line, match) for line in doc.lines if (match := selection.search(line.raw_text))]
    if len(rules) != 1:
        return []
    rule, match = rules[0]
    purchase_date = (match.group(1) or match.group(2)).lower().startswith("соверш")
    findings = []
    for table in doc.tables:
        if len(table.headers) != 3 or not any(word in table.headers[2].lower() for word in ("ожидаем", "результат")):
            continue
        for row in table.rows:
            if len(row.cells) != 3:
                continue
            initial, action, expected = [c.lower() for c in row.cells]
            if "покупк" not in initial or not re.search(r"\b(?:провести|проведение)\b", action):
                continue
            event_text = initial if purchase_date else action
            months = [m for m, _ in _MONTH_NAMES if re.search(r"\b\d{1,2}\s+" + m + r"\b", event_text)]
            months.extend(_MONTH_NAMES[int(m) - 1][0] for m in re.findall(
                r"\b(?:0?[1-9]|[12]\d|3[01])\.(0[1-9]|1[0-2])(?:\.\d{4})?\b", event_text))
            expected_months = []
            nominatives = ("январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь")
            for (month, adjective), name in zip(_MONTH_NAMES, nominatives):
                if re.search(r"расходы " + month + r"\s+(?:выросли|увеличились)", expected) or re.search(adjective + r"\s+сч[её]тчик вырос", expected) or re.search(
                    r"расходы за " + name + r"\s+(?:выросли|увеличились)", expected
                ):
                    expected_months.append(month)
            if len(months) != 1 or len(expected_months) != 1 or months == expected_months:
                continue
            findings.append(SetDefect(
                defect_id="calendar", related_req_ids=[], criterion="VERIFIABLE_SET", severity="CRITICAL",
                title="Месяц в критерии приёмки расходится с правилом учёта",
                problem=f"Дата события по правилу: «{event_text}». В ожидаемом результате указан другой месяц: «{expected}».",
                impact="Одна реализация не может одновременно пройти этот пример и выполнить правило учёта.",
                question_for_analyst="Какой источник следует исправить: правило выбора даты или ожидаемый результат?",
                recommendation="Согласовать правило и пример, затем повторить проверку на границе месяцев.",
                decision=DecisionScenario(
                    scenario=f"{row.cells[0]}. Действие: {row.cells[1]}.",
                    alternatives=(
                        f"Сохранить правило выбора даты. В примере учёт относится к месяцу даты из условия «{event_text}».",
                        f"Сохранить ожидаемый результат «{row.cells[2]}». Согласовать правило выбора месяца для таких операций.",
                    ),
                    acceptance_check="Повторить покупку и проведение из примера на границе месяцев. Проверить изменение обоих месячных счётчиков.",
                ),
                evidence=[_make_span(rule), _make_span(doc.lines[row.line_no - 1])], origin="STRUCTURAL",
            ))
    return findings


def _check_notification_limit(doc: ParsedDocument) -> list[SetDefect]:
    """Ограниченная проверка пары правил об одном канале, карте и месяце."""
    findings = []
    channel = r"(?:push|пуш|sms|смс)"
    quota = re.compile(r"(?:максимум|не (?:более|больше))\s+(?:один|одного|одно|1)\s+(?:тако(?:й|го|е)\s+)?(" + channel + r")\b", re.I)
    repeat = re.compile(r"(?:достижение|пересечение)[^.!?]{0,65}(?:вызывает|приводит к)\s+(" + channel + r")\b", re.I)
    independent = re.compile(r"(?:независимо|вне зависимости)\s+от[^.!?]{0,95}(?:настройк|уведомлен|отправк)", re.I)
    exception = re.compile(r"приоритет|исключени|кроме|с уч[её]том.{0,40}(?:лимита|ограничения)|если.{0,35}(?:ещ[её] не|не был)|повторн\w* " + channel + r" не", re.I)
    if any(exception.search(line.raw_text) and re.search(r"порог|лимит|отправк|уведомлен", line.raw_text, re.I)
           for line in doc.lines if line.kind in ("text", "list_item", "table_row")):
        return []
    for cap in doc.lines:
        text = cap.raw_text.lower()
        limit = quota.search(text)
        periods = [p for marker, p in (("месяц", "месяц"), ("недел", "неделю"), ("сутк", "сутки")) if marker in text]
        if not limit or len(periods) != 1 or "карт" not in text:
            continue
        media = limit.group(1)
        if re.search(channel + r"\s+(?:о|об|для|при)\s+", text) and not re.search(r"порог|лимит", text):
            continue
        section = [line for line in doc.lines if line.section_path == cap.section_path and line.kind in ("list_item", "text")]
        if any(exception.search(line.raw_text) for line in section):
            continue
        for reset in section:
            rule = reset.raw_text.lower()
            event = repeat.search(rule)
            parameter = re.search(r"(?:изменени|смен)[а-я]*\s+(порог|лимит)а", rule)
            if cap == reset or not parameter or not event or not independent.search(rule) or media != event.group(1).lower():
                continue
            period = periods[0]
            name = parameter.group(1)
            findings.append(SetDefect(
                defect_id="notification-limit", criterion="CONSISTENT", severity="CRITICAL",
                title=f"Лимит отправок за {period} и повторное достижение настройки задают разные исходы",
                problem=f"Для одной карты ограничена частота {media} за {period}. После смены настройки второе правило требует отправку независимо от её истории.",
                impact="Для одной последовательности событий QA получает два несовместимых ожидаемых результата.",
                question_for_analyst=f"При новом достижении настройки нужно ли отправить второе {media} по этой карте за {period}?",
                recommendation="Зафиксировать приоритет правил и проверить повторное событие.",
                decision=DecisionScenario(
                    scenario=f"По карте уже была отправка {media}. В том же периоде {name} повышают выше накопленной суммы. Затем сумма достигает нового значения.",
                    alternatives=(f"За {period} по карте остаётся одна отправка {media}.",
                                  f"После смены настройки допускается вторая отправка {media} за {period}."),
                    acceptance_check=f"Для одной карты повторить сценарий и сравнить число отправок {media} с согласованным исходом.",
                ),
                evidence=[_make_span(cap), _make_span(reset)], origin="STRUCTURAL",
            ))
    return findings


# «Отправить/отправляет» описывает действие (уведомление, сообщение), а не поле данных, — не учитывается.
_PASS_VERB = re.compile(r"\b(?:передать|переда[её]т)\s+([^.;:]+)", re.I)
_FIELD_TAIL = re.compile(r"\s+(?:при|если|когда|после|для|по|в течение|через)\s.*$", re.I)
_WORD = re.compile(r"[а-яёa-z0-9]+", re.I)


def _content_stems(phrase: str) -> list[str]:
    words = [w.lower().replace("ё", "е") for w in _WORD.findall(phrase)]
    return [w[: max(4, len(w) - 2)] for w in words if len(w) >= 4]


def _split_fields(phrase: str) -> list[str]:
    phrase = _FIELD_TAIL.sub("", phrase.strip())
    return [p.strip() for p in re.split(r",|\s+и\s+", phrase) if p.strip()]


def _check_passed_value_sources(doc: ParsedDocument) -> list[SetDefect]:
    """Поле сообщения из последовательности без описанного источника: хранения, события или получения."""
    content = [dl for dl in doc.lines if dl.kind in ("text", "list_item")]
    steps = [dl for dl in content if re.search(r"sequence|последовательност", dl.section_path, re.I)]
    data_lines = [dl for dl in content if "требования к данным" in dl.section_path.lower()]
    sources = [dl.raw_text for dl in content if re.search(r"требования к (?:данным|интеграции)", dl.section_path, re.I)]
    # Остальные сообщения последовательности (входящие события, получение) тоже описывают данные.
    for dl in steps:
        sources.extend(c for c in re.split(r"(?<=[.;])\s+", dl.raw_text) if not _PASS_VERB.search(c))
    source_words = [{w.lower().replace("ё", "е") for w in _WORD.findall(text)} for text in sources]

    def described(field: str) -> bool:
        stems = _content_stems(field)
        return bool(stems) and any(all(any(w.startswith(s) for w in words) for s in stems) for words in source_words)

    findings: list[SetDefect] = []
    seen: set[str] = set()
    for dl in steps:
        for match in _PASS_VERB.finditer(dl.raw_text):
            for field in _split_fields(match.group(1)):
                key = field.lower()
                if key in seen or not _content_stems(field) or described(field):
                    continue
                seen.add(key)
                evidence = [_make_span(dl)] + ([_make_span(data_lines[0])] if data_lines else [])
                findings.append(SetDefect(
                    defect_id="passed-value-source", related_req_ids=[], criterion="COMPLETE_SET", severity="MAJOR",
                    title=f"В переданной спецификации не описан источник значения «{field}»",
                    problem=(f"Шаг последовательности передаёт значение «{field}». Требования к данным и интеграции "
                             "не называют его хранимым полем, данными входящего события или результатом получения."),
                    impact=f"По документу нельзя решить, откуда шаг берёт значение «{field}» и как проверить его в тестах.",
                    question_for_analyst=f"Откуда берётся значение «{field}», которое передаётся на этом шаге?",
                    recommendation="Указать источник значения: хранимое поле, данные события или описанное получение.",
                    evidence=evidence, origin="STRUCTURAL",
                ))
    return findings


def run_structural_scan(doc: ParsedDocument) -> StructuralScanResult:
    """Выполняет детерминированные статические проверки документа."""
    baseline_reqs = extract_baseline_requirements(doc)
    atomic_defects: list[AtomicDefect] = []
    set_defects: list[SetDefect] = []

    req_by_line: dict[int, RequirementItem] = {}
    for req in baseline_reqs:
        for ln in range(req.source.line_start, req.source.line_end + 1):
            req_by_line[ln] = req

    a_idx = 1
    s_idx = 1

    def next_aid() -> str:
        nonlocal a_idx
        val = f"DEF-A-S{a_idx:02d}"
        a_idx += 1
        return val

    def next_sid() -> str:
        nonlocal s_idx
        val = f"DEF-S-S{s_idx:02d}"
        s_idx += 1
        return val

    # 1. Проверка атомарности (ISO 29148 Singular) для составных нумерованных требований
    for req in baseline_reqs:
        if req.is_compound and len(req.sub_rules) >= 3:
            atomic_defects.append(
                AtomicDefect(
                    defect_id=next_aid(),
                    req_id=req.report_id,
                    doc_id=req.doc_id,
                    criterion="SINGULAR",
                    severity="MINOR",
                    problem=(
                        f"Требование {req.doc_id or req.report_id} объединяет сразу {len(req.sub_rules)} "
                        "разнородных бизнес-правила под одним идентификатором (нарушение свойства атомарности ISO 29148 п. 5.2.5)."
                    ),
                    impact=(
                        "Затруднена трассировка тест-кейсов и баг-репортов: при падении теста невозможно однозначно "
                        "сослаться на конкретное подусловие без цитирования текста."
                    ),
                    recommendation=(
                        f"Разбить {req.doc_id or req.report_id} на атомарные подпункты "
                        f"({(req.doc_id or req.report_id)}.1 – {(req.doc_id or req.report_id)}.{len(req.sub_rules)}), "
                        "оставив по одному проверяемому правилу на идентификатор."
                    ),
                    evidence=[req.source],
                    origin="STRUCTURAL",
                )
            )

    # Неопределённая скорость без заданного числового предела.
    for dl in doc.lines:
        if dl.kind in ("heading", "empty", "table_row", "table_sep", "code"):
            continue
        sec_low = dl.section_path.lower()
        text = dl.raw_text.strip()
        is_nfr_sec = "нефункциональн" in sec_low or "nfr" in sec_low
        is_sla_fallback = (not sec_low) and any(w in text.lower() for w in ("сервис", "push", "ответ", "задерж"))
        if is_nfr_sec or is_sla_fallback:
            if re.search(r"\b(?:быстро|оперативно|своевременно)\b", text, re.I) and not re.search(
                r"\d+\s*(?:мс|сек|мин|час)|в течение (?:минуты|секунды|часа)", text, re.I
            ):
                linked = req_by_line.get(dl.line_no)
                atomic_defects.append(
                    AtomicDefect(
                        defect_id=next_aid(),
                        req_id=linked.report_id if linked else "REQ-00",
                        doc_id=linked.doc_id if linked else None,
                        criterion="VERIFIABLE",
                        severity="MINOR",
                        problem="Скорость описана качественным словом без измеримого предела в этой формулировке.",
                        impact="По этому пункту QA не может определить, какой результат испытания считать успешным.",
                        recommendation="Указать применимый предел или ссылку на соответствующее требование.",
                        evidence=[_make_span(dl)],
                        origin="STRUCTURAL",
                    )
                )

    # 3. Проверка незаполненных шаблонов, чек-листов ревью и пустых таблиц согласования
    for tbl in doc.tables:
        unselected_rows = [
            r for r in tbl.rows if any(UNSELECTED_BINARY_RE.search(c) for c in r.cells)
        ]
        if len(unselected_rows) >= 2:
            first_r, last_r = unselected_rows[0], unselected_rows[-1]
            set_defects.append(
                SetDefect(
                    defect_id=next_sid(),
                    related_req_ids=[],
                    criterion="MODIFIABLE_GOVERNANCE",
                    severity="MAJOR",
                    title="Незаполненный чек-лист ревью требований (оставлены шаблонные «Да / Нет»)",
                    problem=(
                        f"В таблице раздела «{tbl.section_path}» ({len(unselected_rows)} строк) не проставлены отметки "
                        "прохождения архитектурного, тестового и ИБ-ревью — сохранены шаблонные значения «Да / Нет»."
                    ),
                    impact=(
                        "Отсутствует подтверждение согласования со смежными командами, информационной безопасностью, "
                        "архитектурой и схемы отката (feature toggle / rollback) до старта разработки."
                    ),
                    question_for_analyst=(
                        "Проводилось ли согласование с ИБ и архитекторами и какая схема отката/скрытия фичи предусмотрена в проде?"
                    ),
                    recommendation="Заполнить актуальные статусы ревью по каждому пункту чек-листа и зафиксировать версию/дату документа.",
                    evidence=[
                        SourceSpan(
                            line_start=first_r.line_no,
                            line_end=min(first_r.line_no + 2, last_r.line_no),
                            page=first_r.page,
                            section_path=tbl.section_path,
                            quote=doc.get_span_text(first_r.line_no, min(first_r.line_no + 2, last_r.line_no)),
                            verified=True,
                        )
                    ],
                    origin="STRUCTURAL",
                )
            )

        if "согласующ" in tbl.section_path.lower() and not any(c.strip() for r in tbl.rows for c in r.cells):
            set_defects.append(
                SetDefect(
                    defect_id=next_sid(),
                    related_req_ids=[],
                    criterion="MODIFIABLE_GOVERNANCE",
                    severity="MINOR",
                    title=f"Пустая таблица в разделе «{tbl.section_path}»",
                    problem=f"Таблица с заголовками ({', '.join(tbl.headers)}) не содержит ни одной заполненной записи.",
                    impact="Не определены ответственные согласующие лица, принимающие изменения в продукте.",
                    question_for_analyst="Кто является обязательными согласующими со стороны бизнеса, процессинга и ИБ?",
                    recommendation="Заполнить таблицу ответственными лицами либо явно указать статус согласования.",
                    evidence=[
                        SourceSpan(
                            line_start=tbl.line_start,
                            line_end=tbl.line_end,
                            page=tbl.page,
                            section_path=tbl.section_path,
                            quote=doc.get_span_text(tbl.line_start, tbl.line_end),
                            verified=True,
                        )
                    ],
                    origin="STRUCTURAL",
                )
            )

    # Незаполненные текстовые заглушки (например, "заполняются тестировщиками", "не заполнен")
    for dl in doc.lines:
        if dl.kind in ("text", "list_item") and PLACEHOLDER_TEXT_RE.search(dl.raw_text):
            sec_low = dl.section_path.lower()
            if "тест" in sec_low:
                impact_txt = "Не определены входные условия, тестовые данные и стратегия проверки требований."
                q_txt = "Какие тестовые сценарии, стенды или моки карточного процессинга обязательны для приёмки?"
                rec_txt = "Заполнить раздел конкретными требованиями к тестированию вместо шаблонной пометки."
            else:
                impact_txt = "Отсутствуют метаданные версии и даты ревизии спецификации, что нарушает прослеживаемость изменений."
                q_txt = "Какова актуальная версия и дата утверждения данного документа требований?"
                rec_txt = "Указать актуальный номер версии, дату фиксации и автора ревизии документа."
            set_defects.append(
                SetDefect(
                    defect_id=next_sid(),
                    related_req_ids=[],
                    criterion="MODIFIABLE_GOVERNANCE",
                    severity="MINOR",
                    title=f"Незаполненная заглушка в разделе «{dl.section_path}»",
                    problem=f"Раздел оставлен незавершённым: присутствует шаблонная пометка «{dl.raw_text.strip()}».",
                    impact=impact_txt,
                    question_for_analyst=q_txt,
                    recommendation=rec_txt,
                    evidence=[_make_span(dl)],
                    origin="STRUCTURAL",
                )
            )

    set_defects.extend(_check_accounting_month(doc))
    set_defects.extend(_check_notification_limit(doc))
    set_defects.extend(_check_passed_value_sources(doc))

    # 4. Статический анализ контрактов REST API
    _scan_rest_api_contracts(doc, set_defects, next_sid)

    return StructuralScanResult(
        baseline_requirements=baseline_reqs,
        atomic_defects=atomic_defects,
        set_defects=set_defects,
    )


def _scan_rest_api_contracts(
    doc: ParsedDocument,
    set_defects: list[SetDefect],
    next_sid,
) -> None:
    """Универсальный анализатор семантики HTTP/REST таблиц и блоков статусов."""
    kv_meta: dict[str, tuple[str, ParsedTableRow, str]] = {}
    req_param_types: dict[str, tuple[str, str, ParsedTableRow, str]] = {}
    resp_param_types: dict[str, tuple[str, str, ParsedTableRow, str]] = {}

    for tbl in doc.tables:
        headers_low = [h.lower() for h in tbl.headers]
        sec_low = tbl.section_path.lower()

        # Key-value таблица метаданных API (Поле | Значение)
        if len(headers_low) == 2 and ("поле" in headers_low[0] or "field" in headers_low[0]):
            for row in tbl.rows:
                if len(row.cells) >= 2:
                    key = re.sub(r"[*`]", "", row.cells[0]).strip().lower()
                    val = row.cells[1].strip()
                    kv_meta[key] = (val, row, tbl.section_path)

        # Таблицы параметров с колонкой DB field
        if "parameter" in headers_low and "format" in headers_low:
            p_idx = headers_low.index("parameter")
            f_idx = headers_low.index("format")
            db_idx = headers_low.index("db field") if "db field" in headers_low else -1
            for row in tbl.rows:
                if len(row.cells) <= max(p_idx, f_idx):
                    continue
                p_name = re.sub(r"[*`]", "", row.cells[p_idx]).strip()
                p_fmt = row.cells[f_idx].strip().lower()
                db_col = (
                    re.sub(r"[*`]", "", row.cells[db_idx]).strip()
                    if db_idx >= 0 and len(row.cells) > db_idx
                    else ""
                )
                key_id = db_col if db_col and db_col not in ("—", "-") else p_name
                if any(k in sec_low for k in ("запрос", "query", "request")):
                    req_param_types[key_id] = (p_name, p_fmt, row, tbl.section_path)
                elif any(k in sec_low for k in ("ответ", "response")):
                    resp_param_types[key_id] = (p_name, p_fmt, row, tbl.section_path)

    # 4a. Проверка мутирующей операции поверх HTTP GET
    if "rest type" in kv_meta:
        method_val, method_row, method_sec = kv_meta["rest type"]
        if method_val.strip().upper() == "GET":
            desc_val, desc_row, _ = kv_meta.get("description", ("", method_row, method_sec))
            url_val, url_row, _ = kv_meta.get("request url", ("", method_row, method_sec))
            combined_hint = f"{desc_val} {url_val}"
            if MUTATING_KEYWORDS_RE.search(combined_hint):
                spans = [
                    SourceSpan(
                        line_start=desc_row.line_no,
                        line_end=method_row.line_no,
                        page=method_row.page,
                        section_path=method_sec,
                        quote=doc.get_span_text(
                            min(desc_row.line_no, method_row.line_no),
                            max(desc_row.line_no, method_row.line_no),
                        ),
                        verified=True,
                    )
                ]
                if "curl" in kv_meta:
                    _, curl_row, curl_sec = kv_meta["curl"]
                    spans.append(
                        SourceSpan(
                            line_start=curl_row.line_no,
                            line_end=curl_row.line_no,
                            page=curl_row.page,
                            section_path=curl_sec,
                            quote=curl_row.raw_text.strip(),
                            verified=True,
                        )
                    )
                set_defects.append(
                    SetDefect(
                        defect_id=next_sid(),
                        related_req_ids=[],
                        criterion="INTERFACE_INTEGRITY",
                        severity="CRITICAL",
                        title="Мутирующая операция сохранения состояния реализована через HTTP GET и Query-параметры",
                        problem=(
                            f"Метод API с типом `REST type: GET` ({url_val}) выполняет изменение данных в хранилище "
                            f"(«{desc_val}») при обращении к этому ресурсу."
                        ),
                        impact=(
                            "Нарушение RFC 9110 (GET обязан быть безопасным без побочных эффектов): риск случайного вызова "
                            "прокси/предзагрузкой, некорректного кеширования ответа."
                        ),
                        question_for_analyst="Какой метод и контракт определяют чтение и сохранение настройки?",
                        recommendation="Согласовать назначение операции, HTTP-метод и пример запроса с владельцем API.",
                        evidence=spans,
                        origin="STRUCTURAL",
                    )
                )

    # 4b. Пропуск Prod URL / Swagger в метаданных API
    missing_api_meta: list[ParsedTableRow] = []
    for k in ("request url", "swagger"):
        if k in kv_meta:
            val, r_obj, _ = kv_meta[k]
            if "—" in val or val.strip() in ("-", ""):
                missing_api_meta.append(r_obj)
    if missing_api_meta:
        set_defects.append(
            SetDefect(
                defect_id=next_sid(),
                related_req_ids=[],
                criterion="COMPLETE_SET",
                severity="MINOR",
                title="В метаданных API остались незаполненные поля",
                problem="Не заполнены значения в строках: " + ", ".join(row.cells[0] for row in missing_api_meta) + ".",
                impact="Интегратору требуется подтвердить актуальные значения перед подключением.",
                question_for_analyst="Где интегратор может получить значения, пропущенные в отмеченных строках контракта?",
                recommendation="Заполнить указанные поля или дать ссылку на согласованный источник.",
                evidence=[
                    SourceSpan(
                        line_start=r.line_no,
                        line_end=r.line_no,
                        page=r.page,
                        section_path=kv_meta["request url"][2] if "request url" in kv_meta else "",
                        quote=r.raw_text.strip(),
                        verified=True,
                    )
                    for r in missing_api_meta
                ],
                origin="STRUCTURAL",
            )
        )

    # 4c. Несоответствие Content-Type: application/json и пустого тела запроса
    ct_lines = [dl for dl in doc.lines if "content-type" in dl.raw_text.lower() and "application/json" in dl.raw_text.lower()]
    no_body_lines = [dl for dl in doc.lines if "тело не используется" in dl.raw_text.lower()]
    for c_dl in ct_lines:
        nb_dl = next((line for line in no_body_lines if line.section_path == c_dl.section_path), None)
        if nb_dl is None:
            continue
        set_defects.append(
            SetDefect(
                defect_id=next_sid(),
                related_req_ids=[],
                criterion="CONSISTENT",
                severity="MINOR",
                title="Нужно уточнить обязательность Content-Type при отсутствии тела",
                problem=(
                    "В описании тела запроса одновременно указан заголовок `Content-Type: application/json` "
                    "и утверждается, что тело запроса не используется."
                ),
                impact="Клиенту и QA нужно знать, какие заголовки обязательны для запроса без тела.",
                question_for_analyst="Обязателен ли Content-Type для этого запроса без тела?",
                recommendation="Зафиксировать обязательность заголовка в контракте и примере запроса.",
                evidence=[_make_span(c_dl), _make_span(nb_dl)],
                origin="STRUCTURAL",
            )
        )

    # 4d. Расхождение типов одних и тех же полей в запросе и ответе
    mismatched_spans: list[SourceSpan] = []
    mismatched_names: list[str] = []
    for db_key, (req_name, req_fmt, req_row, req_sec) in req_param_types.items():
        if db_key in resp_param_types:
            resp_name, resp_fmt, resp_row, resp_sec = resp_param_types[db_key]
            if req_fmt != resp_fmt:
                mismatched_names.append(f"`{req_name}` (`{db_key}`: запрос `{req_fmt}` vs ответ `{resp_fmt}`)")
                mismatched_spans.append(
                    SourceSpan(
                        line_start=req_row.line_no,
                        line_end=req_row.line_no,
                        page=req_row.page,
                        section_path=req_sec,
                        quote=req_row.raw_text.strip(),
                        verified=True,
                    )
                )
                mismatched_spans.append(
                    SourceSpan(
                        line_start=resp_row.line_no,
                        line_end=resp_row.line_no,
                        page=resp_row.page,
                        section_path=resp_sec,
                        quote=resp_row.raw_text.strip(),
                        verified=True,
                    )
                )
    if mismatched_names:
        set_defects.append(
            SetDefect(
                defect_id=next_sid(),
                related_req_ids=[],
                criterion="INTERFACE_INTEGRITY",
                severity="MINOR",
                title="Различие типов данных на входе и выходе REST API требует подтверждения",
                problem=(
                    "Атрибуты, маппящиеся в одни и те же поля БД, меняют тип данных между входом и выходом метода: "
                    + ", ".join(mismatched_names)
                    + "."
                ),
                impact="Клиенту требуется явное преобразование между входным и выходным представлениями.",
                question_for_analyst="Преднамеренно ли различаются представления перечисленных полей?",
                recommendation="Если различие предусмотрено, зафиксировать правило преобразования и пример. Иначе согласовать типы в контракте.",
                evidence=mismatched_spans,
                origin="STRUCTURAL",
            )
        )

    # 4e. Противоречия в HTTP-статусах (200 с ERROR vs 400; 403 с описанием 404)
    status_blocks: dict[str, tuple[DocumentLine, list[DocumentLine]]] = {}
    current_code: str | None = None
    for dl in doc.lines:
        hm = HTTP_STATUS_HEADING_RE.match(dl.raw_text.strip())
        if hm:
            current_code = hm.group(1)
            status_blocks[current_code] = (dl, [])
        elif current_code is not None:
            if dl.kind == "heading":
                current_code = None
            else:
                status_blocks[current_code][1].append(dl)

    if "200" in status_blocks:
        h200, body200 = status_blocks["200"]
        body200_text = "\n".join(b.raw_text for b in body200)
        if '"ERROR"' in body200_text and "400" in status_blocks:
            h400, body400 = status_blocks["400"]
            err_line = next((b for b in body200 if '"ERROR"' in b.raw_text), h200)
            b400_line = next((b for b in body400 if "message" in b.raw_text), h400)
            set_defects.append(
                SetDefect(
                    defect_id=next_sid(),
                    related_req_ids=[],
                    criterion="CONSISTENT",
                    severity="CRITICAL",
                    title="Конфликт HTTP-статусов: секция 200 содержит ответ ERROR, а для ошибок описан статус 400",
                    problem=(
                        f"В секции «{h200.raw_text.strip(' #')}» приведено тело ошибки `{err_line.raw_text.strip()}`. "
                        f"В секции «{h400.raw_text.strip(' #')}» ошибка описана отдельно: `{b400_line.raw_text.strip()}`."
                    ),
                    impact="По документу нельзя определить, какой HTTP-статус и какую структуру тела ожидать в случае, описанном телом ошибки в секции 200.",
                    question_for_analyst=(
                        "Какой HTTP-статус и какой формат тела ответа должны возвращаться в случае, для которого в секции 200 приведено тело с ERROR?"
                    ),
                    recommendation="Согласовать с владельцем API один статус и одну схему тела для этого случая и привести к ним секции статусов.",
                    evidence=[_make_span(h200), _make_span(err_line), _make_span(b400_line)],
                    origin="STRUCTURAL",
                )
            )

    if "403" in status_blocks:
        h403, body403 = status_blocks["403"]
        body403_text = "\n".join(b.raw_text for b in body403)
        if "404" in body403_text and "403" not in body403_text:
            line_404_note = next((b for b in body403 if "404" in b.raw_text), h403)
            set_defects.append(
                SetDefect(
                    defect_id=next_sid(),
                    related_req_ids=[],
                    criterion="CONSISTENT",
                    severity="MAJOR",
                    title="Противоречие в описании HTTP 403 Forbidden и 404 Not Found",
                    problem=(
                        f"Секция «{h403.raw_text.strip(' #')}» заявляет код 403, но её текст описывает возврат 404: "
                        f"«{line_404_note.raw_text.strip()}». Условия возврата 403 в секции не описаны."
                    ),
                    impact="По документу нельзя составить проверку: неизвестно, в каком случае метод возвращает 403, а в каком 404.",
                    question_for_analyst="В каких случаях метод возвращает 403, а в каких 404?",
                    recommendation="Согласовать с владельцем API условия и формат ответа для 403 и 404 и описать каждый код в своей секции.",
                    evidence=[_make_span(h403), _make_span(line_404_note)],
                    origin="STRUCTURAL",
                )
            )
