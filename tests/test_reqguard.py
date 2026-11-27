"""Комплексные автотесты ReqGuard: парсер (MD/DOCX/PDF), статический анализатор,
анти-галлюцинационный верификатор цитат, кросс-дедупликация, устойчивость LLM-шлюза и CLI.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from docx import Document as DocxDocument
from fpdf import FPDF
from pydantic import BaseModel

from reqguard.cli import main as cli_main
from reqguard.config import LLMSettings, detect_agent_version
from reqguard.llm_client import ResilientLLMClient, extract_json_payload
from reqguard.models import AtomicDefect, SetDefect, SourceSpan
from reqguard.parser import parse_document
from reqguard.pipeline import run_audit_pipeline
from reqguard.reporter_md import render_markdown_report
from reqguard.structural_scanner import run_structural_scan
from reqguard.verifier import build_verified_audit_report, verify_and_anchor_span


SAMPLE_SPEC_MD = """# Тестовое ТЗ: Лимиты по карте

## Часть A. Функционал

### Цели
Сообщать клиенту о достижении порога в момент покупки.

### Функциональные требования
* **Ф-1.** Клиент задаёт порог от 1 000 до 1 000 000 ₸. По умолчанию выключено.
* **Ф-2.** Месяц определяется по дате совершения покупки в Алматы. В расходы входят проведённые покупки. Возврат уменьшает расходы месяца исходной покупки. Переводы исключаются.
* **Ф-3.** Клиент получает push при достижении порога. На карту приходится максимум один такой push за календарный месяц.
* **Ф-4.** При изменении порога накопленная сумма сохраняется. Если она ниже нового порога, следующее достижение вызывает push независимо от истории предыдущей настройки.

### Нефункциональные требования
Сервис передаёт push на отправку в течение минуты после получения события.

### Тестирование требований
заполняются тестировщиками

### Критерии приемки
| Исходные условия | Действие | Ожидаемый результат |
| :--- | :--- | :--- |
| Покупка 31 января в 23:58 на 4 000 ₸ | Провести 1 февраля в 00:03 по Алматы | Февральский счётчик вырос на 4 000 ₸ |

### Ревью требований
| Проверка | Создано | Проверено |
| :--- | :--- | :--- |
| Архитектура согласована | Да / Нет | Да / Нет |
| Риски ИБ отсутствуют | Да / Нет | Да / Нет |

### Блок согласующих
| Подразделение | ФИО | Подпись |
| :--- | :--- | :--- |
| | | |

## Часть B. REST API

### Общие параметры
| Поле | Значение |
| :--- | :--- |
| **Description** | Сохранение порога. Функциональные требования: чтение порога |
| **REST type** | GET |
| **Request URL** | Dev: `/alerts/saveLimit` . Prod: — |
| **CURL** | `curl -G "$BASE/alerts/saveLimit" --data 'cardId=1&amount=50000&enabled=1'` |
| **Swagger** | — |

### Параметры запроса
| Parameter | Format | Mandatory | DB field |
| :--- | :--- | :--- | :--- |
| `amount` | integer | Да | `limit_kzt` |
| `enabled` | integer | Да | `enabled` |

#### Request body
* **Content-Type:** `application/json` .
* Тело не используется.

### Ответ
| Parameter | Format | DB field |
| :--- | :--- | :--- |
| `amount` | string | `limit_kzt` |
| `enabled` | string | `enabled` |

