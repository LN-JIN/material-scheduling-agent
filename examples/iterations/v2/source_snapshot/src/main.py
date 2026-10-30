"""CLI for offline process testing and live Qwen decisions; never publishes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .agent import run_agent, sanitize
from .models import ModelError, OfflineModel, QwenModel
from .store import DraftStore
from .tools import ToolSet


def main(argv=None):
    parser = argparse.ArgumentParser(description="Material review and scheduling draft agent")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--mode", choices=("offline", "live"), default="offline")
    parser.add_argument("--db", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--trace", required=True, type=Path)
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parents[1] / "data")
    parser.add_argument("--max-steps", type=int, default=12)
    parser.add_argument("--fault", choices=("save_fail_before_commit", "save_fail_permanent", "conflict_on_save", "save_response_lost", "tool_failure"))
    args = parser.parse_args(argv)
    store = None
    request = {}
    try:
        request = json.loads(args.input.read_text(encoding="utf-8-sig"))
        model = OfflineModel() if args.mode == "offline" else QwenModel()
        store = DraftStore(args.db)
        tools = ToolSet(args.data_dir, store, fault=args.fault)
        result = run_agent(request, model, tools, args.max_steps)
    except (OSError, ValueError, ModelError) as exc:
        result = {"mode": args.mode, "model": "offline-simulator" if args.mode == "offline" else "qwen3.8-max",
                  "request_id": request.get("request_id") if isinstance(request, dict) else None,
                  "material_id": request.get("material_id") if isinstance(request, dict) else None,
                  "original_time": request.get("requested_time") if isinstance(request, dict) else None,
                  "review": None, "risks": [], "suggested_time": None, "confirmation_status": "not_evaluated",
                  "review_status": None, "risk_reasons": [], "missing_fields": [], "pending_items": [],
                  "draft_status": None, "run_completed": False, "next_step": "correct_configuration_or_input",
                  "task_status": "rejected", "save_status": "not_saved", "draft_id": None,
                  "stop_reason": sanitize(str(exc)), "steps": 0,
                  "trace": [{"event": "initialization_rejected", "validation": "rejected", "error": sanitize(str(exc))}]}
    finally:
        if store:
            store.close()
    trace = result.pop("trace", [])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.trace.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(sanitize(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # JSONL works for both manual inspection and automated regression checks.
    args.trace.write_text("".join(json.dumps(sanitize(entry), ensure_ascii=False) + "\n" for entry in trace), encoding="utf-8")
    print(json.dumps({"task_status": result["task_status"], "save_status": result["save_status"],
                      "draft_id": result.get("draft_id"), "steps": result.get("steps", 0)}, ensure_ascii=False))
    return 2 if result["task_status"] == "rejected" else (1 if result["task_status"] == "incomplete" else 0)


if __name__ == "__main__":
    sys.exit(main())
