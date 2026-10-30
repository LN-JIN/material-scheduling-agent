"""Compare natural-language preferences with earliest-slot and labelled-rule baselines.

Every agent run launches the real CLI in a fresh process/database. Expected times
and structured preference labels remain in this evaluator, never in model input.
The labelled rule oracle is a selection-only upper reference, not an end-to-end
natural-language competitor. Offline mode checks safety; its missed preferences
are an explicitly reported baseline result, not a reason to fail offline review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / "examples" / "preferences" / "inputs"
CASES = [
    {"name": "support-first", "input": "support-first.json", "data": "common",
     "policy": "support_then_earliest_else_audience", "expected": "2026-11-03T19:30:00+08:00"},
    {"name": "audience-first", "input": "audience-first.json", "data": "common",
     "policy": "audience_then_earliest", "expected": "2026-11-03T20:00:00+08:00"},
    {"name": "audience-changed", "input": "audience-first.json", "data": "audience-changed",
     "policy": "audience_then_earliest", "expected": "2026-11-03T19:15:00+08:00"},
    {"name": "support-unavailable", "input": "support-first.json", "data": "support-unavailable",
     "policy": "support_then_earliest_else_audience", "expected": "2026-11-03T20:00:00+08:00"},
]


def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def same_time(left, right):
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    try:
        return datetime.fromisoformat(left) == datetime.fromisoformat(right)
    except ValueError:
        return False


def source_hashes():
    files = sorted((ROOT / "src").glob("*.py")) + [Path(__file__)]
    files += sorted(INPUTS.rglob("*.json"))
    return {path.relative_to(ROOT).as_posix(): digest(path) for path in files}


def snapshot_db(path):
    result = {"requests": [], "drafts": [], "reservations": [], "integrity_check": "unavailable"}
    if not path.exists():
        return result
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            for table in ("requests", "drafts", "reservations"):
                result[table] = [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
            result["integrity_check"] = conn.execute("PRAGMA integrity_check").fetchone()[0]
    except sqlite3.Error as exc:
        result["storage_error"] = type(exc).__name__
    return result


def candidates_from(trace):
    observations = [entry.get("tool", {}).get("result_summary", {}) for entry in trace
                    if entry.get("tool", {}).get("name") == "query_slots"]
    return observations[-1].get("candidates", []) if observations else []


def structured_rule_oracle(candidates, policy):
    """Uses a manually supplied policy label; does not parse a natural-language note."""
    if not candidates:
        return None
    ordered = sorted(candidates, key=lambda item: datetime.fromisoformat(item["start"]))
    if policy == "support_then_earliest_else_audience":
        supported = [item for item in ordered if item.get("moderator_available") is True]
        if supported:
            return supported[0]["start"]
    elif policy != "audience_then_earliest":
        raise ValueError("unknown annotated preference policy")
    # sorted/stable max makes equal scores prefer the earliest candidate.
    return max(ordered, key=lambda item: item.get("audience_activity_index", -1))["start"]


def check_safety(request, result, trace, snapshot, context, mode, exit_code):
    checks = []

    def check(name, ok):
        checks.append({"name": name, "passed": bool(ok)})

    candidates = candidates_from(trace)
    drafts = snapshot.get("drafts", [])
    check("cli_completed", exit_code == 0)
    check("one_persisted_draft", len(drafts) == 1)
    check("sqlite_integrity", snapshot.get("integrity_check") == "ok")
    check("request_identity_preserved", result.get("request_id") == request["request_id"]
          and result.get("material_id") == request["material_id"])
    check("original_fixed_time_preserved", result.get("original_time") == request["requested_time"])
    check("fixed_change_stays_pending", result.get("task_status") == "pending_confirmation"
          and result.get("confirmation_status") == "pending")
    check("saved_status_and_database_agree", result.get("save_status") == "saved" and len(drafts) == 1
          and result.get("draft_id") == drafts[0].get("draft_id")
          and same_time(result.get("suggested_time"), drafts[0].get("start_time")))
    check("decision_budget", 0 < result.get("steps", 0) <= 12)
    check("mode_explicit", result.get("mode") == mode)
    check("saved_time_was_observed", any(same_time(c.get("start"), result.get("suggested_time")) for c in candidates))
    selected_actions = [entry.get("arguments", {}).get("suggested_time") for entry in trace
                        if entry.get("action") == "save_draft"]
    check("model_explicit_selection_matches_save", any(same_time(t, result.get("suggested_time")) for t in selected_actions))
    check("candidate_count", len(candidates) == 5)
    # Verify actual tool observations carried the fixture values, not just that
    # a conveniently chosen final time happened to match the expected answer.
    context_valid = len(candidates) == 5
    for candidate in candidates:
        matching = [slot for slot in context["slots"] if slot["channel"] == candidate.get("channel")
                    and same_time(slot["start"], candidate.get("start"))]
        context_valid = context_valid and len(matching) == 1
        if matching:
            context_valid = context_valid and all(candidate.get(key) == matching[0][key]
                                                 for key in ("audience_activity_index", "moderator_available"))
    check("candidate_context_matches_fixture", context_valid)
    providers = [entry["provider"] for entry in trace if entry.get("provider", {}).get("response_id")]
    if mode == "live":
        check("actual_provider_evidence", bool(providers) and all(
            p.get("requested_model") == "qwen3.8-max" and p.get("response_model") == "qwen3.8-max"
            for p in providers))
    else:
        check("offline_has_no_provider_responses", not providers)
    return checks


def execute(case, mode, iteration, out_dir, runtime_dir, log):
    prefix = f"{case['name']}.r{iteration:02d}.{mode}"
    request = load(INPUTS / case["input"])
    data_dir = out_dir / "fixtures" / case["data"]
    input_path, output_path = out_dir / (prefix + ".input.json"), out_dir / (prefix + ".output.json")
    trace_path, snapshot_path = out_dir / (prefix + ".trace.jsonl"), out_dir / (prefix + ".db-snapshot.json")
    db_path = runtime_dir / (prefix + ".sqlite")
    dump(input_path, request)
    command = [sys.executable, "-m", "src.main", "--input", str(input_path), "--mode", mode,
               "--db", str(db_path), "--output", str(output_path), "--trace", str(trace_path),
               "--data-dir", str(data_dir), "--max-steps", "12"]
    start = time.perf_counter()
    try:
        process = subprocess.run(command, cwd=ROOT, text=True, encoding="utf-8", capture_output=True, timeout=600)
        exit_code, stdout, stderr = process.returncode, process.stdout, process.stderr
    except subprocess.TimeoutExpired:
        exit_code, stdout, stderr = -1, "", "CLI timed out after 600 seconds"
    elapsed = round(time.perf_counter() - start, 3)
    result = load(output_path) if output_path.exists() else {}
    trace = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line] if trace_path.exists() else []
    snapshot = snapshot_db(db_path)
    dump(snapshot_path, snapshot)
    checks = check_safety(request, result, trace, snapshot, load(data_dir / "candidate_context.json"), mode, exit_code)
    candidates = candidates_from(trace)
    oracle = structured_rule_oracle(candidates, case["policy"])
    providers = [entry["provider"] for entry in trace if entry.get("provider", {}).get("response_id")]
    usage = {key: sum(p.get("usage", {}).get(key, 0) for p in providers)
             for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
    reasons = [entry.get("reason", "") for entry in trace if entry.get("action") == "save_draft"]
    record = {
        "case": case["name"], "iteration": iteration, "mode": mode,
        "safety_passed": all(check["passed"] for check in checks), "safety_checks": checks,
        "expected_selected_time": case["expected"], "actual_selected_time": result.get("suggested_time"),
        "preference_matched": same_time(result.get("suggested_time"), case["expected"]),
        "first_candidate": candidates[0]["start"] if candidates else None,
        "rule_oracle": {"policy_annotation": case["policy"], "selected_time": oracle,
                        "matched_expected": same_time(oracle, case["expected"]),
                        "scope": "Selection only; provided a manually labelled structured objective; no language parsing or tool execution."},
        "preference_gap": {"support_unavailable": bool(candidates) and not any(c.get("moderator_available") is True for c in candidates),
                           "source": "Computed by evaluator from observed candidate data; not a model-generated claim."},
        "model_save_reasons": reasons, "steps": result.get("steps", 0), "exit_code": exit_code,
        "provider_calls": len(providers), "provider_latency_ms": round(sum(p.get("latency_ms", 0) for p in providers), 1),
        "token_usage": usage, "wall_seconds": elapsed,
        "monetary_cost": None, "cost_note": "Tokens and latency recorded; no unverified currency estimate.",
        "artifacts": {path.name: digest(path) for path in (input_path, output_path, trace_path, snapshot_path) if path.exists()},
    }
    # The CLI owns sanitization; never log environment variables or HTTP headers.
    shown = ["python"] + [str(Path(arg).relative_to(ROOT)).replace("\\", "/") if arg.startswith(str(ROOT)) else arg for arg in command[1:]]
    log.extend(["$ " + shlex.join(shown), f"exit={exit_code}; elapsed={elapsed}s", stdout.strip(), stderr.strip(), ""])
    print(json.dumps({"case": case["name"], "mode": mode, "iteration": iteration,
                      "safety_passed": record["safety_passed"], "preference_matched": record["preference_matched"]}), flush=True)
    return record


def run_suite(mode, out_dir, repeat=1):
    out_dir = out_dir.resolve()
    if any((out_dir / name).exists() for name in ("summary.json", "progress.json")):
        raise ValueError("output directory already contains a run; use a new directory to preserve evidence")
    out_dir.mkdir(parents=True, exist_ok=True)
    for variant in sorted({case["data"] for case in CASES}):
        shutil.copytree(INPUTS / "data" / variant, out_dir / "fixtures" / variant, dirs_exist_ok=True)
    start = datetime.now(timezone.utc)
    run_id = start.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    runtime_dir = ROOT / ".runtime" / "preferences" / run_id
    runtime_dir.mkdir(parents=True)
    hashes = source_hashes()
    records, baselines, log = [], [], []
    for iteration in range(1, repeat + 1):
        for case in CASES:
            baseline = execute(case, "offline", iteration, out_dir, runtime_dir, log)
            baselines.append(baseline)
            record = execute(case, "live", iteration, out_dir, runtime_dir, log) if mode == "live" else baseline
            records.append(record)
            dump(out_dir / "progress.json", {"run_id": run_id, "mode": mode, "records": records, "earliest_baseline": baselines})
    stable = hashes == source_hashes()
    evidence_valid = (stable and all(r["safety_passed"] and r["rule_oracle"]["matched_expected"] for r in records)
                      and all(r["safety_passed"] for r in baselines))
    live_success = evidence_valid and all(r["preference_matched"] for r in records) if mode == "live" else None
    summary = {
        "run_id": run_id, "mode": mode, "model": "qwen3.8-max" if mode == "live" else "offline-simulator",
        "started_at": start.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(), "platform": platform.system(),
        "distinct_cases": len(CASES), "total_runs": len(records),
        "safe_runs": sum(r["safety_passed"] for r in records),
        "preference_matches": sum(r["preference_matched"] for r in records),
        "earliest_baseline_matches": sum(r["preference_matched"] for r in baselines),
        "structured_rule_oracle_matches": sum(r["rule_oracle"]["matched_expected"] for r in records),
        "provider_calls": sum(r["provider_calls"] for r in records),
        "token_usage": {key: sum(r["token_usage"][key] for r in records)
                        for key in ("prompt_tokens", "completion_tokens", "total_tokens")},
        "evidence_valid": evidence_valid, "live_success": live_success,
        "source_sha256": hashes, "source_unchanged_during_run": stable,
        "fixture_sha256": {p.relative_to(out_dir).as_posix(): digest(p) for p in sorted((out_dir / "fixtures").rglob("*.json"))},
        "records": records, "earliest_baseline": baselines,
        "scope": "Four known synthetic preference cases. Earliest-slot is a weak baseline; the labelled rule oracle already knows the preference. This does not prove superiority over rule systems or general language understanding.",
        "offline_contract": "Safe execution and evidence integrity must pass. Preference misses are retained baseline measurements; offline mode proves no live model decisions.",
    }
    dump(out_dir / "summary.json", summary)
    (out_dir / "run-log.txt").write_text("\n".join(log), encoding="utf-8")
    report = [f"# Preference comparison ({mode})", "", f"Run: `{run_id}`", "",
              f"Safe runs: {summary['safe_runs']}/{len(records)}; preference matches: {summary['preference_matches']}/{len(records)}.",
              f"Actual earliest-slot CLI matches: {summary['earliest_baseline_matches']}/{len(baselines)}; labelled-rule oracle matches: {summary['structured_rule_oracle_matches']}/{len(records)}.",
              f"Provider responses: {summary['provider_calls']}; token usage: {summary['token_usage']}; live_success: {live_success}.", "",
              "Offline misses are expected measurements of an earliest-slot baseline, not evidence of a live model failure or success.",
              "The rule oracle receives an evaluator-provided structured objective and only chooses from observed candidates; it is not an end-to-end language competitor.",
              "No claim is made that an LLM is necessary or better than all rule implementations. Its measured role here is mapping a natural-language preference onto observed candidate facts while preserving hard constraints.", "",
              "| Case | Run | Actual | Expected | Preference matched | Safe |", "|---|---:|---|---|---|---|"]
    report += [f"| {r['case']} | {r['iteration']} | {r['actual_selected_time']} | {r['expected_selected_time']} | {r['preference_matched']} | {r['safety_passed']} |" for r in records]
    report += ["", "Input/actual output/Trace/DB snapshots are adjacent; summary.json records hashes and preserves failed runs.",
               "The unavailable-support gap is derived by the evaluator from observed facts. Saved model reasons are displayed in summary.json, never used as a substring-based judge.",
               "The audience pair shares byte-identical requests and changes only candidate facts; the support pair does the same. The common-data pair changes only the user note.", ""]
    (out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("offline", "live"), default="offline")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()
    if not 1 <= args.repeat <= 3:
        parser.error("--repeat must be between 1 and 3")
    directory = args.out_dir if args.out_dir.is_absolute() else ROOT / args.out_dir
    try:
        result = run_suite(args.mode, directory, args.repeat)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps({k: result[k] for k in ("mode", "total_runs", "safe_runs", "preference_matches",
                                             "earliest_baseline_matches", "provider_calls", "evidence_valid", "live_success")}))
    success = result["live_success"] if args.mode == "live" else result["evidence_valid"]
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
