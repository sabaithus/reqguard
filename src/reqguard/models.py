"""Строгие модели данных (Pydantic v2) для всех этапов анализа и верификации."""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


RequirementCategory = Literal[
    "GOAL",
    "ARCHITECTURE",
    "UI_UX",
    "PROCESS",
    "FUNCTIONAL",
    "NON_FUNCTIONAL",
    "DATA",
    "INTEGRATION",
    "TESTING_AC",
    "API_CONTRACT",
    "GOVERNANCE",
]

AtomicCriterion = Literal[
    "UNAMBIGUOUS",          # Однозначность (ISO 29148 5.2.5)
    "SINGULAR",             # Атомарность / единичность (ISO 29148 5.2.5)
    "COMPLETE",             # Внутренняя полнота формулировки (ISO 29148 5.2.5)
    "VERIFIABLE",           # Проверяемость / тестируемость (ISO 29148 5.2.5)
    "FEASIBLE",             # Реализуемость и корректность ограничений (ISO 29148 5.2.5)
    "IMPLEMENTATION_FREE",  # Отсутствие ошибочных проектных решений (ISO 29148 5.2.5)
    "TRACEABLE",            # Трассируемость (ISO 29148 5.2.5)
]

SetCriterion = Literal[
    "CONSISTENT",            # Внутренняя непротиворечивость (ISO 29148 5.2.6 / IEEE 830 4.3.2)
    "COMPLETE_SET",          # Полнота набора и покрытие сценариев (ISO 29148 5.2.6 / IEEE 830 4.3.3)
    "INTERFACE_INTEGRITY",   # Согласованность интерфейсов UI ↔ Логика ↔ БД ↔ API
    "VERIFIABLE_SET",        # Согласованность критериев приёмки и бизнес-правил
    "MODIFIABLE_GOVERNANCE", # Структурированность, версионность и заполненность артефакта (IEEE 830 4.3.6)
]

Severity = Literal["CRITICAL", "MAJOR", "MINOR"]

FindingOrigin = Literal["LLM", "STRUCTURAL", "HYBRID"]


CRITERION_LABELS_RU: dict[str, str] = {
    "UNAMBIGUOUS": "Однозначность (ISO 29148 п. 5.2.5)",
    "SINGULAR": "Единичность / атомарность (ISO 29148 п. 5.2.5)",
    "COMPLETE": "Полнота формулировки (ISO 29148 п. 5.2.5)",
    "VERIFIABLE": "Проверяемость / измеримость (ISO 29148 п. 5.2.5)",
    "FEASIBLE": "Техническая корректность и реализуемость (ISO 29148 п. 5.2.5)",
    "IMPLEMENTATION_FREE": "Адекватность архитектурных ограничений (ISO 29148 п. 5.2.5)",
    "TRACEABLE": "Трассируемость (ISO 29148 п. 5.2.5)",
    "CONSISTENT": "Непротиворечивость набора (ISO 29148 п. 5.2.6 / IEEE 830 п. 4.3)",
    "COMPLETE_SET": "Полнота набора требований и edge-кейсов (ISO 29148 п. 5.2.6)",
    "INTERFACE_INTEGRITY": "Целостность контрактов (UI ↔ Логика ↔ БД ↔ REST API)",
    "VERIFIABLE_SET": "Согласованность правил и критериев приёмки (IEEE 830 п. 4.3)",
    "MODIFIABLE_GOVERNANCE": "Готовность, версионность и заполненность спецификации (IEEE 830 п. 4.3)",
}

CATEGORY_LABELS_RU: dict[str, str] = {
    "GOAL": "Бизнес-цель",
    "ARCHITECTURE": "Архитектура",
    "UI_UX": "UI / UX процесс",
    "PROCESS": "Процесс (BPMN / Sequence)",
    "FUNCTIONAL": "Функциональное требование",
    "NON_FUNCTIONAL": "Нефункциональное требование",
    "DATA": "Требование к данным",
    "INTEGRATION": "Интеграция и идемпотентность",
    "TESTING_AC": "Тестирование / Критерий приёмки",
    "API_CONTRACT": "Контракт REST API",
    "GOVERNANCE": "Ревью и согласование документа",
}

SEVERITY_LABELS_RU: dict[str, str] = {
    "CRITICAL": "Критичный (Блокер)",
    "MAJOR": "Существенный",
    "MINOR": "Незначительный",
}


class SourceSpan(BaseModel):
    """Точная ссылка на место в исходном документе с дословной цитатой."""

    line_start: int = Field(description="Номер начальной строки (1-based)")
    line_end: int = Field(description="Номер конечной строки (1-based)")
    page: int | None = Field(default=None, description="Номер страницы (для PDF)")
    section_path: str = Field(default="", description="Путь разделов в документе")
    quote: str = Field(description="Дословная цитата из исходного текста документа")
    verified: bool = Field(
        default=False,
        description="Флаг детерминированной проверки подлинности цитаты в исходном файле",
    )

    def format_locator(self) -> str:
        page_prefix = f"Стр. {self.page}, " if self.page else ""
        if self.line_start == self.line_end:
            line_part = f"строка {self.line_start}"
        else:
            line_part = f"строки {self.line_start}–{self.line_end}"
        if self.section_path:
            return f"{page_prefix}{line_part} ({self.section_path})"
        return f"{page_prefix}{line_part}"


