"""Count reference issues whose source anchors occur together in one verified finding."""
import argparse
import json
import re
from pathlib import Path


def normalize(text):
    return " ".join(re.findall(r"[\w]+", text.casefold().replace("ё", "е")))


def score(path, key):
    report = json.loads(path.read_text(encoding="utf8"))
    if "passport" in report:
        findings = report["atomic_defects"] + report["set_defects"]
        groups = [[s for s in f["evidence"] if s.get("verified")] for f in findings]
    else:
        groups = [[s for s in f["verified_evidence"] if s] for f in report["findings"]]
    evidence = [[normalize(s["quote"]) for s in group] for group in groups]
    hits = [row["id"] for row in key if all(row["anchors"]) and any(
        all(any(normalize(a) in quote for quote in group) for a in row["anchors"]) for group in evidence)]
    return {"evidence_hits": hits, "covered": len(hits), "reference_count": len(key)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("key", type=Path)
    parser.add_argument("reports", type=Path, nargs="+")
    args = parser.parse_args()
    key = json.loads(args.key.read_text(encoding="utf8"))
    for path in args.reports:
        print(path, score(path, key))
