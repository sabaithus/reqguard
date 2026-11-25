"""Мультиформатный парсер документов (Markdown, PDF, DOCX, TXT) в пронумерованный AST."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

LineKind = Literal["heading", "table_row", "table_sep", "code", "list_item", "text", "empty"]

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
TABLE_SEP_RE = re.compile(r"^\|?\s*:?-{2,}:?(?:\s*\|\s*:?-{2,}:?)+\s*\|?\s*$")
LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+(.+)$")


@dataclass(slots=True)
class DocumentLine:
    line_no: int
    raw_text: str
    section_path: str
    kind: LineKind
    page: int | None = None


@dataclass(slots=True)
class ParsedTableRow:
    line_no: int
    cells: list[str]
    raw_text: str
    page: int | None = None


@dataclass(slots=True)
class ParsedTable:
    line_start: int
    line_end: int
    section_path: str
    headers: list[str]
    rows: list[ParsedTableRow] = field(default_factory=list)
    page: int | None = None


@dataclass(slots=True)
class ParsedDocument:
    title: str
    source_path: Path
    source_format: str
    sha256: str
    lines: list[DocumentLine]
    tables: list[ParsedTable]
    total_pages: int | None = None

    @property
    def total_lines(self) -> int:
        return len(self.lines)

    def get_line(self, line_no: int) -> DocumentLine | None:
        if 1 <= line_no <= len(self.lines):
            return self.lines[line_no - 1]
        return None

    def get_span_text(self, line_start: int, line_end: int) -> str:
        start = max(1, line_start)
        end = min(len(self.lines), max(start, line_end))
        return "\n".join(self.lines[i - 1].raw_text for i in range(start, end + 1)).strip()

    def render_numbered_text(self) -> str:
        """Формирует пронумерованный по строкам текст документа для точных ссылок LLM."""
        out: list[str] = []
        width = max(3, len(str(len(self.lines))))
        for dl in self.lines:
            if dl.page is not None:
                prefix = f"[P{dl.page:02d}:L{dl.line_no:0{width}d}]"
            else:
                prefix = f"[L{dl.line_no:0{width}d}]"
            out.append(f"{prefix} {dl.raw_text}")
        return "\n".join(out)


def _split_md_table_cells(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _build_ast_from_raw_lines(
    raw_items: list[tuple[str, int | None]],
    fallback_title: str,
    source_path: Path,
    source_format: str,
    sha256: str,
    total_pages: int | None = None,
) -> ParsedDocument:
    heading_stack: list[tuple[int, str]] = []
    doc_title: str | None = None
    in_code_block = False
    doc_lines: list[DocumentLine] = []

    for idx, (raw_line, page_no) in enumerate(raw_items, start=1):
        line = raw_line.rstrip("\r\n")
        stripped = line.strip()

        if stripped.startswith("```"):
            in_code_block = not in_code_block
            section_path = " → ".join(h[1] for h in heading_stack)
            doc_lines.append(
                DocumentLine(
                    line_no=idx,
                    raw_text=line,
                    section_path=section_path,
                    kind="code",
                    page=page_no,
                )
            )
            continue

        if in_code_block:
            section_path = " → ".join(h[1] for h in heading_stack)
            doc_lines.append(
                DocumentLine(
                    line_no=idx,
                    raw_text=line,
                    section_path=section_path,
                    kind="code",
                    page=page_no,
                )
            )
            continue

        if not stripped:
            section_path = " → ".join(h[1] for h in heading_stack)
            doc_lines.append(
                DocumentLine(
                    line_no=idx,
                    raw_text=line,
                    section_path=section_path,
                    kind="empty",
                    page=page_no,
                )
            )
            continue

        h_match = HEADING_RE.match(stripped)
        if h_match:
            level = len(h_match.group(1))
            h_text = h_match.group(2).strip()
            if doc_title is None and level == 1:
                doc_title = h_text
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, h_text))
            section_path = " → ".join(h[1] for h in heading_stack)
            doc_lines.append(
                DocumentLine(
                    line_no=idx,
                    raw_text=line,
                    section_path=section_path,
                    kind="heading",
                    page=page_no,
                )
            )
            continue

        section_path = " → ".join(h[1] for h in heading_stack)
        if TABLE_SEP_RE.match(stripped):
            kind: LineKind = "table_sep"
        elif stripped.startswith("|") and "|" in stripped[1:]:
            kind = "table_row"
        elif LIST_ITEM_RE.match(stripped):
            kind = "list_item"
        else:
            kind = "text"
            if doc_title is None and idx <= 5 and len(stripped) < 120:
                doc_title = stripped

        doc_lines.append(
            DocumentLine(
                line_no=idx,
                raw_text=line,
                section_path=section_path,
                kind=kind,
                page=page_no,
            )
        )

    tables = _extract_tables(doc_lines)
    return ParsedDocument(
        title=doc_title or fallback_title,
        source_path=source_path,
        source_format=source_format,
        sha256=sha256,
        lines=doc_lines,
        tables=tables,
        total_pages=total_pages,
    )


def _extract_tables(doc_lines: list[DocumentLine]) -> list[ParsedTable]:
    tables: list[ParsedTable] = []
    i = 0
    n = len(doc_lines)
    while i < n:
        dl = doc_lines[i]
        if dl.kind == "table_row":
            block: list[DocumentLine] = []
            while i < n and doc_lines[i].kind in ("table_row", "table_sep"):
                block.append(doc_lines[i])
                i += 1
            row_lines = [b for b in block if b.kind == "table_row"]
            if row_lines:
                headers = _split_md_table_cells(row_lines[0].raw_text)
                parsed_rows = [
                    ParsedTableRow(
                        line_no=r.line_no,
                        cells=_split_md_table_cells(r.raw_text),
                        raw_text=r.raw_text,
                        page=r.page,
                    )
                    for r in row_lines[1:]
                ]
                tables.append(
                    ParsedTable(
                        line_start=block[0].line_no,
                        line_end=block[-1].line_no,
                        section_path=block[0].section_path,
                        headers=headers,
                        rows=parsed_rows,
                        page=block[0].page,
                    )
                )
        else:
            i += 1
    return tables


PDF_CANONICAL_HEADING_RE = re.compile(
    r"^(?:Часть\s+[A-ZА-Я0-9]+\.|Раздел\s+\d+|\d{3}\.\s+(?:Successful|Unsuccessful)|"
    r"Общие сведения|Цели|Архитектура решения|Дизайн процесса UI|Требования к .+|"
    r"BPMN.*|Функциональные требования|Sequence Diagram|Нефункциональные требования|"
    r"Критерии при[её]мки|Ревью требований|Блок согласующих|Общие параметры|"
    r"Логика работы метода|Особые правила|Параметры запроса|Path-параметры|"
    r"Query-параметры|Request body|Ответ|Статусы)$",
    re.IGNORECASE,
)
PDF_NEW_BLOCK_START_RE = re.compile(
    r"^(?:[-*+•]\s+|\d+[.)]\s+|(?:Ф|НФ|Т|И|Б|FR|NFR|REQ|BR|UC|US|AC|КП)-\d+|\d{3}\.\s+|\{|\[|\})",
    re.IGNORECASE,
)


def _read_pdf_lines_pypdf_fallback(path: Path) -> tuple[list[tuple[str, int | None]], int]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    total_pages = len(reader.pages)
    if total_pages == 0:
        raise ValueError(f"PDF-файл '{path.name}' не содержит страниц.")

    raw_items: list[tuple[str, int | None]] = []
    extracted_chars = 0
    for page_idx, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        extracted_chars += len(text.strip())
        for line in text.splitlines():
            raw_items.append((line, page_idx))

    if extracted_chars == 0:
        raise ValueError(
            f"PDF-файл '{path.name}' не содержит текстового слоя (вероятно, это скан без OCR). "
            "Передайте PDF с текстовым слоем, DOCX или Markdown."
        )
    return raw_items, total_pages


def _read_pdf_lines(path: Path) -> tuple[list[tuple[str, int | None]], int]:
    try:
        import pdfplumber
    except ImportError:
        return _read_pdf_lines_pypdf_fallback(path)

    try:
        with pdfplumber.open(str(path)) as pdf:
            total_pages = len(pdf.pages)
            if total_pages == 0:
                raise ValueError(f"PDF-файл '{path.name}' не содержит страниц.")

            all_sizes: list[float] = []
            page_data: list[tuple[int, list, list[dict]]] = []
            extracted_chars = 0

            for page_idx, page in enumerate(pdf.pages, start=1):
                tables = page.find_tables() or []
                words = page.extract_words(extra_attrs=["fontname", "size"]) or []
                table_bboxes_tmp = [t.bbox for t in tables]
                for w in words:
                    wx = (float(w["x0"]) + float(w["x1"])) / 2.0
                    wy = (float(w["top"]) + float(w["bottom"])) / 2.0
                    in_tbl = any(
                        (bx0 - 2.0) <= wx <= (bx1 + 2.0) and (btop - 2.0) <= wy <= (bbot + 2.0)
                        for bx0, btop, bx1, bbot in table_bboxes_tmp
                    )
                    sz = w.get("size")
                    if not in_tbl and isinstance(sz, (int, float)) and sz > 0:
                        all_sizes.append(round(float(sz) * 2.0) / 2.0)
                page_text_len = sum(len(w.get("text", "").strip()) for w in words)
                for t in tables:
                    for row in t.extract() or []:
                        page_text_len += sum(len((c or "").strip()) for c in row)
                extracted_chars += page_text_len
                page_data.append((page_idx, tables, words))

            if extracted_chars == 0:
                raise ValueError(
                    f"PDF-файл '{path.name}' не содержит текстового слоя (вероятно, это скан без OCR). "
                    "Передайте PDF с текстовым слоем, DOCX или Markdown."
                )

            if all_sizes:
                from collections import Counter

                body_size = Counter(all_sizes).most_common(1)[0][0]
            else:
                body_size = 10.0

            raw_items: list[tuple[str, int | None]] = []

            for page_idx, tables, words in page_data:
                table_bboxes = [t.bbox for t in tables]

                def _in_table(w: dict) -> bool:
                    wx = (float(w["x0"]) + float(w["x1"])) / 2.0
                    wy = (float(w["top"]) + float(w["bottom"])) / 2.0
                    for bx0, btop, bx1, bbot in table_bboxes:
                        if (bx0 - 2.0) <= wx <= (bx1 + 2.0) and (btop - 2.0) <= wy <= (bbot + 2.0):
                            return True
                    return False

                outside_words = [w for w in words if not _in_table(w)]
                # Группировка слов по строкам (допуск по vertical top = 3.0 pt)
                outside_words.sort(key=lambda w: (round(float(w["top"]) / 3.0), float(w["x0"])))
                text_lines: list[list[dict]] = []
                for w in outside_words:
                    if not text_lines:
                        text_lines.append([w])
                        continue
                    prev_top = float(text_lines[-1][0]["top"])
                    if abs(float(w["top"]) - prev_top) <= 3.0:
                        text_lines[-1].append(w)
                    else:
                        text_lines.append([w])

                # Элементы страницы: (top, bottom, kind, payload)
                blocks: list[tuple[float, float, str, object]] = []
                for t in tables:
                    extracted = t.extract() or []
                    cleaned_rows: list[list[str]] = []
                    for r in extracted:
                        cells = [" ".join((c or "").split()) for c in r]
                        if any(cells) or len(cells) >= 2:
                            cleaned_rows.append(cells)
                    if cleaned_rows:
                        blocks.append((float(t.bbox[1]), float(t.bbox[3]), "table", cleaned_rows))

                for line_words in text_lines:
                    line_words.sort(key=lambda w: float(w["x0"]))
                    text = " ".join(w["text"] for w in line_words).strip()
                    if not text:
                        continue
                    top_v = min(float(w["top"]) for w in line_words)
                    bot_v = max(float(w["bottom"]) for w in line_words)
                    sizes = [float(w.get("size", body_size)) for w in line_words]
                    avg_size = sum(sizes) / len(sizes)
                    bold_words = sum(
                        1
                        for w in line_words
                        if any(
                            b in str(w.get("fontname", "")).lower()
                            for b in ("bold", "demi", "heavy", "black", "medium")
                        )
                    )
                    is_bold = bold_words >= max(1, int(len(line_words) * 0.7))
                    blocks.append(
                        (
                            top_v,
                            bot_v,
                            "text",
                            {
                                "text": text,
                                "avg_size": avg_size,
                                "is_bold": is_bold,
                                "height": max(6.0, bot_v - top_v),
                            },
                        )
                    )

                blocks.sort(key=lambda b: b[0])

                # Сборка строк с распознаванием заголовков и склейкой перенесённых строк абзаца
                prev_text_meta: dict | None = None
                for top_v, bot_v, b_kind, payload in blocks:
                    if b_kind == "table":
                        prev_text_meta = None
                        rows = payload  # type: ignore[assignment]
                        col_count = max(len(r) for r in rows)
                        for r_idx, row in enumerate(rows):
                            padded = row + [""] * (col_count - len(row))
                            raw_items.append(("| " + " | ".join(padded) + " |", page_idx))
                            if r_idx == 0:
                                sep = "| " + " | ".join(["---"] * col_count) + " |"
                                raw_items.append((sep, page_idx))
                        raw_items.append(("", page_idx))
                        continue

                    info = payload  # type: ignore[assignment]
                    text = str(info["text"])
                    avg_size = float(info["avg_size"])
                    is_bold = bool(info["is_bold"])
                    height = float(info["height"])

                    is_req_line = bool(
                        re.match(
                            r"^(?:[-*+•]\s*)?(?:Ф|НФ|Т|И|Б|FR|NFR|REQ|BR|UC|US|AC|КП)-\d+",
                            text,
                            re.IGNORECASE,
                        )
                    )
                    heading_level = 0
                    if not is_req_line:
                        if avg_size >= body_size + 3.5:
                            heading_level = 1
                        elif avg_size >= body_size + 1.8:
                            heading_level = 2
                        elif avg_size >= body_size + 1.4:
                            heading_level = 3
                        elif (
                            is_bold
                            and len(text) < 90
                            and not text.startswith(("{", "[", "|"))
                            and not text.endswith(".")
                        ):
                            heading_level = 3
                        elif PDF_CANONICAL_HEADING_RE.match(text):
                            heading_level = 2 if text.lower().startswith(("часть ", "раздел ")) else 3

                    if heading_level > 0:
                        prev_text_meta = None
                        raw_items.append((f"{'#' * heading_level} {text}", page_idx))
                        continue

                    # Проверяем, можно ли склеить с предыдущей строкой того же абзаца/требования
                    if (
                        prev_text_meta is not None
                        and not PDF_NEW_BLOCK_START_RE.match(text)
                        and not prev_text_meta["text"].endswith(("}", "]", "|"))
                        and (top_v - prev_text_meta["bottom"]) <= 1.7 * prev_text_meta["height"]
                    ):
                        last_txt, last_pg = raw_items[-1]
                        raw_items[-1] = (f"{last_txt} {text}", last_pg)
                        prev_text_meta = {
                            "text": raw_items[-1][0],
                            "bottom": bot_v,
                            "height": height,
                        }
                    else:
                        raw_items.append((text, page_idx))
                        prev_text_meta = {
                            "text": text,
                            "bottom": bot_v,
                            "height": height,
                        }

            return raw_items, total_pages
    except ValueError:
        raise
    except Exception:
        return _read_pdf_lines_pypdf_fallback(path)


def _read_docx_lines(path: Path) -> list[tuple[str, int | None]]:
    from docx import Document as DocxDocument
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = DocxDocument(str(path))
    raw_items: list[tuple[str, int | None]] = []

    for child in doc.element.body.iterchildren():
        if isinstance(child, CT_P):
            para = Paragraph(child, doc)
            text = para.text.strip()
            style_name = (para.style.name or "").lower() if para.style else ""
            if text and "heading" in style_name:
                digits = "".join(ch for ch in style_name if ch.isdigit())
                level = min(6, max(1, int(digits))) if digits else 2
                raw_items.append((f"{'#' * level} {text}", None))
            else:
                for subline in (para.text or "").splitlines() or [""]:
                    raw_items.append((subline, None))
        elif isinstance(child, CT_Tbl):
            tbl = Table(child, doc)
            if not tbl.rows:
                continue
            for r_idx, row in enumerate(tbl.rows):
                cells = [" ".join(c.text.split()) for c in row.cells]
                raw_items.append(("| " + " | ".join(cells) + " |", None))
                if r_idx == 0:
                    sep = "| " + " | ".join(["---"] * len(cells)) + " |"
                    raw_items.append((sep, None))
            raw_items.append(("", None))
    return raw_items


def parse_document(path: str | Path) -> ParsedDocument:
    """Читает входной документ (.md, .pdf, .docx, .txt) и строит индексированный AST."""
    file_path = Path(path).resolve()
    if not file_path.exists():
        raise FileNotFoundError(f"Входной файл не найден: {file_path}")
    if not file_path.is_file():
        raise ValueError(f"Ожидался путь к файлу, получен каталог: {file_path}")

    raw_bytes = file_path.read_bytes()
    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    ext = file_path.suffix.lower()

    total_pages: int | None = None
    if ext in (".md", ".markdown", ".txt"):
        text = raw_bytes.decode("utf-8-sig")
        raw_items = [(line, None) for line in text.splitlines()]
        fmt = "Markdown" if ext in (".md", ".markdown") else "PlainText"
    elif ext == ".pdf":
        from pypdf.errors import PyPdfError
        try:
            raw_items, total_pages = _read_pdf_lines(file_path)
        except PyPdfError as exc:
            raise ValueError("PDF повреждён или недоступен для чтения.") from exc
        fmt = "PDF"
    elif ext == ".docx":
        from zipfile import BadZipFile
        from docx.opc.exceptions import PackageNotFoundError
        try:
            raw_items = _read_docx_lines(file_path)
        except (BadZipFile, PackageNotFoundError, KeyError) as exc:
            raise ValueError("Не удалось прочитать документ DOCX.") from exc
        fmt = "DOCX"
    else:
        raise ValueError(f"Неподдерживаемый формат {ext!r}. Допустимы PDF, DOCX, Markdown и TXT.")

    if not any(line.strip() for line, _ in raw_items):
        raise ValueError(f"Документ '{file_path.name}' пуст.")

    return _build_ast_from_raw_lines(
        raw_items=raw_items,
        fallback_title=file_path.stem,
        source_path=file_path,
        source_format=fmt,
        sha256=sha256,
        total_pages=total_pages,
    )
