"""Печать находок отчёта (оценка E8/E9): python eval/e8e9/show.py <report.json>..."""
import json
import sys
from pathlib import Path

for arg in sys.argv[1:]:
    r = json.loads(Path(arg).read_text(encoding="utf-8"))
    p = r["passport"]
    print(f"== {arg}  {p['analysis_status']} calls={p['llm_calls_count']} model={p['model_name']} ver={p['agent_version']} filtered={p.get('hallucinations_filtered')}")
    for d in r["atomic_defects"] + r["set_defects"]:
        lines = [e["line_start"] for e in d["evidence"]]
        head = d.get("title") or d["problem"]
        print(f"  {d['defect_id']} {d['criterion']} {d['origin']} {lines} | {head[:150]}")
        if d.get("title"):
            print(f"      Q: {d['question_for_analyst'][:200]}")
