"""Парный эксперимент на одном документе и одной модели. Смысл замечаний оценивает рецензент."""
from __future__ import annotations

import argparse
import json
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from shutil import copyfile

from pydantic import BaseModel

from main import ROOT_DIR
from reqguard.config import load_settings
from reqguard.llm_client import LLMGatewayError, ResilientLLMClient
from reqguard.models import Severity, SourceSpan
from reqguard.parser import parse_document
from reqguard.pipeline import run_audit_pipeline
from reqguard.prompts import SYSTEM_PROMPT_AUDITOR
from reqguard.verifier import verify_and_anchor_span


class Finding(BaseModel):
    problem: str
    severity: Severity
    evidence: list[SourceSpan]


class SingleReview(BaseModel):
    findings: list[Finding]


def compare(source: Path, destination: Path) -> None:
    if destination.exists():
        raise ValueError("Укажите новый каталог для результатов сравнения.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    settings = replace(load_settings(ROOT_DIR / ".env"), fallback_models=[], max_retries=1)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
        work = Path(temp)
        snapshot = work / "input" / source.name
        snapshot.parent.mkdir()
        copyfile(source, snapshot)
        doc = parse_document(snapshot)
        if doc.unreadable_pages:
            raise ValueError("Для сравнения нужен читаемый документ.")
        client = ResilientLLMClient(settings)
        prompt = (
            "За один ответ проверь каждое требование и весь набор по ISO 29148 и IEEE 830. "
            "Проверь приёмку, исключения и границы. Для каждого дефекта укажи проблему, тяжесть и "
            "дословные цитаты всех мест. Проверяй приоритет правил. Верни JSON по схеме "
            + json.dumps(SingleReview.model_json_schema(), ensure_ascii=False) + "\n" + doc.render_numbered_text()
        )
        started = time.perf_counter()
        review = client.generate_structured(SYSTEM_PROMPT_AUDITOR, prompt, SingleReview)
        elapsed = time.perf_counter() - started
        baseline = {
            "source_sha256": doc.sha256, "provider": settings.provider, "model": client.last_used_model,
            "duration_sec": round(elapsed, 2), "http_attempt_models": client.http_models,
            "token_usage": [u.model_dump() for u in client.token_usage],
            "findings": [
                {**f.model_dump(), "verified_evidence": [
                    anchored.model_dump() if (anchored := verify_and_anchor_span(doc, span)) else None
                    for span in f.evidence
                ]} for f in review.findings
            ],
        }
        agent = run_audit_pipeline(snapshot, settings)
        if agent.passport.analysis_status != "complete":
            raise ValueError(f"Полная пара не получена: {agent.passport.status_label}.")
        pair = work / "pair"
        pair.mkdir()
        (pair / "single.json").write_text(json.dumps(baseline, indent=2, ensure_ascii=False), encoding="utf8")
        (pair / "agent.json").write_text(agent.model_dump_json(indent=2), encoding="utf8")
        pair.rename(destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        compare(args.source, args.destination)
    except (OSError, ValueError, LLMGatewayError) as exc:
        parser.exit(2, f"{exc}\n")
