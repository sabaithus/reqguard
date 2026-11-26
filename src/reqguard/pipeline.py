"""Оркестратор гибридного пайплайна проверки требований:
AST-парсинг → Детерминированный сканер → LLM Фаза 1 (Нарезка + Atomic) → LLM Фаза 2 (Set) → Верификация цитат и скоринг.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from pydantic import BaseModel, Field

from reqguard.config import LLMSettings, detect_agent_version
from reqguard.llm_client import LLMGatewayError, ResilientLLMClient
from reqguard.models import (
    AtomicCriterion,
    AtomicDefect,
    AuditReport,
    RequirementCategory,
    RequirementItem,
    RunPassport,
    SetCriterion,
    SetDefect,
    Severity,
    SourceSpan,
)
from reqguard.parser import ParsedDocument, parse_document
from reqguard.prompts import (
    SYSTEM_PROMPT_AUDITOR,
    build_pass1_atomic_prompt,
    build_pass2_set_prompt,
)
from reqguard.structural_scanner import StructuralScanResult, run_structural_scan
from reqguard.verifier import build_verified_audit_report, verify_and_anchor_span


# Промежуточные схемы ответов LLM
class _LLMRawReq(BaseModel):
    temp_id: str
    doc_id: str | None = None
    category: RequirementCategory = "FUNCTIONAL"
    title: str
    is_compound: bool = False
    sub_rules: list[str] = Field(default_factory=list)
    line_start: int
    line_end: int
    quote: str


class _ModelFinding(BaseModel):
    severity: Severity = "MAJOR"
    blocked_decision: str = ""
    impact: str

    def assessed_severity(self) -> Severity:
        if self.severity == "CRITICAL" and not self.blocked_decision.strip():
            return "MAJOR"
        return self.severity

    def reported_impact(self) -> str:
        if self.assessed_severity() == "CRITICAL":
            return f"{self.impact.strip()} Блокируемое решение: {self.blocked_decision.strip()}"
        return self.impact.strip()


class _LLMRawAtomicDefect(_ModelFinding):
    temp_req_id: str = ""
    doc_id: str | None = None
    criterion: AtomicCriterion = "COMPLETE"
    problem: str
    recommendation: str
    suggested_fix: str | None = None
    line_start: int
    line_end: int
    quote: str


class _Pass1Response(BaseModel):
    requirements: list[_LLMRawReq]
    atomic_defects: list[_LLMRawAtomicDefect]


class _LLMRawQuote(BaseModel):
    line_start: int
    line_end: int
    quote: str


class _LLMRawSetDefect(_ModelFinding):
    related_req_ids: list[str] = Field(default_factory=list)
    criterion: SetCriterion = "CONSISTENT"
    title: str
    problem: str
    question_for_analyst: str
    recommendation: str
    evidence: list[_LLMRawQuote] = Field(min_length=1)


class _Pass2Response(BaseModel):
    reviewed_req_ids: list[str] = Field(default_factory=list)
    additional_atomic_defects: list[_LLMRawAtomicDefect] = Field(default_factory=list)
    set_defects: list[_LLMRawSetDefect]


def _merge_requirements(
    doc: ParsedDocument,
    baseline: list[RequirementItem],
    llm_reqs: list[_LLMRawReq],
) -> tuple[list[RequirementItem], dict[str, str], int]:
    """Объединяет требования от LLM и базового структурного парсера без дублирования, верифицируя цитаты."""
    merged: list[RequirementItem] = []
    temp_to_report_id: dict[str, str] = {}
    covered_lines: set[int] = set()
    covered_doc_ids: dict[str, RequirementItem] = {}
    dropped_unverified = 0

    for raw in llm_reqs:
        clean_doc_id = raw.doc_id.strip() if raw.doc_id and raw.doc_id.strip() not in ("—", "-", "null", "None") else None
        candidate_span = SourceSpan(
            line_start=raw.line_start,
            line_end=max(raw.line_start, raw.line_end),
            quote=raw.quote,
        )
        verified_span = verify_and_anchor_span(doc, candidate_span)
        if verified_span is None:
            dropped_unverified += 1
            continue

        item = RequirementItem(
            model_reviewed=True,
            report_id="TEMP",
            doc_id=clean_doc_id,
            category=raw.category,
            title=raw.title.strip(),
            is_compound=raw.is_compound,
            sub_rules=raw.sub_rules,
            source=verified_span,
        )
        merged.append(item)
        temp_to_report_id[raw.temp_id] = f"__IDX_{len(merged) - 1}"
        for ln in range(verified_span.line_start, verified_span.line_end + 1):
            covered_lines.add(ln)
        if clean_doc_id:
            covered_doc_ids[clean_doc_id.upper()] = item

    # Дополняем детерминированными требованиями, если модель пропустила какой-то раздел или строку таблицы приёмки
    for base_req in baseline:
        if base_req.doc_id and base_req.doc_id.upper() in covered_doc_ids:
            existing = covered_doc_ids[base_req.doc_id.upper()]
            if base_req.is_compound and not existing.is_compound:
                existing.is_compound = True
                existing.sub_rules = base_req.sub_rules
            continue
        base_lines = set(range(base_req.source.line_start, base_req.source.line_end + 1))
        if not (base_lines & covered_lines):
            base_req.model_reviewed = False
            merged.append(base_req)
            covered_lines.update(base_lines)

    # Сортируем по порядку строк в документе и присваиваем стабильные REQ-01..REQ-NN
    indexed_items = list(enumerate(merged))
    indexed_items.sort(key=lambda pair: (pair[1].source.line_start, pair[0]))

    old_idx_to_new_id: dict[int, str] = {}
    final_reqs: list[RequirementItem] = []
    for new_pos, (old_idx, req_item) in enumerate(indexed_items, start=1):
        new_id = f"REQ-{new_pos:02d}"
        req_item.report_id = new_id
        old_idx_to_new_id[old_idx] = new_id
        final_reqs.append(req_item)

    resolved_map: dict[str, str] = {}
    for t_id, marker in temp_to_report_id.items():
        old_i = int(marker.replace("__IDX_", ""))
        if old_i in old_idx_to_new_id:
            resolved_map[t_id] = old_idx_to_new_id[old_i]

    return final_reqs, resolved_map, dropped_unverified


def _format_req_catalog(reqs: list[RequirementItem]) -> str:
    lines: list[str] = []
    for r in reqs:
        did = f" ({r.doc_id})" if r.doc_id else ""
        lines.append(
            f"- {r.report_id}{did} [{r.category}] (строки {r.source.line_start}–{r.source.line_end}): {r.title}"
        )
    return "\n".join(lines)


def run_audit_pipeline(
    input_path: str | Path,
    settings: LLMSettings,
    progress_cb: Callable[[str], None] | None = None,
    allow_structural_only: bool = False,
) -> AuditReport:
    """Выполняет полный цикл проверки документа и возвращает верифицированный отчёт."""
    t0 = time.perf_counter()

    def notify(msg: str) -> None:
        if progress_cb:
            progress_cb(msg)

    notify("Шаг 1/5: Чтение документа и построение индексированного AST...")
    doc = parse_document(input_path)
    numbered_text = doc.render_numbered_text()

    notify("Шаг 2/5: Детерминированный структурный и контрактный пре-анализ...")
    structural: StructuralScanResult = run_structural_scan(doc)

    llm_client = ResilientLLMClient(settings)
    raw_llm_reqs: list[_LLMRawReq] = []
    raw_llm_atomic: list[_LLMRawAtomicDefect] = []
    raw_llm_set: list[_LLMRawSetDefect] = []

    analysis_status = "complete"
    if settings.provider == "none" and allow_structural_only:
        analysis_status = "structural_only"
        notify("Режим без внешнего LLM (structural-only): пропуск сетевых вызовов...")
        provider_label = "structural-only"
        model_label = "deterministic-static-engine"
    else:
        notify(f"Шаг 3/5: LLM Фаза 1 ({settings.provider}:{settings.primary_model}) — нарезка и индивидуальная проверка требований (ISO 29148 п. 5.2.5)...")
        pass1_prompt = build_pass1_atomic_prompt(numbered_text)
        if structural.baseline_requirements:
            pass1_prompt += (
                "\nЧерновой реестр парсера для проверки покрытия. Проверь каждую строку "
                "по исходному тексту. Исправь ошибки нарезки, добавь пропуски и верни "
                "проверенные элементы в requirements.\n"
                + _format_req_catalog(structural.baseline_requirements)
            )
        pass1_resp = llm_client.generate_structured(
            system_prompt=SYSTEM_PROMPT_AUDITOR,
            user_prompt=pass1_prompt,
            response_model=_Pass1Response,
            status_cb=notify,
        )
        raw_llm_reqs = pass1_resp.requirements
        raw_llm_atomic = pass1_resp.atomic_defects

    requirements, temp_id_map, dropped_reqs = _merge_requirements(
        doc=doc,
        baseline=structural.baseline_requirements,
        llm_reqs=raw_llm_reqs,
    )

    if not (settings.provider == "none" and allow_structural_only):
        notify(f"Шаг 4/5: LLM Фаза 2 ({settings.provider}:{llm_client.last_used_model}) — перекрёстная проверка набора и трассировка приёмки (ISO 29148 п. 5.2.6 / IEEE 830)...")
        catalog_str = _format_req_catalog(requirements)
        pass2_prompt = build_pass2_set_prompt(numbered_text, catalog_str)
        pending = {r.report_id for r in requirements if not r.model_reviewed}
        if pending:
            pass2_prompt += (
                "\nТакже выполни индивидуальную проверку следующих пунктов: " + ", ".join(sorted(pending))
                + ". Добавь reviewed_req_ids со списком фактически проверенных ID и "
                "additional_atomic_defects с выявленными индивидуальными дефектами "
                "(temp_req_id = REQ-ID, criterion, severity, problem, impact, recommendation, "
                "line_start, line_end, quote). Если дефектов нет, верни пустой массив."
            )
        try:
            pass2_resp = llm_client.generate_structured(
                system_prompt=SYSTEM_PROMPT_AUDITOR,
                user_prompt=pass2_prompt,
                response_model=_Pass2Response,
                status_cb=notify,
            )
            raw_llm_set = pass2_resp.set_defects
            reviewed = set(pass2_resp.reviewed_req_ids) & pending
            for req in requirements:
                if req.report_id in reviewed:
                    req.model_reviewed = True
            for defect in pass2_resp.additional_atomic_defects:
                if defect.temp_req_id in reviewed:
                    key = f"phase2:{defect.temp_req_id}"
                    temp_id_map[key] = defect.temp_req_id
                    raw_llm_atomic.append(defect.model_copy(update={"temp_req_id": key}))
        except LLMGatewayError as exc:
            if llm_client.calls_count >= 1:
                notify(
                    f"  [!] Предупреждение: Фаза 2 LLM завершилась ошибкой ({exc}). "
                    "Сборка отчёта продолжается на данных AST, статического анализатора и Фазы 1."
                )
                analysis_status = "phase2_failed"
                raw_llm_set = []
            else:
                raise
        provider_label = settings.provider
        model_label = llm_client.last_used_model

    notify("Шаг 5/5: Детерминированная анти-галлюцинационная верификация цитат и расчёт готовности...")

    # Индексы для привязки дефектов к итоговым REQ-ID
    doc_id_to_req: dict[str, RequirementItem] = {
        r.doc_id.upper(): r for r in requirements if r.doc_id
    }
    line_to_req: dict[int, RequirementItem] = {}
    for r in requirements:
        for ln in range(r.source.line_start, r.source.line_end + 1):
            line_to_req.setdefault(ln, r)

    candidate_atomic: list[AtomicDefect] = []
    # Перепривязываем структурные атомарные дефекты к финальным REQ-ID
    for s_ad in structural.atomic_defects:
        matched_req = (
            doc_id_to_req.get(s_ad.doc_id.upper())
            if s_ad.doc_id
            else line_to_req.get(s_ad.evidence[0].line_start)
        )
        if matched_req is None:
            continue
        s_ad.req_id = matched_req.report_id
        s_ad.doc_id = matched_req.doc_id if matched_req else s_ad.doc_id
        candidate_atomic.append(s_ad)

    for idx, raw_ad in enumerate(raw_llm_atomic, start=1):
        resolved_req_id = temp_id_map.get(raw_ad.temp_req_id)
        if not resolved_req_id and raw_ad.doc_id:
            m_req = doc_id_to_req.get(raw_ad.doc_id.strip().upper())
            if m_req:
                resolved_req_id = m_req.report_id
        if not resolved_req_id:
            m_req = line_to_req.get(raw_ad.line_start)
            if m_req is None:
                continue
            resolved_req_id = m_req.report_id

        req_obj = next((r for r in requirements if r.report_id == resolved_req_id), None)
        candidate_atomic.append(
            AtomicDefect(
                defect_id=f"DEF-A{idx:02d}",
                req_id=resolved_req_id,
                doc_id=req_obj.doc_id if req_obj else raw_ad.doc_id,
                criterion=raw_ad.criterion,
                severity=raw_ad.assessed_severity(),
                problem=raw_ad.problem.strip(),
                impact=raw_ad.reported_impact(),
                recommendation=raw_ad.recommendation.strip(),
                suggested_fix=raw_ad.suggested_fix.strip() if raw_ad.suggested_fix else None,
                evidence=[
                    SourceSpan(
                        line_start=raw_ad.line_start,
                        line_end=max(raw_ad.line_start, raw_ad.line_end),
                        quote=raw_ad.quote,
                    )
                ],
                origin="LLM",
            )
        )

    candidate_set: list[SetDefect] = list(structural.set_defects)
    valid_req_ids = {r.report_id for r in requirements}
    for idx, raw_sd in enumerate(raw_llm_set, start=1):
        cleaned_rel: list[str] = []
        for rid in raw_sd.related_req_ids:
            rid_u = rid.strip().upper()
            if rid_u in valid_req_ids:
                cleaned_rel.append(rid_u)
            elif rid_u in doc_id_to_req:
                cleaned_rel.append(doc_id_to_req[rid_u].report_id)
            elif rid.strip() in temp_id_map:
                cleaned_rel.append(temp_id_map[rid.strip()])

        candidate_set.append(
            SetDefect(
                defect_id=f"DEF-S{idx:02d}",
                related_req_ids=sorted(set(cleaned_rel)),
                criterion=raw_sd.criterion,
                severity=raw_sd.assessed_severity(),
                title=raw_sd.title.strip(),
                problem=raw_sd.problem.strip(),
                impact=raw_sd.reported_impact(),
                question_for_analyst=raw_sd.question_for_analyst.strip(),
                recommendation=(
                    "Уточнить у владельца документа: " + raw_sd.question_for_analyst.strip()
                    if raw_sd.criterion == "COMPLETE_SET"
                    else raw_sd.recommendation.strip()
                ),
                evidence=[
                    SourceSpan(
                        line_start=ev.line_start,
                        line_end=max(ev.line_start, ev.line_end),
                        quote=ev.quote,
                    )
                    for ev in raw_sd.evidence
                ],
                origin="LLM",
            )
        )

    # Привязываем структурные set_defects к REQ-ID по строкам их доказательств
    for sd in candidate_set:
        if not sd.related_req_ids:
            inferred = []
            for ev in sd.evidence:
                for ln in range(ev.line_start, ev.line_end + 1):
                    if ln in line_to_req:
                        inferred.append(line_to_req[ln].report_id)
            sd.related_req_ids = sorted(set(inferred))

    if not requirements:
        analysis_status = "insufficient_input"
    elif analysis_status == "complete" and any(not r.model_reviewed for r in requirements):
        analysis_status = "coverage_incomplete"

    duration = round(time.perf_counter() - t0, 2)
    passport = RunPassport(
        document_title=doc.title,
        source_file=doc.source_path.name,
        source_format=doc.source_format,
        source_sha256=doc.sha256,
        analysis_status=analysis_status,
        total_lines=doc.total_lines,
        total_pages=doc.total_pages,
        run_timestamp=datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %z"),
        provider=provider_label,
        model_name=model_label,
        agent_version=detect_agent_version(),
        duration_sec=duration,
        llm_calls_count=llm_client.calls_count,
        http_attempt_models=llm_client.http_models,
    )

    return build_verified_audit_report(
        doc=doc,
        passport=passport,
        requirements=requirements,
        candidate_atomic=candidate_atomic,
        candidate_set=candidate_set,
        initial_filtered_count=dropped_reqs,
    )