class RequirementItem(BaseModel):
    model_reviewed: bool | None = None
    """Единица требования, выделенная агентом при нарезке документа."""

    report_id: str = Field(description="Внутренний ID в отчёте, например REQ-01")
    doc_id: str | None = Field(
        default=None,
        description="Явный идентификатор из документа (например, Ф-1) или None, если номера не было",
    )
    category: RequirementCategory = Field(default="FUNCTIONAL")
    title: str = Field(description="Краткая формулировка сути требования на русском языке")
    is_compound: bool = Field(
        default=False,
        description="Содержит ли один пункт сразу несколько разнородных бизнес-правил",
    )
    sub_rules: list[str] = Field(
        default_factory=list,
        description="Подусловия внутри составного требования (если is_compound=True)",
    )
    source: SourceSpan


class AtomicDefect(BaseModel):
    """Замечание к отдельному требованию (ISO/IEC/IEEE 29148 п. 5.2.5)."""

    defect_id: str = Field(description="ID дефекта, например DEF-A01")
    req_id: str = Field(description="Ссылка на report_id требования, например REQ-05")
    doc_id: str | None = Field(default=None, description="Исходный номер в документе, если есть")
    criterion: AtomicCriterion
    severity: Severity
    problem: str = Field(description="Точное описание дефекта формулировки без общих фраз")
    impact: str = Field(description="К чему приведёт дефект при разработке, тестировании или в продакшене")
    recommendation: str = Field(description="Что именно нужно уточнить или изменить")
    suggested_fix: str | None = Field(
        default=None,
        description="Предлагаемый вариант исправленной формулировки или вопрос аналитику",
    )
    evidence: list[SourceSpan] = Field(min_length=1)
    origin: FindingOrigin = Field(default="LLM")


class SetDefect(BaseModel):
    """Замечание ко всему набору требований / межсекционный дефект (ISO 29148 п. 5.2.6 / IEEE 830 п. 4.3)."""

    defect_id: str = Field(description="ID дефекта набора, например DEF-S01")
    related_req_ids: list[str] = Field(
        default_factory=list,
        description="Список затронутых report_id (например, ['REQ-06', 'REQ-15'])",
    )
    criterion: SetCriterion
    severity: Severity
    title: str = Field(description="Короткий заголовок противоречия или пропуска")
    problem: str = Field(description="Суть противоречия, нестыковки контракта или пропущенного сценария")
    impact: str = Field(description="Последствия для банка, клиента, разработки и тестирования")
    question_for_analyst: str = Field(
        description="Конкретный вопрос бизнесу / системному аналитику для снятия блокера"
    )
    recommendation: str = Field(description="Как согласовать разделы между собой")
    evidence: list[SourceSpan] = Field(min_length=1)
    origin: FindingOrigin = Field(default="LLM")


class RunPassport(BaseModel):
    """Паспорт прогона агента (Раздел 6 ТЗ хакатона)."""

    document_title: str
    source_file: str
    source_format: str
    source_sha256: str
    total_lines: int
    total_pages: int | None = None
    run_timestamp: str
    provider: str
    model_name: str
    agent_version: str
    analysis_status: Literal["complete", "structural_only", "phase2_failed", "insufficient_input", "coverage_incomplete", "unverified_findings", "unknown"] = "unknown"
    duration_sec: float
    http_attempt_models: list[str] = Field(default_factory=list)
    llm_calls_count: int = 0
    verified_quotes_count: int = 0
    hallucinations_filtered: int = 0


    @property
    def status_label(self) -> str:
        return {
            "complete": "Обе фазы завершены",
            "coverage_incomplete": "Часть каталога выделена только статическим сканером",
            "phase2_failed": "Вторая фаза модели не завершена",
            "structural_only": "Только статическая проверка",
            "insufficient_input": "Проверяемые требования не выделены",
            "unverified_findings": "Часть утверждений модели отклонена при проверке доказательств",
            "unknown": "Статус отсутствует в паспорте этой версии",
        }[self.analysis_status]


class AuditSummary(BaseModel):
    """Агрегированная сводка по проверке."""

    total_requirements: int
    requirements_with_defects: int
    total_atomic_defects: int
    total_set_defects: int
    total_defects: int
    critical_count: int
    major_count: int
    minor_count: int
    by_criterion: dict[str, int] = Field(default_factory=dict)
    readiness_score: int | None = Field(ge=0, le=100)
    verdict: str
    can_start_development: bool | None
    can_start_testing: bool | None
    verdict_rationale: str
    priority_fixes: list[str] = Field(default_factory=list)


    @property
    def score_label(self) -> str:
        return "не оценено" if self.readiness_score is None else f"{self.readiness_score}/100"


class AuditReport(BaseModel):
    """Полный верифицированный результат работы агента."""

    passport: RunPassport
    requirements: list[RequirementItem]
    atomic_defects: list[AtomicDefect]
    set_defects: list[SetDefect]
    summary: AuditSummary
