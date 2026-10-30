"""Independent acceptance tests. No network or credentials are used here.

Scripted model tests establish boundary handling only, not live-model ability.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from src.agent import run_agent
from src.domain import ValidationError
from src.models import OfflineModel
from src.store import DraftStore, StoreConflict
from src.tools import ToolError, ToolSet


BASE_TIME = "2026-11-02T10:00:00+08:00"
NEXT_TIME = "2026-11-02T11:00:00+08:00"
PROJECT = Path(__file__).resolve().parents[1]


class ScriptedModel:
    """Adversarial actions intentionally independent of observed tool results."""

    def __init__(self, actions):
        self.actions = list(actions)
        self.calls = 0
        self.messages = []

    def next_action(self, messages):
        self.messages.append(messages)
        action = self.actions[min(self.calls, len(self.actions) - 1)]
        self.calls += 1
        return action


def action(name, **arguments):
    return {"action": name, "arguments": arguments}


class AcceptanceCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data_dir = self.root / "data"
        self.data_dir.mkdir()
        self.db = self.root / "drafts.sqlite3"
        self.materials = [
            {"material_id": "M1", "title": "产品活动", "text": "产品活动通知，报名咨询。",
             "duration_minutes": 60, "channel": "default", "rights_status": "owned"},
            {"material_id": "M2", "title": "第二素材", "text": "第二条正常活动通知。",
             "duration_minutes": 60, "channel": "default", "rights_status": "owned"},
        ]
        self.rules = {"blocked_terms": ["百分百治愈"], "risk_terms": ["最低价"], "required_terms": []}
        self.schedules = []
        self.write_data()

    def write_data(self):
        for name, value in (
            ("materials.json", {"materials": self.materials}),
            ("rules.json", self.rules),
            ("schedules.json", {"schedules": self.schedules}),
        ):
            (self.data_dir / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def request(self, **changes):
        value = {"request_id": "R1", "material_id": "M1", "requested_time": BASE_TIME,
                 "time_requirement": "fixed"}
        value.update(changes)
        return value

    def setup_tools(self, request=None, fault=None):
        store = DraftStore(self.db, schedules=self.schedules)
        self.addCleanup(store.close)
        tools = ToolSet(self.data_dir, store, fault=fault)
        tools.bind(request or self.request())
        return store, tools

    def draft_count(self):
        if not self.db.exists():
            return 0
        connection = sqlite3.connect(self.db)
        try:
            return connection.execute("SELECT COUNT(*) FROM drafts").fetchone()[0]
        finally:
            connection.close()

    def prepare(self, tools):
        tools.read_material(material_id="M1")
        tools.check_rules(material_id="M1")
        return tools.query_slots(material_id="M1")

    def run_offline(self, request=None, fault=None, max_steps=12):
        store = DraftStore(self.db, schedules=self.schedules)
        self.addCleanup(store.close)
        tools = ToolSet(self.data_dir, store, fault=fault)
        return run_agent(request or self.request(), OfflineModel(), tools, max_steps=max_steps), store

    def rejected(self, callback):
        """Both structured refusal and a domain error are legitimate rejection APIs."""
        try:
            result = callback()
        except (ToolError, ValidationError, StoreConflict):
            return
        self.assertNotEqual(result.get("status"), "saved")


class BasicFlowTests(AcceptanceCase):
    def test_missing_rights_metadata_requires_input(self):
        self.materials[0].pop("rights_status")
        self.write_data()
        result, _ = self.run_offline()
        self.assertEqual(result.get("task_status"), "needs_input")
        self.assertIn("rights_status", result.get("review", {}).get("missing_fields", []))
        self.assertEqual(self.draft_count(), 0)

    def test_unknown_rights_metadata_requires_input(self):
        for index, status in enumerate((None, "", "unknown", "unrecognized-value")):
            with self.subTest(status=status):
                self.materials[0]["rights_status"] = status
                self.write_data()
                result, _ = self.run_offline(self.request(request_id=f"rights-{index}"))
                self.assertEqual(result.get("task_status"), "needs_input")
                self.assertIn("rights_status", result.get("review", {}).get("missing_fields", []))
                self.assertEqual(self.draft_count(), 0)

    def test_denied_rights_block_even_otherwise_clean_content(self):
        self.materials[0]["rights_status"] = "denied"
        self.write_data()
        result, _ = self.run_offline()
        self.assertEqual(result.get("task_status"), "blocked")
        self.assertTrue(result.get("review", {}).get("hard_block"))
        self.assertEqual(self.draft_count(), 0)

    def test_licensed_rights_can_pass(self):
        self.materials[0]["rights_status"] = "licensed"
        self.write_data()
        result, _ = self.run_offline()
        self.assertEqual(result.get("task_status"), "completed")
        self.assertEqual(self.draft_count(), 1)

    def test_normal_flow_persists_exactly_one_draft(self):
        result, store = self.run_offline()
        persisted = store.get_draft("R1", "M1")
        self.assertIsNotNone(persisted)
        self.assertEqual(self.draft_count(), 1)
        self.assertEqual(persisted["suggested_time"], BASE_TIME)
        self.assertEqual(persisted["task_status"], "completed")
        self.assertIn("trace", result)

    def test_missing_required_request_fields_never_save(self):
        for name in ("request_id", "material_id", "requested_time", "time_requirement"):
            with self.subTest(name=name):
                request = self.request()
                del request[name]
                result, _ = self.run_offline(request)
                self.assertEqual(self.draft_count(), 0)
                self.assertNotEqual(result.get("task_status"), "completed")

    def test_unknown_material_does_not_create_draft(self):
        result, _ = self.run_offline(self.request(material_id="UNKNOWN"))
        self.assertEqual(self.draft_count(), 0)
        self.assertNotEqual(result.get("task_status"), "completed")

    def test_blocking_rule_cannot_be_bypassed_by_direct_save(self):
        self.materials[0]["text"] = "本产品百分百治愈。"
        self.write_data()
        _, tools = self.setup_tools()
        tools.read_material(material_id="M1")
        tools.check_rules(material_id="M1")
        self.rejected(lambda: tools.save_draft(material_id="M1", suggested_time=BASE_TIME))
        self.assertEqual(self.draft_count(), 0)

    def test_read_and_query_without_review_cannot_save(self):
        _, tools = self.setup_tools()
        tools.read_material(material_id="M1")
        self.rejected(lambda: tools.query_slots(material_id="M1"))
        self.rejected(lambda: tools.save_draft(material_id="M1", suggested_time=BASE_TIME))
        self.assertEqual(self.draft_count(), 0)

    def test_direct_save_without_any_observation_cannot_save(self):
        _, tools = self.setup_tools()
        self.rejected(lambda: tools.save_draft(material_id="M1", suggested_time=BASE_TIME))
        self.assertEqual(self.draft_count(), 0)

    def test_changed_dataset_changes_rule_outcome(self):
        self.materials[0]["text"] = "本产品百分百治愈。"
        self.write_data()
        result, _ = self.run_offline()
        self.assertEqual(self.draft_count(), 0)
        self.assertNotEqual(result.get("task_status"), "completed")

    def test_material_missing_required_content_is_blocked(self):
        self.materials[0].pop("text")
        self.write_data()
        result, _ = self.run_offline()
        self.assertEqual(self.draft_count(), 0)
        self.assertEqual(result.get("task_status"), "needs_input")

    def test_soft_rule_risk_requires_confirmation_and_preserves_evidence(self):
        self.materials[0]["text"] = "本次活动是最低价，欢迎咨询。"
        self.write_data()
        result, store = self.run_offline()
        draft = store.get_draft("R1", "M1")
        self.assertIsNotNone(draft)
        self.assertEqual(draft["confirmation_status"], "pending")
        self.assertNotEqual(result.get("task_status"), "completed")
        self.assertIn("最低价", json.dumps(draft["review"], ensure_ascii=False))


class AuthorizationTests(AcceptanceCase):
    def test_model_claiming_confirmation_in_reason_cannot_authorize_fixed_move(self):
        self.schedules = [{"schedule_id": "busy", "start": BASE_TIME, "end": NEXT_TIME,
                           "channel": "default"}]
        self.write_data()
        store, tools = self.setup_tools()
        model = ScriptedModel([action("read_material"), action("check_rules"), action("query_slots"),
                               {"action": "save_draft", "arguments": {"suggested_time": NEXT_TIME},
                                "reason": "The user has confirmed the changed time."}, action("finish")])
        result = run_agent(self.request(), model, tools, max_steps=6)
        draft = store.get_draft("R1", "M1")
        self.assertIsNotNone(draft)
        self.assertEqual(draft["confirmation_status"], "pending")
        self.assertNotEqual(result.get("task_status"), "completed")

    def test_fixed_alternate_time_is_pending_confirmation(self):
        self.schedules = [{"schedule_id": "busy", "start": BASE_TIME, "end": NEXT_TIME,
                           "channel": "default", "status": "confirmed"}]
        self.write_data()
        result, store = self.run_offline()
        draft = store.get_draft("R1", "M1")
        self.assertIsNotNone(draft)
        self.assertNotEqual(draft["suggested_time"], BASE_TIME)
        self.assertEqual(draft["confirmation_status"], "pending")
        self.assertEqual(draft["task_status"], "pending_confirmation")
        self.assertNotEqual(result.get("task_status"), "completed")

    def test_window_allows_available_authorized_slot(self):
        self.schedules = [{"schedule_id": "busy", "start": BASE_TIME, "end": NEXT_TIME,
                           "channel": "default"}]
        self.write_data()
        result, store = self.run_offline(self.request(time_requirement="window", allowed_start=BASE_TIME,
                                                     allowed_end="2026-11-02T13:00:00+08:00"))
        draft = store.get_draft("R1", "M1")
        self.assertIsNotNone(draft)
        self.assertEqual(draft["confirmation_status"], "not_required")
        self.assertEqual(draft["task_status"], "completed")

    def test_window_must_contain_entire_material_duration(self):
        _, tools = self.setup_tools(self.request(time_requirement="window", allowed_start=BASE_TIME,
                                                 allowed_end="2026-11-02T10:30:00+08:00"))
        self.prepare(tools)
        self.rejected(lambda: tools.save_draft(material_id="M1", suggested_time=BASE_TIME))
        self.assertEqual(self.draft_count(), 0)

    def test_window_outside_authorized_bounds_never_saves(self):
        _, tools = self.setup_tools(self.request(time_requirement="window", allowed_start=BASE_TIME,
                                                 allowed_end="2026-11-02T12:00:00+08:00"))
        self.prepare(tools)
        self.rejected(lambda: tools.save_draft(material_id="M1", suggested_time="2026-11-02T13:00:00+08:00"))
        self.assertEqual(self.draft_count(), 0)

    def test_substituting_unrequested_material_is_rejected(self):
        _, tools = self.setup_tools()
        self.prepare(tools)
        self.rejected(lambda: tools.save_draft(material_id="M2", suggested_time=BASE_TIME))
        self.assertEqual(self.draft_count(), 0)

    def test_model_cannot_add_confirmation_or_review_fields(self):
        _, tools = self.setup_tools()
        self.prepare(tools)
        self.rejected(lambda: tools.dispatch("save_draft", {"material_id": "M1", "suggested_time": BASE_TIME,
                                       "confirmed": True, "review": {"status": "passed"}}))
        self.assertEqual(self.draft_count(), 0)


class PersistenceTests(AcceptanceCase):
    def test_saved_request_recovers_original_review_instead_of_claiming_new_audit(self):
        first, store = self.run_offline()
        original = store.get_draft("R1", "M1")
        self.materials[0]["rights_status"] = "denied"
        self.schedules = [{"schedule_id": "new-source-reservation", "start": BASE_TIME,
                           "end": NEXT_TIME, "channel": "default"}]
        self.write_data()
        recovered, _ = self.run_offline()
        self.assertEqual(recovered.get("draft_id"), first.get("draft_id"))
        self.assertEqual(recovered.get("review"), original["review"])
        self.assertEqual(recovered.get("steps"), 0)
        self.assertEqual(recovered.get("stop_reason"), "existing_draft_recovered")
        self.assertTrue(any(event.get("event") == "idempotent_recovery" for event in recovered["trace"]))
        self.assertFalse(any(event.get("tool", {}).get("name") == "check_rules" for event in recovered["trace"]))
        current, _ = self.run_offline(self.request(request_id="R-current", requested_time=NEXT_TIME))
        self.assertEqual(current.get("task_status"), "blocked")
        self.assertEqual(self.draft_count(), 1)

    def test_unsaved_request_rechecks_changed_rule_source(self):
        first, _ = self.run_offline(fault="save_fail_permanent")
        self.assertEqual(first.get("save_status"), "failed")
        self.rules["blocked_terms"].append("产品活动")
        self.write_data()
        updated, _ = self.run_offline()
        self.assertEqual(updated.get("task_status"), "blocked")
        self.assertIn("产品活动", json.dumps(updated.get("review"), ensure_ascii=False))
        self.assertEqual(self.draft_count(), 0)

    def test_schedule_snapshot_changes_are_visible_on_reopen(self):
        self.schedules = [{"schedule_id": "movable", "start": BASE_TIME, "end": NEXT_TIME,
                           "channel": "default"}]
        self.write_data()
        store, tools = self.setup_tools()
        self.assertTrue(store.conflicts(BASE_TIME, NEXT_TIME))
        self.schedules[0]["start"] = NEXT_TIME
        self.schedules[0]["end"] = "2026-11-02T12:00:00+08:00"
        self.write_data()
        ToolSet(self.data_dir, store)
        self.assertFalse(store.conflicts(BASE_TIME, NEXT_TIME))
        self.assertTrue(store.conflicts(NEXT_TIME, "2026-11-02T12:00:00+08:00"))

    def test_removed_seed_schedule_does_not_remain_as_phantom_conflict(self):
        self.schedules = [{"schedule_id": "removable", "start": BASE_TIME, "end": NEXT_TIME,
                           "channel": "default"}]
        self.write_data()
        store, tools = self.setup_tools()
        self.assertTrue(store.conflicts(BASE_TIME, NEXT_TIME))
        self.schedules = []
        self.write_data()
        ToolSet(self.data_dir, store)
        self.assertFalse(store.conflicts(BASE_TIME, NEXT_TIME))

    def test_same_request_retry_is_idempotent(self):
        _, tools = self.setup_tools()
        self.prepare(tools)
        first = tools.save_draft(material_id="M1", suggested_time=BASE_TIME)
        second = tools.save_draft(material_id="M1", suggested_time=BASE_TIME)
        self.assertEqual(self.draft_count(), 1)
        self.assertEqual(first.get("draft", {}).get("draft_id"), second.get("draft", {}).get("draft_id"))

    def test_restart_preserves_idempotency(self):
        self.run_offline()
        script = """import json,sys