### Статусы
#### 200. Successful — Created
```json
{"result":"ERROR","message":"Проверьте сумму"}
```
#### 400. Unsuccessful — Bad request
```json
{"message":"Некорректный запрос"}
```
#### 403. Unsuccessful — Forbidden
Для чужой карты возвращается 404, как и для отсутствующей карты.
"""


def _find_cyrillic_fonts() -> tuple[Path, Path | None]:
    fonts_dir = Path("C:/Windows/Fonts")
    candidates = [
        ("arial.ttf", "arialbd.ttf"),
        ("calibri.ttf", "calibrib.ttf"),
        ("segoeui.ttf", "segoeuib.ttf"),
        ("tahoma.ttf", "tahomabd.ttf"),
    ]
    for reg, bold in candidates:
        p_reg = fonts_dir / reg
        if p_reg.exists():
            p_bold = fonts_dir / bold
            return p_reg, p_bold if p_bold.exists() else None
    pytest.skip("Не найден кириллический TTF-шрифт в C:/Windows/Fonts")


def _build_spec_pdf(pdf_path: Path, md_text: str) -> None:
    """Генерирует реальный многостраничный кириллический PDF с заголовками и таблицами."""
    reg_font, bold_font = _find_cyrillic_fonts()
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=14)
    pdf.add_page()
    pdf.add_font("SysFont", "", str(reg_font))
    if bold_font:
        pdf.add_font("SysFont", "B", str(bold_font))
    else:
        pdf.add_font("SysFont", "B", str(reg_font))

    lines = md_text.splitlines()
    i = 0
    in_code = False
    while i < len(lines):
        raw = lines[i].rstrip()
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            i += 1
            continue
        if in_code:
            pdf.set_x(pdf.l_margin)
            pdf.set_font("SysFont", "", 9)
            pdf.multi_cell(pdf.epw, 5, stripped)
            i += 1
            continue
        if not stripped or stripped == "---":
            pdf.ln(2)
            i += 1
            continue
        if stripped.startswith("#"):
            hashes = len(stripped) - len(stripped.lstrip("#"))
            title = stripped.lstrip("#").strip()
            size = {1: 16, 2: 14, 3: 12, 4: 11}.get(hashes, 11)
            pdf.set_x(pdf.l_margin)
            pdf.set_font("SysFont", "B", size)
            pdf.ln(2)
            pdf.multi_cell(pdf.epw, 6, title)
            pdf.ln(1)
            i += 1
            continue
        if stripped.startswith("|"):
            tbl_rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                r_txt = lines[i].strip()
                if not all(ch in "|-: " for ch in r_txt):
                    cells = [c.strip().replace("**", "").replace("`", "") for c in r_txt.strip("|").split("|")]
                    tbl_rows.append(cells)
                i += 1
            if tbl_rows:
                col_n = max(len(r) for r in tbl_rows)
                usable_w = pdf.epw
                col_w = usable_w / col_n
                pdf.set_x(pdf.l_margin)
                pdf.set_font("SysFont", "", 8)
                with pdf.table(col_widths=[col_w] * col_n, line_height=5) as table:
                    for r_idx, r_cells in enumerate(tbl_rows):
                        row_obj = table.row()
                        padded = r_cells + [""] * (col_n - len(r_cells))
                        for c_val in padded:
                            row_obj.cell(c_val)
                pdf.set_x(pdf.l_margin)
                pdf.ln(2)
            continue

        clean_line = stripped.replace("**", "").replace("`", "")
        if clean_line.startswith("* "):
            clean_line = "• " + clean_line[2:]
        pdf.set_x(pdf.l_margin)
        pdf.set_font("SysFont", "", 10)
        pdf.multi_cell(pdf.epw, 5, clean_line)
        i += 1

    pdf.output(str(pdf_path))


def test_parse_markdown_and_quote_verifier(tmp_path: Path) -> None:
    md_file = tmp_path / "spec.md"
    md_file.write_text(SAMPLE_SPEC_MD, encoding="utf-8")

    doc = parse_document(md_file)
    assert doc.title == "Тестовое ТЗ: Лимиты по карте"
    assert doc.total_lines > 40
    assert len(doc.tables) >= 5

    # Истинная цитата с намеренно сбитым номером строки (строка 1 вместо реальной) должна найтись
    shifted = SourceSpan(
        line_start=1,
        line_end=1,
        quote="Месяц определяется по дате совершения покупки в Алматы.",
    )
    anchored = verify_and_anchor_span(doc, shifted)
    assert anchored is not None
    assert anchored.verified is True
    assert anchored.line_start > 5
    assert "Месяц определяется по дате совершения покупки" in anchored.quote

    # Выдуманная цитата (галлюцинация LLM) отклоняется
    hallucinated = SourceSpan(
        line_start=10,
        line_end=11,
        quote="Клиент обязан подтверждать изменение лимита через биометрию FaceID и SMS-код.",
    )
    assert verify_and_anchor_span(doc, hallucinated) is None


def test_verifier_rejects_short_single_word_hallucinations(tmp_path: Path) -> None:
    md_file = tmp_path / "spec.md"
    md_file.write_text(SAMPLE_SPEC_MD, encoding="utf-8")
    doc = parse_document(md_file)

    # Одно короткое слово с неверной строкой НЕ должно проходить глобальный поиск!
    for bad_short in ("карта", "порог", "GET", "сумма", "push"):
        cand = SourceSpan(line_start=2, line_end=2, quote=bad_short)
        assert verify_and_anchor_span(doc, cand) is None


def test_parse_real_generated_pdf_with_tables_and_headings(tmp_path: Path) -> None:
    pdf_path = tmp_path / "spec.pdf"
    _build_spec_pdf(pdf_path, SAMPLE_SPEC_MD)

    doc = parse_document(pdf_path)
    assert doc.source_format == "PDF"
    assert doc.total_pages is not None and doc.total_pages >= 1
    assert len(doc.tables) >= 4
    assert any(dl.kind == "heading" for dl in doc.lines)
    assert any(dl.section_path for dl in doc.lines if dl.kind == "text")

    # Проверяем, что структурный анализатор на PDF находит те же ключевые дефекты, что и на Markdown
    scan = run_structural_scan(doc)
    titles = " | ".join(d.title for d in scan.set_defects)
    assert "HTTP GET" in titles
    assert "Да / Нет" in titles
    assert "типов данных" in titles
    assert "секция 200 содержит ответ ERROR, а для ошибок описан статус 400" in titles
    assert any(ad.criterion == "SINGULAR" and ad.doc_id == "Ф-2" for ad in scan.atomic_defects)
    assert not any(ad.criterion == "VERIFIABLE" and "в течение минуты" in ad.evidence[0].quote for ad in scan.atomic_defects)


def test_parse_empty_text_pdf_raises_clear_error(tmp_path: Path) -> None:
    pdf_path = tmp_path / "blank.pdf"
    pdf = FPDF()
    pdf.add_page()
    pdf.output(str(pdf_path))

    with pytest.raises(ValueError, match="не содержит текстового слоя"):
        parse_document(pdf_path)


def test_parse_docx_with_headings_and_tables(tmp_path: Path) -> None:
    docx_path = tmp_path / "bank_spec.docx"
    d = DocxDocument()
    d.add_heading("Спецификация карточных уведомлений", level=1)
    d.add_heading("Функциональные требования", level=2)
    d.add_paragraph("Ф-1. Порог задаётся в диапазоне от 1000 до 500000 тенге.")
    tbl = d.add_table(rows=2, cols=2)
    tbl.rows[0].cells[0].text = "Поле"
    tbl.rows[0].cells[1].text = "Значение"
    tbl.rows[1].cells[0].text = "REST type"
    tbl.rows[1].cells[1].text = "GET"
    d.save(str(docx_path))

    parsed = parse_document(docx_path)
    assert parsed.source_format == "DOCX"
    assert parsed.title == "Спецификация карточных уведомлений"
    assert len(parsed.tables) == 1


def test_structural_scanner_maps_api_tables_to_req_ids(tmp_path: Path) -> None:
    md_file = tmp_path / "spec.md"
    md_file.write_text(SAMPLE_SPEC_MD, encoding="utf-8")
    doc = parse_document(md_file)
    report = run_audit_pipeline(
        md_file,
        LLMSettings(provider="none", api_key="", primary_model="none"),
        allow_structural_only=True,
    )
    type_defect = next(d for d in report.set_defects if "типов данных" in d.title)
    assert len(type_defect.related_req_ids) >= 2


def test_cross_deduplication_eliminates_triple_counting(tmp_path: Path) -> None:
    md_file = tmp_path / "spec.md"
    md_file.write_text(SAMPLE_SPEC_MD, encoding="utf-8")
    doc = parse_document(md_file)
    scan = run_structural_scan(doc)

    # Находим реальные строки GET и 200 ERROR в тестовом документе
    line_get = next(dl.line_no for dl in doc.lines if "**REST type**" in dl.raw_text)
    line_err = next(dl.line_no for dl in doc.lines if '{"result":"ERROR"' in dl.raw_text)

    # Имитируем дублирующие находки от LLM в Фазе 1 (Atomic) и составную находку в Фазе 2 (Set)
    llm_atomic_dup = AtomicDefect(
        defect_id="DEF-A99",
        req_id="REQ-15",
        criterion="FEASIBLE",
        severity="CRITICAL",
        problem="Использование HTTP-метода GET для мутирующей операции сохранения порога нарушает RFC.",
        impact="Кэширование и утечка данных в URL.",
        recommendation="Перевести на POST/PUT.",
        evidence=[SourceSpan(line_start=line_get, line_end=line_get, quote="| **REST type** | GET |")],
        origin="LLM",
    )
    llm_set_compound = SetDefect(
        defect_id="DEF-S99",
        related_req_ids=["REQ-15"],
        criterion="INTERFACE_INTEGRITY",
        severity="MAJOR",
        title="Некорректный HTTP-метод GET и 200 OK при ошибке валидации",
        problem="Сохранение настроек через GET и возврат 200 OK со строкой ERROR при ошибке суммы.",
        impact="Сломан контракт API.",
        question_for_analyst="Когда переведём на POST и 400?",
        recommendation="Использовать POST и 400 Bad Request.",
        evidence=[
            SourceSpan(line_start=line_get, line_end=line_get, quote="| **REST type** | GET |"),
            SourceSpan(line_start=line_err, line_end=line_err, quote='{"result":"ERROR","message":"Проверьте сумму"}'),
        ],
        origin="LLM",
    )

    from reqguard.models import RunPassport

    passport = RunPassport(
        document_title=doc.title,
        source_file=doc.source_path.name,
        source_format=doc.source_format,
        source_sha256="deadbeef",
        total_lines=doc.total_lines,
        run_timestamp="2026-11-25 18:00:00",
        provider="gemini",
        model_name="test-model",
        agent_version="v1.0.0",
        duration_sec=1.0,
    )
    report = build_verified_audit_report(
        doc=doc,
        passport=passport,
        requirements=scan.baseline_requirements,
        candidate_atomic=scan.atomic_defects + [llm_atomic_dup],
        candidate_set=scan.set_defects + [llm_set_compound],
    )

    # Дублирующий Atomic (GET) и составной Set (GET + 200 ERROR) должны быть поглощены!
    assert not any("HTTP-метода GET" in ad.problem for ad in report.atomic_defects)
    get_set_defects = [sd for sd in report.set_defects if "HTTP GET" in sd.title]
    assert len(get_set_defects) == 1
    assert get_set_defects[0].origin == "HYBRID"
    # Легитимный SINGULAR у Ф-2 должен остаться нетронутым
    assert any(ad.criterion == "SINGULAR" and ad.doc_id == "Ф-2" for ad in report.atomic_defects)


def test_deduplicator_keeps_mutating_get_separate_from_missing_read_endpoint(tmp_path: Path) -> None:
    md_file = tmp_path / "spec.md"
    md_file.write_text(SAMPLE_SPEC_MD, encoding="utf-8")
    doc = parse_document(md_file)
    scan = run_structural_scan(doc)
    line_desc = next(dl.line_no for dl in doc.lines if "**Description**" in dl.raw_text)
    line_get = next(dl.line_no for dl in doc.lines if "**REST type**" in dl.raw_text)

    # Пропуск описанного чтения — утверждение о полноте (COMPLETE_SET), как его выдаёт промпт фазы 2 после E6.
    missing_read_llm = SetDefect(
        defect_id="DEF-S88",
        related_req_ids=["REQ-15"],
        criterion="COMPLETE_SET",
        severity="CRITICAL",
        title="Отсутствие REST API метода для чтения настроек порога и расходов",
        problem="В Description указано чтение порога, но в API описан только saveLimit через GET.",
        impact="UI не сможет загрузить текущие настройки.",
        question_for_analyst="Какой эндпоинт возвращает текущие настройки?",
        recommendation="Добавить GET /alerts/getLimit.",
        evidence=[
            SourceSpan(
                line_start=line_desc,
                line_end=line_get,
                quote="| **Description** | Сохранение порога. Функциональные требования: чтение порога |\n| **REST type** | GET |",
            )
        ],
        origin="LLM",
    )

    from reqguard.models import RunPassport

    passport = RunPassport(
        document_title=doc.title,
        source_file=doc.source_path.name,
        source_format=doc.source_format,
        source_sha256="deadbeef",
        total_lines=doc.total_lines,
        run_timestamp="2026-11-25 18:00:00",
        provider="gemini",
        model_name="test-model",
        agent_version="v1.0.0",
        duration_sec=1.0,
    )
    report = build_verified_audit_report(
        doc=doc,
        passport=passport,
        requirements=scan.baseline_requirements,
        candidate_atomic=scan.atomic_defects,
        candidate_set=scan.set_defects + [missing_read_llm],
    )
    titles = [sd.title for sd in report.set_defects]
    assert any("HTTP GET" in t for t in titles)
    assert any("чтения настроек" in t for t in titles)


def test_llm_gateway_fallback_on_404_503_timeout_and_thinking_400() -> None:
    raw = 'Ответ модели:\n```json\n{"requirements": [], "atomic_defects": [],}\n```'
    assert extract_json_payload(raw) == {"requirements": [], "atomic_defects": []}

    settings = LLMSettings(
        provider="gemini",
        api_key="test-key-123",
        primary_model="gemini-retired-404",
        fallback_models=["gemini-busy-503", "gemini-slow-timeout", "gemini-3.6-flash"],
        base_url="https://generativelanguage.googleapis.com/v1beta",
        max_retries=2,
        thinking_budget=1536,
    )
    client = ResilientLLMClient(settings)
    call_log: list[tuple[str, bool]] = []

    def fake_post(self_client, url: str, **kwargs):
        has_thinking = "thinkingConfig" in kwargs.get("json", {}).get("generationConfig", {})
        call_log.append((url, has_thinking))
        req = httpx.Request("POST", url)
        if "gemini-retired-404" in url:
            return httpx.Response(404, json={"error": {"message": "Model not found"}}, request=req)
        if "gemini-busy-503" in url:
            return httpx.Response(503, json={"error": {"message": "High demand"}}, request=req)
        if "gemini-slow-timeout" in url:
            raise httpx.ReadTimeout("Timed out", request=req)
        if "gemini-3.6-flash" in url and has_thinking:
            return httpx.Response(400, json={"error": {"message": "thinkingConfig not supported"}}, request=req)
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]},
            request=req,
        )

    class DummyResp(BaseModel):
        ok: bool

    statuses: list[str] = []
    with patch.object(httpx.Client, "post", new=fake_post), patch("reqguard.llm_client.time.sleep"):
        out = client.generate_structured("sys", "usr", DummyResp, status_cb=statuses.append)
        assert out.ok is True
        assert client.last_used_model == "gemini-3.6-flash"
        # 404 -> 503 -> Timeout -> 400 (with thinking) -> 200 (without thinking)
        assert len(call_log) == 5
        assert any("Таймаут" in s for s in statuses)


def test_pipeline_graceful_degradation_when_pass2_fails(tmp_path: Path) -> None:
    md_file = tmp_path / "spec.md"
    md_file.write_text(SAMPLE_SPEC_MD, encoding="utf-8")

    call_num = {"n": 0}

    def fake_post(self_client, url: str, **kwargs):
        call_num["n"] += 1
        req = httpx.Request("POST", url)
        if call_num["n"] == 1:
            p1 = {
                "requirements": [
                    {
                        "temp_id": "R1",
                        "doc_id": "Ф-1",
                        "category": "FUNCTIONAL",
                        "title": "Порог уведомлений",
                        "is_compound": False,
                        "sub_rules": [],
                        "line_start": 9,
                        "line_end": 9,
                        "quote": "Клиент задаёт порог от 1 000 до 1 000 000 ₸.",
                    }
                ],
                "atomic_defects": [],
            }
            return httpx.Response(
                200,
                json={"candidates": [{"content": {"parts": [{"text": json.dumps(p1, ensure_ascii=False)}]}}]},
                request=req,
            )
        return httpx.Response(503, json={"error": {"message": "Overloaded"}}, request=req)

    settings = LLMSettings(
        provider="gemini",
        api_key="valid-key",
        primary_model="gemini-3.6-flash",
        fallback_models=[],
        max_retries=1,
    )
    logs: list[str] = []
    with patch.object(httpx.Client, "post", new=fake_post), patch("reqguard.llm_client.time.sleep"):
        report = run_audit_pipeline(md_file, settings, progress_cb=logs.append)

    assert any("Предупреждение: Фаза 2 LLM завершилась ошибкой" in m for m in logs)
    assert report.summary.total_requirements >= 10
    assert report.summary.critical_count >= 2


def test_detect_agent_version_independent_of_cwd(tmp_path: Path) -> None:
    from reqguard import __version__

    orig_cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        ver = detect_agent_version()
        assert ver.startswith(f"v{__version__}")
    finally:
        os.chdir(orig_cwd)


def test_markdown_report_has_no_broken_bold_markers(tmp_path: Path) -> None:
    md_file = tmp_path / "spec.md"
    md_file.write_text(SAMPLE_SPEC_MD, encoding="utf-8")
    report = run_audit_pipeline(
        md_file,
        LLMSettings(provider="none", api_key="", primary_model="none"),
        allow_structural_only=True,
    )
    md_out = render_markdown_report(report)
    sec2 = md_out.split("## 3. Проверка каждого требования")[0]
    assert "Ф-1.**" not in sec2
    assert "Ф-2.**" not in sec2
    assert "Цитата: «Ф-1." in sec2


def test_end_to_end_hybrid_pipeline_filters_hallucinations(tmp_path: Path) -> None:
    md_file = tmp_path / "spec.md"
    md_file.write_text(SAMPLE_SPEC_MD, encoding="utf-8")

    fake_pass1 = {
        "requirements": [
            {
                "temp_id": "R1",
                "doc_id": "Ф-2",
                "category": "FUNCTIONAL",
                "title": "Правила расчёта месячных расходов и возвратов",
                "is_compound": True,
                "sub_rules": ["Дата по Алматы", "Проведённые покупки", "Возвраты", "Переводы"],
                "line_start": 10,
                "line_end": 10,
                "quote": "Месяц определяется по дате совершения покупки в Алматы.",
            }
        ],
        "atomic_defects": [
            {
                "temp_req_id": "R1",
                "doc_id": "Ф-2",
                "criterion": "UNAMBIGUOUS",
                "severity": "CRITICAL",
                "problem": "ГАЛЛЮЦИНАЦИЯ: выдуманное замечание про блокчейн-токены.",
                "impact": "Нет",
                "recommendation": "Нет",
                "suggested_fix": None,
                "line_start": 99,
                "line_end": 99,
                "quote": "Кэшбэк начисляется в криптовалюте USDT каждую пятницу.",
            },
        ],
    }

    fake_pass2 = {
        "set_defects": [
            {
                "related_req_ids": ["REQ-03"],
                "criterion": "VERIFIABLE_SET",
                "severity": "CRITICAL",
                "title": "Противоречие между Ф-2 (дата покупки) и критерием приёмки (дата проведения)",
                "problem": "По Ф-2 месяц определяется по дате покупки (31 января -> январь), а в таблице приёмки покупка от 31 января, проведённая 1 февраля, увеличивает февральский счётчик.",
                "impact": "Разработчик и тестировщик реализуют противоположную логику пограничных дат месяца.",
                "question_for_analyst": "По какой дате относится операция к месяцу: по дате авторизации (покупки) или по дате финансового проведения?",
                "recommendation": "Привести таблицу приёмки и Ф-2 к единому атрибуту даты.",
                "evidence": [
                    {
                        "line_start": 10,
                        "line_end": 10,
                        "quote": "Месяц определяется по дате совершения покупки в Алматы.",
                    },
                    {
                        "line_start": 22,
                        "line_end": 22,
                        "quote": "Покупка 31 января в 23:58 на 4 000 ₸ | Провести 1 февраля в 00:03 по Алматы | Февральский счётчик вырос на 4 000 ₸",
                    },
                ],
            },
        ]
    }

    responses = [fake_pass1, fake_pass2]
    call_idx = {"i": 0}

    def fake_call_provider(self_client, model_name: str, system_prompt: str, user_prompt: str) -> str:
        res = responses[call_idx["i"]]
        call_idx["i"] += 1
        return json.dumps(res, ensure_ascii=False)

    settings = LLMSettings(provider="gemini", api_key="valid-test-key", primary_model="gemini-3.6-flash")
    with patch.object(ResilientLLMClient, "_call_provider", new=fake_call_provider):
        report = run_audit_pipeline(md_file, settings)

    assert report.passport.hallucinations_filtered == 1
    assert not any("USDT" in d.problem or "криптовалют" in d.problem for d in report.atomic_defects)
    assert report.summary.critical_count >= 3
    assert report.summary.can_start_development is False
    assert 5 <= report.summary.readiness_score < 60


def test_cli_structural_run_on_official_case_md_and_pdf(tmp_path: Path) -> None:
    official_doc = Path(__file__).resolve().parent.parent / "docs" / "requirements.md"
    if not official_doc.exists():
        pytest.skip("Локальная копия _official_task/docs/requirements.md не найдена")

    out_md = tmp_path / "official_md.md"
    assert cli_main([str(official_doc), str(out_md), "--structural-only", "--with-html"]) == 3
    md_content = out_md.read_text(encoding="utf-8")
    assert "HTTP GET" in md_content
    assert out_md.with_suffix(".html").exists()

    # Генерируем PDF прямо из официального requirements.md и прогоняем через тот же CLI!
    pdf_doc = tmp_path / "official_requirements.pdf"
    _build_spec_pdf(pdf_doc, official_doc.read_text(encoding="utf-8"))
    out_pdf_md = tmp_path / "official_pdf.md"
    assert cli_main([str(pdf_doc), str(out_pdf_md), "--structural-only"]) == 3
    pdf_report = out_pdf_md.read_text(encoding="utf-8")
    assert "HTTP GET" in pdf_report
    assert "типов данных" in pdf_report
    assert "Да / Нет" in pdf_report
    assert "секция 200 содержит ответ ERROR, а для ошибок описан статус 400" in pdf_report


def test_deduplicator_merges_overlapping_200_400_status_defects(tmp_path: Path) -> None:
    md_file = tmp_path / "spec.md"
    md_file.write_text(SAMPLE_SPEC_MD, encoding="utf-8")
    doc = parse_document(md_file)
    scan = run_structural_scan(doc)
    line_err = next(dl.line_no for dl in doc.lines if '{"result":"ERROR"' in dl.raw_text)
    line_400 = next(dl.line_no for dl in doc.lines if '{"message":"Некорректный запрос"}' in dl.raw_text)

    llm_status_dup = SetDefect(
        defect_id="DEF-S77",
        related_req_ids=["REQ-19"],
        criterion="INTERFACE_INTEGRITY",
        severity="CRITICAL",
        title="Несогласованность HTTP-кодов ответа 200 и 400 при ошибке",
        problem="Для ошибки суммы возвращается статус 200 со строкой ERROR, хотя для невалидных параметров задан 400.",
        impact="Сломанная обработка ошибок на клиенте.",
        question_for_analyst="Какой статус возвращать при ошибке?",
        recommendation="Возвращать 400 Bad Request.",
        evidence=[
            SourceSpan(line_start=line_err, line_end=line_err, quote='{"result":"ERROR","message":"Проверьте сумму"}'),
            SourceSpan(line_start=line_400, line_end=line_400, quote='{"message":"Некорректный запрос"}'),
        ],
        origin="LLM",
    )

    from reqguard.models import RunPassport

    passport = RunPassport(
        document_title=doc.title,
        source_file=doc.source_path.name,
        source_format=doc.source_format,
        source_sha256="deadbeef",
        total_lines=doc.total_lines,
        run_timestamp="2026-11-25 18:30:00",
        provider="gemini",
        model_name="test-model",
        agent_version="v1.2.0",
        duration_sec=1.0,
    )
    report = build_verified_audit_report(
        doc=doc,
        passport=passport,
        requirements=scan.baseline_requirements,
        candidate_atomic=scan.atomic_defects,
        candidate_set=scan.set_defects + [llm_status_dup],
    )
    status_defs = [sd for sd in report.set_defects if "200" in sd.title and "400" in sd.title]
    assert len(status_defs) == 1
    assert status_defs[0].origin == "HYBRID"


def test_cli_missing_positional_args_without_serve_exits_with_2() -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main([])
    assert exc_info.value.code == 2


def test_web_ui_server_endpoints_and_analysis_job(tmp_path: Path) -> None:
    import base64
    import threading
    import time
    from reqguard.web_ui import create_web_server

    server = create_web_server(host="127.0.0.1", port=0, base_dir=tmp_path)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        base_url = f"http://127.0.0.1:{port}"
        r_home = httpx.get(f"{base_url}/", timeout=5)
        assert r_home.status_code == 200
        assert "ReqGuard Studio" in r_home.text

        r_latest = httpx.get(f"{base_url}/api/latest", timeout=5)
        assert r_latest.status_code == 200
        assert r_latest.json()["available"] is False

        b64 = base64.b64encode(SAMPLE_SPEC_MD.encode("utf-8")).decode("ascii")
        r_start = httpx.post(
            f"{base_url}/api/analyze",
            json={"filename": "spec.md", "content_base64": b64, "structural_only": True},
            timeout=5,
        )
        assert r_start.status_code == 202
        job_id = r_start.json()["job_id"]

        job_data = {}
        for _ in range(40):
            r_job = httpx.get(f"{base_url}/api/jobs/{job_id}", timeout=5)
            assert r_job.status_code == 200
            job_data = r_job.json()
            if job_data["status"] in ("done", "error"):
                break
            time.sleep(0.1)

        assert job_data["status"] == "done", job_data.get("error")
        assert job_data["report"]["summary"]["critical_count"] >= 2
        assert "## 1. Паспорт прогона" in job_data["markdown"]
        assert "ReqGuard Отчёт" in job_data["html"]
    finally:
        server.shutdown()
        server.server_close()


def test_deduplicator_preserves_f2_acceptance_conflict_alongside_goal_bpmn_conflict() -> None:
    from reqguard.models import SetDefect, SourceSpan
    from reqguard.verifier import _deduplicate_set

    goal_bpmn_f2 = SetDefect(
        defect_id="TMP-1",
        title="Конфликт между разделом Цели, BPMN-схемой и Ф-2 по срокам начисления кэшбэка",
        criterion="CONSISTENT",
        severity="CRITICAL",
        related_req_ids=["Цели", "BPMN", "Ф-2"],
        problem="В Целях указано моментально, в BPMN через 3 дня, а в Ф-2 — до 5 числа следующего месяца.",
        impact="Невозможно однозначно реализовать начисление кэшбэка.",
        question_for_analyst="Какой срок начисления является целевым?",
        recommendation="Согласовать единый срок начисления кэшбэка во всех разделах.",
        origin="LLM",
        evidence=[
            SourceSpan(section_path="Цели", line_start=12, line_end=12, quote="моментально", verified=True),
            SourceSpan(section_path="BPMN", line_start=25, line_end=25, quote="через 3 дня", verified=True),
            SourceSpan(section_path="Ф-2", line_start=38, line_end=38, quote="до 5 числа", verified=True),
        ],
    )
    f2_acceptance = SetDefect(
        defect_id="TMP-2",
        title="Противоречие между Ф-2 и Критериями приёмки по дате выплаты кэшбэка",
        criterion="CONSISTENT",
        severity="CRITICAL",
        related_req_ids=["Ф-2", "Приёмка"],
        problem="В Ф-2 срок выплаты — 5 число следующего месяца, а в Приёмке — 31 января / 1 февраля.",
        impact="Приёмочное тестирование не сойдётся с бизнес-логикой.",
        question_for_analyst="Какая дата выплаты приоритетна: 5-е число или конец месяца?",
        recommendation="Устранить расхождение между Ф-2 и разделом Критерии приёмки.",
        origin="LLM",
        evidence=[
            SourceSpan(section_path="Ф-2", line_start=38, line_end=38, quote="до 5 числа", verified=True),
            SourceSpan(section_path="Критерии приёмки", line_start=90, line_end=90, quote="31 января / 1 февраля", verified=True),
        ],
    )
    res = _deduplicate_set([goal_bpmn_f2, f2_acceptance])
    assert len(res) == 2
