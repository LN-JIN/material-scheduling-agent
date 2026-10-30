"""Independent revision checks using real SQLite errors and changed business data.

No live model is called. These tests establish program behavior, not model quality.
The data below was added after the initial submission and its 58-test acceptance.
"""
from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from src.agent import run_agent
from src.models import OfflineModel
from src.store import DraftStore
from src.tools import ToolSet


PROJECT = Path(__file__).resolve().parents[1]
START = "2027-02-11T23:40:00+05:45"


class RevisionCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.db = self.root / "drafts.sqlite"
        self.material = {
            "material_id": "CLIP-Z9", "title": "Independent fixture",
            "text": "Synthetic material with editorial rights.",
            "rights_status": "licensed", "channel": "reels-east",
            "playback_duration_seconds": 91,
        }
        self.rules = {
            "blocked_terms": ["RIGHTS-EXCLUDED"],
            "risk_terms": ["REVIEW-MARKER"],
            "scheduling_policy": {"posting_interval_minutes": 37},
        }
        self.schedules = []
        self.write_data()

    def write_data(self):
        for filename, payload in (
            ("materials.json", {"materials": [self.material]}),
            ("rules.json", self.rules),
            ("schedules.json", {"schedules": self.schedules}),
        ):
            (self.data / filename).write_text(json.dumps(payload), encoding="utf-8")

    def request(self, **changes):
        value = {"request_id": "R-new-data", "material_id": "CLIP-Z9",
                 "requested_time": START, "time_requirement": "fixed"}
        value.update(changes)
        return value

    def tools(self):
        store = DraftStore(self.db)
        self.addCleanup(store.close)
        return store, ToolSet(self.data, store)

    def prepared(self):
        store, tools = self.tools()
        tools.bind(self.request())
        tools.read_material()
        tools.check_rules()
        tools.query_slots()
        return store, tools

    def run_offline(self, request=None):
        store, tools = self.tools()
        result = run_agent(request or self.request(), OfflineModel(), tools)
        return result, store

    def cli(self, request=None, name="run"):
        source = self.root / (name + ".input.json")
        output = self.root / (name + ".output.json")
        trace = self.root / (name + ".trace.jsonl")
        source.write_text(json.dumps(request or self.request()), encoding="utf-8")
        process = subprocess.run(
            [sys.executable, "-m", "src.main", "--mode", "offline",
             "--input", str(source), "--data-dir", str(self.data), "--db", str(self.db),
             "--output", str(output), "--trace", str(trace)],
            cwd=PROJECT, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertTrue(output.exists(), process.stderr)
        self.assertTrue(trace.exists(), process.stderr)
        result = json.loads(output.read_text(encoding="utf-8"))
        events = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
        return process, result, events

    def count(self):
        if not self.db.exists():
            return 0
        with closing(sqlite3.connect(self.db)) as conn:
            tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='drafts'").fetchone()
            return conn.execute("SELECT count(*) FROM drafts").fetchone()[0] if tables else 0

    def assert_rejected_configuration(self, name="run"):
        process, result, _ = self.cli(name=name)
        self.assertEqual(process.returncode, 2, process.stderr)
        self.assertEqual(result["task_status"], "rejected")
        self.assertNotEqual(result.get("review_status"), "pass")
        self.assertNotEqual(result.get("save_status"), "saved")
        self.assertEqual(self.count(), 0)


class PolicyAvailabilityTests(RevisionCase):
    def test_missing_policy_file_never_becomes_no_risk(self):
        self.material["text"] += " REVIEW-MARKER"
        self.write_data()
        (self.data / "rules.json").unlink()
        self.assert_rejected_configuration()

    def test_invalid_json_has_structured_configuration_rejection(self):
        (self.data / "rules.json").write_text('{"risk_terms":', encoding="utf-8")
        self.assert_rejected_configuration()

    def test_wrong_policy_root_types_are_rejected(self):
        for index, payload in enumerate((None, [], "policy", 12, True)):
            with self.subTest(payload=payload):
                self.rules = payload
                self.write_data()
                self.assert_rejected_configuration(name=f"root-{index}")

    def test_wrong_rule_containers_are_rejected(self):
        for index, payload in enumerate((
            {"blocked_terms": "RIGHTS-EXCLUDED"},
            {"risk_terms": None}, {"rules": {}}, {"rules": ["not-a-rule"]},
        )):
            with self.subTest(payload=payload):
                self.rules = payload
                self.write_data()
                self.assert_rejected_configuration(name=f"container-{index}")

    def test_unsupported_rule_type_cannot_approve_material(self):
        self.rules["rules"] = [{"id": "unsupported", "type": "external_approval", "severity": "hard"}]
        self.write_data()
        self.assert_rejected_configuration()

    def test_unknown_rule_severity_cannot_silently_downgrade_to_risk(self):
        self.rules["rules"] = [{"id": "typo", "type": "blocked_terms", "severity": "hardd", "pattern": "Synthetic"}]
        self.write_data()
        self.assert_rejected_configuration()

    def test_explicit_empty_policy_still_checks_required_metadata(self):
        self.rules = {}
        self.material["rights_status"] = "denied"
        self.write_data()
        result, _ = self.run_offline()
        self.assertEqual(result["task_status"], "blocked")
        self.assertEqual(self.count(), 0)

    def test_required_custom_field_null_is_missing_not_string_none(self):
        self.material["editorial_approval"] = None
        self.rules["rules"] = [{"id": "approval-record", "type": "required_field", "field": "editorial_approval", "severity": "hard"}]
        self.write_data()
        result, _ = self.run_offline()
        self.assertEqual(result["task_status"], "blocked")
        self.assertEqual(self.count(), 0)


class NativeStorageFailureTests(RevisionCase):
    def test_real_sqlite_insert_rejection_reports_save_failure(self):
        store, tools = self.tools()
        store.connection.execute("CREATE TRIGGER reject_write BEFORE INSERT ON drafts BEGIN SELECT RAISE(ABORT, 'storage rejected insert'); END")
        result = run_agent(self.request(), OfflineModel(), tools)
        self.assertEqual(result["task_status"], "failed")
        self.assertEqual(result["save_status"], "failed")
        self.assertEqual(self.count(), 0)
        self.assertNotEqual(result["stop_reason"], "model_call_failed")
        save_events = [event for event in result["trace"] if event.get("action") == "save_draft"]
        self.assertTrue(save_events)
        self.assertTrue(all(event.get("validation") != "model_error" for event in save_events))
        self.assertTrue(all(event.get("tool", {}).get("name") == "save_draft" for event in save_events))

    def test_readonly_save_is_definitely_failed_and_creates_no_draft(self):
        store, tools = self.prepared()
        store.connection.execute("PRAGMA query_only=ON")
        observed = tools.save_draft(suggested_time=START)
        self.assertEqual(observed["status"], "failed")
        self.assertEqual(tools.save_status, "failed")
        self.assertEqual(self.count(), 0)
        self.assertFalse(store.connection.in_transaction)

    def test_real_save_lock_can_retry_after_owner_releases_it(self):
        store, tools = self.prepared()
        store.connection.execute("PRAGMA busy_timeout=1")
        other = sqlite3.connect(self.db, isolation_level=None)
        self.addCleanup(other.close)
        other.execute("BEGIN IMMEDIATE")
        try:
            failed = tools.save_draft(suggested_time=START)
        finally:
            other.execute("ROLLBACK")
        self.assertEqual(failed["status"], "failed")
        self.assertIs(failed.get("retryable"), True)
        self.assertEqual(self.count(), 0)
        saved = tools.save_draft(suggested_time=START)
        self.assertEqual(saved["status"], "saved")
        self.assertEqual(self.count(), 1)

    def test_corrupt_database_produces_structured_cli_rejection(self):
        self.db.write_bytes(b"Not a SQLite database, independent fixture.\n")
        process, result, _ = self.cli()
        self.assertEqual(process.returncode, 2, process.stderr)
        self.assertEqual(result["task_status"], "rejected")
        self.assertEqual(result["stop_reason"], "storage_unavailable")
        self.assertNotEqual(result["save_status"], "saved")
        self.assertNotIn("Traceback", process.stderr)

    def test_native_request_binding_failure_is_not_model_failure(self):
        store = DraftStore(self.db)
        store.connection.execute("CREATE TRIGGER reject_binding BEFORE INSERT ON requests BEGIN SELECT RAISE(ABORT, 'storage rejected binding'); END")
        store.close()
        process, result, _ = self.cli()
        self.assertEqual(process.returncode, 2, process.stderr)
        self.assertEqual(result["task_status"], "rejected")
        self.assertEqual(result["stop_reason"], "storage_unavailable")
        self.assertEqual(self.count(), 0)


class ChangedBusinessDataTests(RevisionCase):
    def test_publishing_interval_is_not_video_playback_duration(self):
        result, store = self.run_offline()
        saved = store.get_draft(self.request()["request_id"], self.material["material_id"])
        self.assertEqual(result["task_status"], "completed")
        self.assertEqual(datetime.fromisoformat(saved["end_time"]) - datetime.fromisoformat(saved["suggested_time"]), timedelta(minutes=37))

    def test_explicit_policy_takes_precedence_over_legacy_duration(self):
        self.material["duration_minutes"] = 9
        self.write_data()
        _, store = self.run_offline()
        saved = store.get_draft(self.request()["request_id"], self.material["material_id"])
        self.assertEqual(datetime.fromisoformat(saved["end_time"]) - datetime.fromisoformat(saved["suggested_time"]), timedelta(minutes=37))

    def test_playback_metadata_change_does_not_change_slot_occupancy(self):
        self.material["playback_duration_seconds"] = 2400
        self.write_data()
        _, store = self.run_offline()
        saved = store.get_draft(self.request()["request_id"], self.material["material_id"])
        self.assertEqual(datetime.fromisoformat(saved["end_time"]) - datetime.fromisoformat(saved["suggested_time"]), timedelta(minutes=37))

    def test_explicit_policy_interval_must_be_finite_positive_number(self):
        for index, interval in enumerate((0, -3, True, "37", float("nan"), float("inf"))):
            with self.subTest(interval=interval):
                self.rules["scheduling_policy"]["posting_interval_minutes"] = interval
                self.write_data()
                self.assert_rejected_configuration(name=f"interval-{index}")

    def test_cross_midnight_equivalent_timezone_conflict_preserves_both_pending_items(self):
        self.material["text"] += " REVIEW-MARKER"
        self.schedules = [{"schedule_id": "fractional-zone-occupancy", "start": "2027-02-11T17:55:00Z",
                           "end": "2027-02-11T18:32:00Z", "channel": "reels-east"}]
        self.write_data()
        result, _ = self.run_offline()
        self.assertEqual(datetime.fromisoformat(result["suggested_time"]), datetime.fromisoformat("2027-02-12T00:17:00+05:45"))
        self.assertEqual(result["confirmation_status"], "pending")
        self.assertEqual({item["type"] for item in result["pending_items"]}, {"time_change", "rule_risk"})
        self.assertEqual(result["original_time"], START)
        self.assertEqual(self.count(), 1)

    def test_identical_time_on_distinct_channel_does_not_conflict(self):
        self.schedules = [{"schedule_id": "other-channel", "start": START,
                           "end": "2027-02-12T00:17:00+05:45", "channel": "reels-west"}]
        self.write_data()
        result, _ = self.run_offline()
        self.assertEqual(result["suggested_time"], START)
        self.assertEqual(result["confirmation_status"], "not_required")
        self.assertEqual(self.count(), 1)

    def test_window_must_fit_posting_interval_even_for_short_clip(self):
        self.material["playback_duration_seconds"] = 3
        self.write_data()
        request = self.request(time_requirement="window", allowed_start=START,
                               allowed_end="2027-02-12T00:16:00+05:45")
        result, _ = self.run_offline(request)
        self.assertEqual(result["task_status"], "no_available_slot")
        self.assertEqual(self.count(), 0)

    def test_same_request_changed_preference_note_cannot_reuse_draft(self):
        original = self.request(note="Prefer an early editorial review.")
        first, _ = self.run_offline(original)
        changed, _ = self.run_offline(self.request(note="Prefer the last available slot."))
        self.assertEqual(first["save_status"], "saved")
        self.assertEqual(changed["task_status"], "rejected")
        self.assertEqual(self.count(), 1)


if __name__ == "__main__":
    unittest.main()
