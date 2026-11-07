"""Summarize local MobilityData validator reports by operator, severity and rule.

Reads only evidence.json/report.json written by validate_external_gtfs.py under
the ignored data/local-verification/phase4-validator/ folder, picking the
latest report_generated run per source. Writes a JSON summary next to them.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "data" / "local-verification" / "phase4-validator"
SEVERITIES = ("ERROR", "WARNING", "INFO")


def latest_reports(folder: Path) -> dict[str, tuple[dict, Path]]:
    chosen: dict[str, tuple[dict, Path]] = {}
    for evidence_file in folder.glob("*/evidence.json"):
        try:
            evidence = json.loads(evidence_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if evidence.get("status") != "report_generated":
            continue
        # Skip records not stored beside their report (written by the pre-fix runner).
        if Path(evidence.get("report_directory", "")).resolve() != evidence_file.parent.resolve():
            continue
        source = evidence["source"]
        if source not in chosen or evidence.get("validated_at_utc", "") > chosen[source][0].get("validated_at_utc", ""):
            chosen[source] = (evidence, evidence_file.parent / "report.json")
    return chosen


def summarize(folder: Path) -> dict:
    operators = {}
    for source, (evidence, report_file) in sorted(latest_reports(folder).items()):
        report = json.loads(report_file.read_text(encoding="utf-8"))
        by_severity, by_rule = Counter(), []
        for notice in report.get("notices", []):
            by_severity[notice["severity"]] += notice["totalNotices"]
            by_rule.append({"severity": notice["severity"], "code": notice["code"], "count": notice["totalNotices"]})
        by_rule.sort(key=lambda r: (SEVERITIES.index(r["severity"]) if r["severity"] in SEVERITIES else 9, -r["count"], r["code"]))
        summary = report.get("summary", {})
        operators[source] = {
            "snapshot_id": evidence["snapshot_id"], "input_sha256": evidence["input_sha256"],
            "validator_version": summary.get("validatorVersion"), "date": summary.get("dateForValidation"),
            "validation_seconds": summary.get("validationTimeSeconds"),
            "report_directory": str(report_file.parent),
            "notice_totals": {s: by_severity.get(s, 0) for s in SEVERITIES},
            "rules_by_severity": {s: sum(1 for r in by_rule if r["severity"] == s) for s in SEVERITIES},
            "rules": by_rule,
        }
    return {"generated_at_utc": datetime.now(timezone.utc).isoformat(), "operators": operators}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", type=Path, default=DEFAULT)
    args = parser.parse_args(argv)
    result = summarize(args.reports)
    target = args.reports / f"analysis-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.json"
    target.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    for source, op in result["operators"].items():
        totals = ", ".join(f"{s} {op['notice_totals'][s]} ({op['rules_by_severity'][s]} rules)" for s in SEVERITIES)
        print(f"{source}: {totals}")
        for rule in op["rules"]:
            print(f"  {rule['severity']:<7} {rule['code']:<55} {rule['count']:>8}")
    print(f"Written: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
