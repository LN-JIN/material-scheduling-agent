"""Audit deliverable files and preserved execution evidence without network access."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {".git", ".runtime", "__pycache__", ".venv", "work"}


def files(root=ROOT):
    for path in sorted(root.rglob("*")):
        if path.is_file() and not (set(path.relative_to(root).parts) & EXCLUDED):
            yield path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def audit():
    checks = []

    def check(label, ok, detail=None):
        item = {"name": label, "passed": bool(ok)}
        if detail is not None:
            item["detail"] = detail
        checks.append(item)

    required = ["README.md", "PLAN.md", "requirements.txt", ".env.example", ".gitignore",
                "src/main.py", "src/agent.py", "src/models.py", "src/tools.py", "src/store.py",
                "tests/test_acceptance.py", "data/materials.json", "data/rules.json", "data/schedules.json",
                "docs/01-requirements.md", "docs/02-solution.md", "docs/03-evaluation.md",
                "docs/04-ai-usage.md", "docs/05-retrospective.md", "docs/acceptance-report.md",
                "examples/test-results.txt"]
    for name in required:
        check("required:" + name, (ROOT / name).is_file())

    for rel in ("examples/live", "examples/live-repeats", "examples/offline", "examples/iterations/v1", "examples/revision/live", "examples/revision/offline"):
        directory = ROOT / rel
        manifest_path = directory / "summary.json"
        check("manifest:" + rel, manifest_path.exists())
        if not manifest_path.exists():
            continue
        summary = read_json(manifest_path)
        historical = rel.endswith("/v1")
        check("case_count:" + rel, summary["total_runs"] == len(summary["records"]))
        check("reported_results:" + rel, summary["passed_runs"] == sum(r["passed"] for r in summary["records"]))
        check("expected_experiment_outcome:" + rel,
              summary["passed_runs"] == (10 if historical else summary["total_runs"]))
        if not historical:
            check("source_stable:" + rel, summary.get("source_unchanged_during_run") is True)
        source_root = directory / "source_snapshot" if historical else (ROOT / "examples/iterations/v2/source_snapshot" if rel in ("examples/live", "examples/live-repeats", "examples/offline") else ROOT)
        for name, expected_hash in summary["source_sha256"].items():
            path = source_root / name
            check("source_hash:" + rel + ":" + name, path.is_file() and sha(path) == expected_hash)
        responses = 0
        for record in summary["records"]:
            for name, expected_hash in record["artifacts"].items():
                path = directory / name
                check("artifact_hash:" + rel + ":" + name, path.is_file() and sha(path) == expected_hash)
            traces = [name for name in record["artifacts"] if name.endswith(".trace.jsonl")]
            for name in traces:
                entries = [json.loads(line) for line in (directory / name).read_text(encoding="utf-8").splitlines() if line]
                provider = [entry["provider"] for entry in entries if entry.get("provider", {}).get("response_id")]
                responses += len(provider)
                if summary["mode"] == "live":
                    check("provider_model:" + rel + ":" + name,
                          all(p.get("requested_model") == "qwen3.8-max" and p.get("response_model") == "qwen3.8-max" for p in provider))
        check("actual_provider_call_count:" + rel, responses == summary["provider_calls"])

    preference_path = ROOT / "examples/revision/preferences/summary.json"
    check("preference_manifest", preference_path.exists())
    if preference_path.exists():
        pref = read_json(preference_path)
        check("preference_live_success", pref.get("live_success") is True)
        check("preference_source_stable", pref.get("source_unchanged_during_run") is True)
        for name, expected in pref["source_sha256"].items():
            check("preference_source:" + name, (ROOT / name).is_file() and sha(ROOT / name) == expected)
        directory = preference_path.parent
        for record in pref["records"] + pref["earliest_baseline"]:
            for name, expected in record["artifacts"].items():
                check("preference_artifact:" + name, (directory / name).is_file() and sha(directory / name) == expected)
        for name, expected in pref["fixture_sha256"].items():
            check("preference_fixture:" + name, sha(directory / name) == expected)

    broken_links = []
    for path in files():
        if path.suffix == ".md":
            content = path.read_text(encoding="utf-8-sig")
            for target in re.findall(r"\[[^\]]*\]\(([^\s)]+)\)", content):
                if re.match(r"[a-z]+:", target, flags=re.I) or target.startswith("#"):
                    continue
                target = target.split("#", 1)[0]
                if target and not (path.parent / target).exists():
                    broken_links.append({"file": path.relative_to(ROOT).as_posix(), "target": target})
    check("local_markdown_links", not broken_links, broken_links)

    suspicious = []
    key_patterns = [rb"sk-ws-[A-Za-z0-9_.-]{24,}", rb"gh[pousr]_[A-Za-z0-9]{24,}",
                    rb"github_pat_[A-Za-z0-9_]{30,}", rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"]
    for path in files():
        data = path.read_bytes()
        if any(re.search(pattern, data) for pattern in key_patterns):
            suspicious.append(path.relative_to(ROOT).as_posix())
        if path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.example"):
            suspicious.append(path.relative_to(ROOT).as_posix())
    check("no_credentials_or_private_keys_in_deliverable", not suspicious, suspicious)
    log_path = ROOT / "examples/test-results.txt"
    if log_path.exists():
        log = log_path.read_text(encoding="utf-8-sig")
        check("actual_unittest_output", bool(re.search(r"Ran \d+ tests in", log)) and log.rstrip().endswith("OK"))
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "checks": checks,
            "total_checks": len(checks), "passed_checks": sum(c["passed"] for c in checks),
            "passed": all(c["passed"] for c in checks),
            "scope": "File completeness, evidence integrity, provider labels and local references; independent behavioral review is in docs/acceptance-report.md."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    failures = [item for item in report["checks"] if not item["passed"]]
    print(json.dumps({"passed": report["passed"], "passed_checks": report["passed_checks"],
                      "total_checks": report["total_checks"], "failures": failures}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
