"""Run the real CLI in isolated databases and assert business outcomes.

No fixture name, fault flag, expected outcome or evaluation assertion is sent to
the model. Live and offline evidence are always stored separately.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shlex
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CASES = [
    {"name": "normal", "expected": "completed", "count": 1},
    {"name": "missing-information", "expected": "needs_input", "count": 0},
    {"name": "rule-block", "expected": "blocked", "count": 0},
    {"name": "time-conflict", "expected": "pending_confirmation", "count": 1, "confirmation": "pending", "hour": 19},
    {"name": "window-authorized", "expected": "completed", "count": 1, "confirmation": "not_required"},
    {"name": "save-failure", "expected": "failed", "count": 0, "fault": "save_fail_permanent"},
    {"name": "risk-confirmation", "expected": "pending_confirmation", "count": 1, "confirmation": "pending"},
    {"name": "conflict-on-save", "expected": "pending_confirmation", "count": 1, "fault": "conflict_on_save", "confirmation": "pending"},
    {"name": "save-response-lost", "expected": "completed", "count": 1, "fault": "save_response_lost"},
    {"name": "tool-failure", "expected": "completed", "count": 1, "fault": "tool_failure"},
    {"name": "step-limit", "expected": "incomplete", "count": 0, "max_steps": 2},
    {"name": "changed-slot", "expected": "pending_confirmation", "count": 1, "variant": "occupied-19", "confirmation": "pending", "hour": 20},
    {"name": "no-slot", "expected": "no_available_slot", "count": 0, "variant": "fully-occupied"},
    {"name": "duplicate-submit", "input": "normal", "expected": "completed", "count": 1, "reuse": "normal"},
]


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes():
    files = sorted((ROOT / "src").glob("*.py")) + sorted((ROOT / "data").glob("*.json"))
    files += [Path(__file__)]
    return {p.relative_to(ROOT).as_posix(): digest(p) for p in files}


def prepare_data(out_dir):
    for variant, hours in (("occupied-19", [19]), ("fully-occupied", [19, 20])):
        target = out_dir / "fixtures" / variant
        for name in ("materials.json", "rules.json", "schedules.json"):
            value = load(ROOT / "data" / name)
            if name == "schedules.json":
                for hour in hours:
                    value["schedules"].append({
                        "schedule_id": f"S{hour}", "material_id": f"EXTERNAL{hour}",
                        "start": f"2026-11-01T{hour:02d}:00:00+08:00",
                        "end": f"2026-11-01T{hour + 1:02d}:00:00+08:00",
                        "channel": "main", "status": "confirmed",
                    })
            dump(target / name, value)


def snapshot_db(path):
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        result = {}
        for table in ("requests", "drafts", "reservations"):
            result[table] = [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
        result["integrity_check"] = conn.execute("PRAGMA integrity_check").fetchone()[0]
        return result


def check_case(case, request, output, trace, snapshot, mode, previous):
    checks = []

    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})

    check("expected_task_status", output.get("task_status") == case["expected"])
    check("expected_database_draft_count", len(snapshot["drafts"]) == case["count"])
    check("sqlite_integrity", snapshot["integrity_check"] == "ok")
    check("original_time_preserved", output.get("original_time") == request["requested_time"])
    check("immutable_request_identity", output.get("request_id") == request["request_id"] and output.get("material_id") == request["material_id"])
    check("decision_budget", output.get("steps", 0) <= case.get("max_steps", 12))
    check("mode_explicit", output.get("mode") == mode)
    if case["count"]:
        check("saved_status_and_database_agree", output.get("save_status") == "saved" and output.get("draft_id") == snapshot["drafts"][0]["draft_id"])
    else:
        check("no_fabricated_draft_id", output.get("draft_id") is None)
        check("not_reported_saved", output.get("save_status") != "saved")
    if "confirmation" in case:
        check("confirmation_boundary", output.get("confirmation_status") == case["confirmation"])
    if "hour" in case and output.get("suggested_time"):
        suggested = datetime.fromisoformat(output["suggested_time"])
        check("changed_availability_reflected", suggested.hour == case["hour"] and suggested.minute == 0)
    if output.get("save_status") == "saved" and request["time_requirement"] == "fixed":
        original = datetime.fromisoformat(request["requested_time"])
        suggested = datetime.fromisoformat(output["suggested_time"])
        check("fixed_change_never_auto_confirmed", original == suggested or output["confirmation_status"] == "pending")
    actions = [entry.get("action") for entry in trace]
    if case["name"] == "conflict-on-save":
        conflicts = [i for i, entry in enumerate(trace) if entry.get("tool", {}).get("result_summary", {}).get("status") == "conflict"]
        check("observed_transactional_conflict", bool(conflicts))
        check("model_requeries_after_conflict", bool(conflicts) and "query_slots" in actions[conflicts[0] + 1:])
    if case["name"] == "save-response-lost":
        unknowns = [i for i, entry in enumerate(trace) if entry.get("tool", {}).get("result_summary", {}).get("status") == "unknown"]
        check("observed_unknown_save_outcome", bool(unknowns))
        check("model_reconciles_unknown_save", bool(unknowns) and "save_draft" in actions[unknowns[0] + 1:])
        check("retry_is_idempotent", any(entry.get("tool", {}).get("result_summary", {}).get("idempotent") is True for entry in trace))
    if case["name"] == "save-failure":
        check("failure_explicit", output.get("save_status") == "failed")
    if case["name"] == "step-limit":
        check("limit_is_explicit_incomplete", output.get("stop_reason") == "max_steps_reached" and output.get("task_status") == "incomplete")
    if case["name"] == "duplicate-submit":
        check("cross_process_same_draft", output.get("draft_id") == previous["draft_id"])
        check("recovered_without_new_decision", output.get("steps") == 0 and any(e.get("event") == "idempotent_recovery" for e in trace))
    elif mode == "live":
        provider = [entry["provider"] for entry in trace if entry.get("provider")]
        check("actual_provider_model_evidence", bool(provider) and all(p.get("requested_model") == "qwen3.8-max" and p.get("response_model") == "qwen3.8-max" and p.get("response_id") for p in provider))
    return checks


def run_suite(mode, out_dir, selected=None, repeat=1):
    started_at = datetime.now(timezone.utc)
    run_id = started_at.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    runtime_dir = ROOT / ".runtime" / "evaluation" / run_id
    runtime_dir.mkdir(parents=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    prepare_data(out_dir)
    requested = set(selected or [c["name"] for c in CASES])
    if "duplicate-submit" in requested:
        requested.add("normal")
    unknown = requested - {c["name"] for c in CASES}
    if unknown:
        raise ValueError("unknown cases: " + ", ".join(sorted(unknown)))
    records, log = [], []
    hashes = source_hashes()
    for iteration in range(1, repeat + 1):
        previous = {}
        for case in CASES:
            if case["name"] not in requested:
                continue
            name = case["name"]
            prefix = f"{name}.r{iteration:02d}" if repeat > 1 else name
            request = load(ROOT / "examples" / "inputs" / (case.get("input", name) + ".json"))
            # The availability pair uses byte-identical business input.
            if name == "changed-slot":
                request = load(ROOT / "examples" / "inputs" / "time-conflict.json")
            input_path = out_dir / (prefix + ".input.json")
            output_path = out_dir / (prefix + ".output.json")
            trace_path = out_dir / (prefix + ".trace.jsonl")
            db_path = runtime_dir / f"{case.get('reuse', name)}.r{iteration:02d}.sqlite"
            data_dir = out_dir / "fixtures" / case["variant"] if case.get("variant") else ROOT / "data"
            dump(input_path, request)
            args = [sys.executable, "-m", "src.main", "--input", str(input_path), "--mode", mode,
                    "--db", str(db_path), "--output", str(output_path), "--trace", str(trace_path),
                    "--data-dir", str(data_dir), "--max-steps", str(case.get("max_steps", 12))]
            if case.get("fault"):
                args += ["--fault", case["fault"]]
            began = time.perf_counter()
            process = subprocess.run(args, cwd=ROOT, text=True, encoding="utf-8", capture_output=True, timeout=600)
            elapsed = round(time.perf_counter() - began, 3)
            output = load(output_path)
            trace = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line]
            snapshot = snapshot_db(db_path)
            snapshot_path = out_dir / (prefix + ".db-snapshot.json")
            dump(snapshot_path, snapshot)
            checks = check_case(case, request, output, trace, snapshot, mode, previous.get("normal"))
            checks.append({"name": "documented_cli_exit_status", "passed": process.returncode == (1 if case["expected"] == "incomplete" else 0)})
            record = {"case": name, "iteration": iteration, "mode": mode, "expected": case["expected"],
                      "actual": output.get("task_status"), "passed": all(c["passed"] for c in checks),
                      "checks": checks, "elapsed_seconds": elapsed, "exit_code": process.returncode,
                      "steps": output.get("steps"), "draft_count": len(snapshot["drafts"]),
                      "action_path": [e.get("action", e.get("event")) for e in trace],
                      "provider_calls": sum(bool(e.get("provider", {}).get("response_id")) for e in trace),
                      "guard_rejections": sum(e.get("validation") in ("rejected", "tool_rejected") for e in trace),
                      "artifacts": {p.name: digest(p) for p in (input_path, output_path, trace_path, snapshot_path)}}
            records.append(record)
            previous[name] = output
            display_args = ["python"] + [str(Path(arg).relative_to(ROOT)).replace("\\", "/") if arg.startswith(str(ROOT)) else arg for arg in args[1:]]
            command = shlex.join(display_args)
            log += [f"$ {command}", f"exit={process.returncode}, elapsed={elapsed}s", process.stdout.strip(), process.stderr.strip(), ""]
            print(json.dumps({"case": name, "iteration": iteration, "passed": record["passed"], "actual": record["actual"], "steps": record["steps"]}), flush=True)
            # Progress is durable, even if the batch is interrupted later.
            dump(out_dir / "progress.json", {"run_id": run_id, "completed": records})
    summary = {"run_id": run_id, "mode": mode, "model": "qwen3.8-max" if mode == "live" else "offline-simulator",
               "started_at": started_at.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
               "python": platform.python_version(), "platform": platform.system(),
               "total_runs": len(records), "passed_runs": sum(r["passed"] for r in records),
               "total_assertions": sum(len(r["checks"]) for r in records),
               "passed_assertions": sum(c["passed"] for r in records for c in r["checks"]),
               "provider_calls": sum(r["provider_calls"] for r in records),
               "scope": "Finite synthetic scenarios; this does not establish general model reliability.",
               "source_sha256": hashes, "records": records}
    dump(out_dir / "summary.json", summary)
    (out_dir / "run-log.txt").write_text("\n".join(log), encoding="utf-8")
    report = [f"# {mode} evaluation", "", f"Run: `{run_id}`", "",
              f"Passed: {summary['passed_runs']}/{summary['total_runs']} runs; {summary['passed_assertions']}/{summary['total_assertions']} assertions.",
              f"Actual provider responses: {summary['provider_calls']}.", "",
              "Offline results verify process constraints only. Live results are a finite synthetic sample, not a general accuracy estimate.", "",
              "| Case | Run | Expected | Actual | Passed | Steps |", "|---|---:|---|---|---|---:|"]
    report += [f"| {r['case']} | {r['iteration']} | {r['expected']} | {r['actual']} | {r['passed']} | {r['steps']} |" for r in records]
    report += ["", "Each case has input, actual output, JSONL trace and SQLite snapshot beside this report.",
               "`summary.json` binds artifacts to SHA-256 hashes and the exact source used.", ""]
    (out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("offline", "live"), default="offline")
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--cases", help="comma-separated subset of case names")
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()
    if not 1 <= args.repeat <= 10:
        parser.error("--repeat must be between 1 and 10")
    out = args.out_dir or ROOT / "examples" / args.mode
    if not out.is_absolute():
        out = ROOT / out
    result = run_suite(args.mode, out, args.cases.split(",") if args.cases else None, args.repeat)
    print(json.dumps({key: result[key] for key in ("mode", "total_runs", "passed_runs", "provider_calls")}))
    return 0 if result["passed_runs"] == result["total_runs"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