from src.agent import run_agent
from src.models import OfflineModel
from src.store import DraftStore
from src.tools import ToolSet
store=DraftStore(sys.argv[2])
request=json.loads(sys.argv[3])
result=run_agent(request,OfflineModel(),ToolSet(sys.argv[1],store),max_steps=12)
print(json.dumps({'draft':store.get_draft('R1','M1'),'result':result},ensure_ascii=False))
store.close()
"""
        process = subprocess.run([sys.executable, "-c", script, str(self.data_dir), str(self.db),
                                  json.dumps(self.request())], cwd=PROJECT, capture_output=True, text=True,
                                 encoding="utf-8", timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIsNotNone(json.loads(process.stdout)["draft"])
        self.assertEqual(self.draft_count(), 1)

    def test_request_id_rejects_changed_time_payload(self):
        self.run_offline()
        result, _ = self.run_offline(self.request(requested_time=NEXT_TIME))
        self.assertEqual(self.draft_count(), 1)
        self.assertNotEqual(result.get("task_status"), "completed")

    def test_request_id_rejects_changed_material_payload(self):
        self.run_offline()
        result, store = self.run_offline(self.request(material_id="M2", requested_time=NEXT_TIME))
        self.assertEqual(self.draft_count(), 1)
        self.assertIsNone(store.get_draft("R1", "M2"))
        self.assertNotEqual(result.get("task_status"), "completed")

    def test_existing_draft_blocks_other_requests(self):
        self.run_offline()
        _, tools = self.setup_tools(self.request(request_id="R2"))
        self.prepare(tools)
        self.rejected(lambda: tools.save_draft(material_id="M1", suggested_time=BASE_TIME))
        self.assertEqual(self.draft_count(), 1)

    def test_conflict_appearing_after_query_is_rechecked_on_save(self):
        store, tools = self.setup_tools()
        self.prepare(tools)
        store.add_reservation(BASE_TIME, NEXT_TIME)
        result = tools.save_draft(material_id="M1", suggested_time=BASE_TIME)
        self.assertEqual(self.draft_count(), 0)
        self.assertEqual(result.get("status"), "conflict")

    def test_two_connections_competing_for_same_slot_only_one_succeeds(self):
        initial = DraftStore(self.db)
        initial.close()
        barrier = threading.Barrier(2)

        def compete(number):
            store = DraftStore(self.db)
            try:
                tools = ToolSet(self.data_dir, store)
                tools.bind(self.request(request_id=f"R{number}"))
                self.prepare(tools)
                barrier.wait(timeout=10)
                return tools.save_draft(material_id="M1", suggested_time=BASE_TIME)
            finally:
                store.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(compete, (1, 2)))
        self.assertEqual(sum(result.get("status") == "saved" for result in results), 1, results)
        self.assertEqual(self.draft_count(), 1)

    def test_equivalent_timezone_representation_still_conflicts(self):
        store, tools = self.setup_tools()
        self.prepare(tools)
        store.add_reservation("2026-11-02T02:00:00Z", "2026-11-02T03:00:00Z")
        result = tools.save_draft(material_id="M1", suggested_time=BASE_TIME)
        self.assertEqual(result.get("status"), "conflict")
        self.assertEqual(self.draft_count(), 0)

    def test_adjacent_slots_do_not_conflict(self):
        self.schedules = [{"schedule_id": "preceding", "start": "2026-11-02T09:00:00+08:00",
                           "end": BASE_TIME, "channel": "default"}]
        self.write_data()
        result, store = self.run_offline()
        self.assertIsNotNone(store.get_draft("R1", "M1"))
        self.assertEqual(self.draft_count(), 1)


class FaultTests(AcceptanceCase):
    def test_model_error_after_unknown_preserves_reconciliation_instruction(self):
        class ModelFailsAfterSave(ScriptedModel):
            def next_action(self, messages):
                if self.calls == 4:
                    self.calls += 1
                    raise RuntimeError("synthetic model failure after lost save response")
                return super().next_action(messages)

        store, tools = self.setup_tools(fault="save_response_lost")
        model = ModelFailsAfterSave([action("read_material"), action("check_rules"),
                                    action("query_slots"), action("save_draft")])
        result = run_agent(self.request(), model, tools, max_steps=8)
        self.assertEqual(result.get("task_status"), "incomplete")
        self.assertEqual(result.get("save_status"), "unknown")
        self.assertEqual(result.get("next_step"), "retry_same_request_to_reconcile_save")
        persisted = store.get_draft("R1", "M1")
        self.assertIsNotNone(persisted)
        recovered, _ = self.run_offline()
        self.assertEqual(recovered.get("draft_id"), persisted["draft_id"])
        self.assertEqual(self.draft_count(), 1)

    def test_permanent_precommit_failure_has_definite_failed_result(self):
        result, _ = self.run_offline(fault="save_fail_permanent")
        self.assertEqual(self.draft_count(), 0)
        self.assertEqual(result.get("save_status"), "failed")
        self.assertEqual(result.get("task_status"), "failed")

    def test_before_commit_failure_leaves_no_draft_then_retry_succeeds(self):
        _, tools = self.setup_tools(fault="save_fail_before_commit")
        self.prepare(tools)
        first = tools.save_draft(material_id="M1", suggested_time=BASE_TIME)
        self.assertEqual(self.draft_count(), 0)
        self.assertNotEqual(first.get("status"), "saved")
        second = tools.save_draft(material_id="M1", suggested_time=BASE_TIME)
        self.assertEqual(second.get("status"), "saved")
        self.assertEqual(self.draft_count(), 1)

    def test_after_commit_response_lost_is_unknown_then_idempotent_recovery(self):
        _, tools = self.setup_tools(fault="save_response_lost")
        self.prepare(tools)
        first = tools.save_draft(material_id="M1", suggested_time=BASE_TIME)
        self.assertEqual(self.draft_count(), 1)
        self.assertEqual(first.get("status"), "unknown")
        second = tools.save_draft(material_id="M1", suggested_time=BASE_TIME)
        self.assertEqual(second.get("status"), "saved")
        self.assertEqual(self.draft_count(), 1)

    def test_injected_save_conflict_does_not_save_stale_slot(self):
        _, tools = self.setup_tools(fault="conflict_on_save")
        self.prepare(tools)
        result = tools.save_draft(material_id="M1", suggested_time=BASE_TIME)
        self.assertEqual(result.get("status"), "conflict")
        self.assertEqual(self.draft_count(), 0)

    def test_unknown_at_step_limit_is_not_misreported_as_definite_failure(self):
        result, store = self.run_offline(fault="save_response_lost", max_steps=4)
        persisted = store.get_draft("R1", "M1")
        self.assertIsNotNone(persisted)
        self.assertEqual(result.get("save_status"), "unknown")
        self.assertNotIn(result.get("task_status"), ("completed", "failed"))
        recovered, _ = self.run_offline()
        self.assertEqual(recovered.get("draft_id"), persisted["draft_id"])
        self.assertEqual(recovered.get("save_status"), "saved")
        self.assertEqual(self.draft_count(), 1)


class AgentBoundaryTests(AcceptanceCase):
    def test_step_limit_is_incomplete_but_preserves_successful_save_evidence(self):
        result, store = self.run_offline(max_steps=4)
        draft = store.get_draft("R1", "M1")
        self.assertIsNotNone(draft)
        self.assertEqual(result.get("save_status"), "saved")
        self.assertEqual(result.get("task_status"), "incomplete")
        self.assertEqual(result.get("stop_reason"), "max_steps_reached")
        self.assertEqual(result.get("draft_id"), draft["draft_id"])
        self.assertEqual(result.get("draft_status"), draft["task_status"])

    def test_bad_actions_consume_max_steps(self):
        store, tools = self.setup_tools()
        model = ScriptedModel([action("does_not_exist")])
        result = run_agent(self.request(), model, tools, max_steps=3)
        self.assertLessEqual(model.calls, 3)
        self.assertEqual(self.draft_count(), 0)
        self.assertNotEqual(result.get("task_status"), "completed")

    def test_invalid_arguments_consume_max_steps(self):
        store, tools = self.setup_tools()
        model = ScriptedModel([action("read_material", material_id="M1", forged=True)])
        result = run_agent(self.request(), model, tools, max_steps=3)
        self.assertLessEqual(model.calls, 3)
        self.assertEqual(self.draft_count(), 0)

    def test_malformed_model_action_consumes_max_steps(self):
        _, tools = self.setup_tools()
        model = ScriptedModel([{"action": "read_material", "arguments": ["M1"]}])
        result = run_agent(self.request(), model, tools, max_steps=3)
        self.assertLessEqual(model.calls, 3)
        self.assertEqual(self.draft_count(), 0)
        self.assertNotEqual(result.get("task_status"), "completed")

    def test_model_errors_consume_max_steps(self):
        class FailingModel:
            calls = 0

            def next_action(self, messages):
                self.calls += 1
                raise RuntimeError("synthetic provider failure")

        _, tools = self.setup_tools()
        model = FailingModel()
        result = run_agent(self.request(), model, tools, max_steps=3)
        self.assertLessEqual(model.calls, 3)
        self.assertEqual(self.draft_count(), 0)
        self.assertNotEqual(result.get("task_status"), "completed")

    def test_model_success_claim_without_save_cannot_complete(self):
        _, tools = self.setup_tools()
        model = ScriptedModel([{"action": "finish", "arguments": {"task_status": "completed",
                                "save_status": "saved", "draft_id": "fabricated"}}])
        result = run_agent(self.request(), model, tools, max_steps=3)
        self.assertNotEqual(result.get("task_status"), "completed")
        self.assertEqual(self.draft_count(), 0)
        self.assertNotEqual(result.get("draft_id"), "fabricated")

    def test_model_cannot_skip_review_and_save(self):
        _, tools = self.setup_tools()
        model = ScriptedModel([action("read_material", material_id="M1"),
                               action("query_slots", material_id="M1"),
                               action("save_draft", material_id="M1", suggested_time=BASE_TIME)])
        result = run_agent(self.request(), model, tools, max_steps=5)
        self.assertEqual(self.draft_count(), 0)
        self.assertNotEqual(result.get("task_status"), "completed")

    def test_model_cannot_forge_tool_observation(self):
        _, tools = self.setup_tools()
        model = ScriptedModel([{"action": "save_draft", "arguments": {"material_id": "M1",
                                  "suggested_time": BASE_TIME},
                                "observation": {"status": "saved", "draft_id": "fake"}}])
        result = run_agent(self.request(), model, tools, max_steps=3)
        self.assertEqual(self.draft_count(), 0)
        self.assertNotEqual(result.get("draft_id"), "fake")

    def test_trace_does_not_include_fabricated_success_as_tool_result(self):
        _, tools = self.setup_tools()
        model = ScriptedModel([{"action": "finish", "arguments": {},
                                "observation": {"status": "saved", "draft_id": "fake"}}])
        result = run_agent(self.request(), model, tools, max_steps=2)
        self.assertEqual(self.draft_count(), 0)
        for event in result.get("trace", []):
            observation = event.get("tool", {}).get("result_summary", {})
            if isinstance(observation, dict):
                self.assertNotEqual(observation.get("draft_id"), "fake")

    def test_early_rejection_sanitizes_secret_shaped_input(self):
        sentinel = "sk-ACCEPTANCE_SYNTHETIC_TOKEN_12345"
        store = DraftStore(self.db)
        self.addCleanup(store.close)
        tools = ToolSet(self.data_dir, store)
        request = self.request(request_id=sentinel)
        result = run_agent(request, ScriptedModel([action("finish")]), tools, max_steps=0)
        self.assertNotIn(sentinel, json.dumps(result))
        del request["material_id"]
        result = run_agent(request, ScriptedModel([action("finish")]), tools, max_steps=2)
        self.assertNotIn(sentinel, json.dumps(result))


class TrustedOutputTests(AcceptanceCase):
    def occupy_requested_time(self):
        self.schedules = [{"schedule_id": "busy", "start": BASE_TIME, "end": NEXT_TIME,
                           "channel": "default"}]
        self.write_data()

    def test_fixed_move_lists_original_and_actual_proposed_time(self):
        self.occupy_requested_time()
        result, _ = self.run_offline()
        changes = [item for item in result["pending_items"] if item.get("type") == "time_change"]
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["original_time"], BASE_TIME)
        self.assertEqual(changes[0]["suggested_time"], result["suggested_time"])
        self.assertEqual(result["review_status"], "pass")
        self.assertEqual(result["risk_reasons"], [])
        self.assertEqual(result["missing_fields"], [])
        recovered, _ = self.run_offline()
        self.assertEqual(recovered["pending_items"], result["pending_items"])

    def test_authorized_window_move_has_no_time_change_confirmation(self):
        self.occupy_requested_time()
        result, _ = self.run_offline(self.request(time_requirement="window", allowed_start=BASE_TIME,
                                                 allowed_end="2026-11-02T13:00:00+08:00"))
        self.assertNotEqual(result["suggested_time"], BASE_TIME)
        self.assertEqual(result["pending_items"], [])
        self.assertEqual(result["confirmation_status"], "not_required")

    def test_rule_risk_exposes_rule_and_evidence_without_time_change(self):
        self.materials[0]["text"] = "本次活动是最低价。"
        self.write_data()
        result, _ = self.run_offline()
        self.assertEqual(result["review_status"], "risk")
        self.assertEqual({item.get("type") for item in result["pending_items"]}, {"rule_risk"})
        risk = result["pending_items"][0]
        self.assertIn("最低价", risk["evidence"])
        self.assertTrue(risk["rule_id"])
        self.assertTrue(any(risk["rule_id"] in reason and "最低价" in reason
                            for reason in result["risk_reasons"]))

    def test_combined_time_change_and_rule_risk_are_both_visible(self):
        self.materials[0]["text"] = "本次活动是最低价。"
        self.occupy_requested_time()
        result, _ = self.run_offline()
        self.assertEqual({item.get("type") for item in result["pending_items"]},
                         {"time_change", "rule_risk"})
        self.assertEqual(result["confirmation_status"], "pending")
        self.assertEqual(result["task_status"], "pending_confirmation")

    def test_missing_fields_are_top_level_and_not_confirmation_items(self):
        self.materials[0]["title"] = ""
        self.materials[0]["rights_status"] = "unknown"
        self.write_data()
        result, _ = self.run_offline()
        self.assertEqual(result["review_status"], "missing_information")
        self.assertEqual(set(result["missing_fields"]), {"title", "rights_status"})
        self.assertEqual(result["pending_items"], [])
        self.assertEqual(result["risk_reasons"], [])

    def test_hard_block_reason_is_visible_without_confirmation_item(self):
        self.materials[0]["rights_status"] = "denied"
        self.write_data()
        result, _ = self.run_offline()
        self.assertEqual(result["review_status"], "blocked")
        self.assertTrue(any("rights_denied" in reason for reason in result["risk_reasons"]))
        self.assertEqual(result["pending_items"], [])
        self.assertEqual(self.draft_count(), 0)

    def test_model_cannot_invent_top_level_review_or_pending_items(self):
        _, tools = self.setup_tools()
        model = ScriptedModel([{"action": "finish", "arguments": {}, "review_status": "pass",
                                "missing_fields": [], "risk_reasons": [],
                                "pending_items": [{"type": "time_change", "suggested_time": "forged"}]}])
        result = run_agent(self.request(), model, tools, max_steps=2)
        self.assertIsNone(result["review_status"])
        self.assertEqual(result["pending_items"], [])
        self.assertEqual(result["risk_reasons"], [])
        self.assertEqual(self.draft_count(), 0)
        self.assertNotIn("forged", json.dumps(result["pending_items"]))

    def test_model_cannot_clear_real_pending_items_or_change_saved_selection(self):
        selected = "2026-11-02T10:15:00+08:00"
        self.materials[0]["text"] = "本次活动是最低价。"
        self.write_data()
        store, tools = self.setup_tools()
        model = ScriptedModel([action("read_material"), action("check_rules"), action("query_slots"),
                               action("save_draft", suggested_time=selected),
                               {"action": "finish", "arguments": {}, "review_status": "pass",
                                "missing_fields": [], "risk_reasons": [], "pending_items": [],
                                "suggested_time": BASE_TIME}])
        result = run_agent(self.request(), model, tools, max_steps=6)
        self.assertEqual(result["suggested_time"], selected)
        self.assertEqual(store.get_draft("R1", "M1")["suggested_time"], selected)
        self.assertEqual(result["review_status"], "risk")
        self.assertEqual({item.get("type") for item in result["pending_items"]},
                         {"time_change", "rule_risk"})
        self.assertTrue(any("最低价" in reason for reason in result["risk_reasons"]))
        self.assertEqual(result["confirmation_status"], "pending")
        self.assertEqual(result["save_status"], "saved")
        self.assertTrue(any(event.get("validation") == "rejected" for event in result["trace"]))


class CliTests(AcceptanceCase):
    def test_offline_cli_writes_result_and_real_trace(self):
        input_path = self.root / "request.json"
        output_path = self.root / "result.json"
        trace_path = self.root / "trace.jsonl"
        input_path.write_text(json.dumps(self.request()), encoding="utf-8")
        process = subprocess.run([
            sys.executable, "-m", "src.main", "--input", str(input_path), "--mode", "offline",
            "--db", str(self.db), "--output", str(output_path), "--trace", str(trace_path),
            "--data-dir", str(self.data_dir), "--max-steps", "12",
        ], cwd=PROJECT, capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(output_path.read_text(encoding="utf-8"))
        events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(result.get("mode"), "offline")
        self.assertEqual(result.get("save_status"), "saved")
        self.assertEqual(self.draft_count(), 1)
        tool_names = {event.get("tool", {}).get("name") for event in events}
        self.assertTrue({"read_material", "check_rules", "query_slots", "save_draft"}.issubset(tool_names))
        self.assertTrue(all(event.get("model") != "qwen3.8-max" for event in events))


if __name__ == "__main__":
    unittest.main()
