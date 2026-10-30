"""SQLite idempotency, reservation and transactional draft persistence."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import timedelta
from pathlib import Path

from .domain import parse_time, payload_hash, utc_text


class StoreConflict(ValueError):
    """The same request key was reused with a different original payload."""


class DraftStore:
    def __init__(self, db_path, schedules=None):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=10000")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS requests (
              request_id TEXT NOT NULL, material_id TEXT NOT NULL,
              payload_hash TEXT NOT NULL, payload_json TEXT NOT NULL,
              PRIMARY KEY (request_id));
            CREATE TABLE IF NOT EXISTS drafts (
              draft_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, material_id TEXT NOT NULL,
              channel TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT NOT NULL,
              confirmation_status TEXT NOT NULL, result_json TEXT NOT NULL,
              UNIQUE (request_id, material_id));
            CREATE TABLE IF NOT EXISTS reservations (
              reservation_id TEXT PRIMARY KEY, material_id TEXT NOT NULL,
              channel TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT NOT NULL);
        """)
        self._normalize_existing_times()
        if schedules:
            self.import_schedules(schedules)

    def _normalize_existing_times(self):
        # Earlier local DB files may predate fixed-width microsecond timestamps.
        conn = self.connection
        conn.execute("BEGIN IMMEDIATE")
        try:
            for table, id_column in (("drafts", "draft_id"), ("reservations", "reservation_id")):
                rows = conn.execute(f"SELECT {id_column}, start_time, end_time FROM {table}").fetchall()
                for row in rows:
                    begin = utc_text(parse_time(row["start_time"]))
                    end = utc_text(parse_time(row["end_time"]))
                    if begin != row["start_time"] or end != row["end_time"]:
                        conn.execute(f"UPDATE {table} SET start_time=?, end_time=? WHERE {id_column}=?",
                                     (begin, end, row[id_column]))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def close(self):
        self.connection.close()

    def bind_request(self, request):
        digest = payload_hash(request)
        conn = self.connection
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = conn.execute(
                "SELECT payload_hash FROM requests WHERE request_id=?",
                (request["request_id"],),
            ).fetchone()
            if existing and existing["payload_hash"] != digest:
                raise StoreConflict("request_id already bound to a different original payload")
            conn.execute(
                "INSERT OR IGNORE INTO requests VALUES (?, ?, ?, ?)",
                (request["request_id"], request["material_id"], digest,
                 json.dumps(request, ensure_ascii=False, sort_keys=True)),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def import_schedules(self, schedules):
        if isinstance(schedules, dict):
            schedules = schedules.get("schedules", [])
        prepared = []
        for index, item in enumerate(schedules):
            if item.get("status", "confirmed") in ("cancelled", "canceled", "rejected"):
                continue
            start = item.get("start", item.get("start_time"))
            end = item.get("end", item.get("end_time"))
            if not start:
                raise ValueError("schedule start is required")
            begin = parse_time(start)
            finish = parse_time(end) if end else begin + timedelta(minutes=int(item.get("duration_minutes", 60)))
            if finish <= begin:
                raise ValueError("schedule end must be after start")
            ident = str(item.get("schedule_id", item.get("id", f"seed-{index}")))
            prepared.append(("schedule:" + ident, str(item.get("material_id", "external")),
                             str(item.get("channel", "default")), utc_text(begin), utc_text(finish)))
        # The JSON file is an authoritative local schedule snapshot. Only seeded
        # rows are synchronized; injected/concurrent reservations and drafts survive.
        conn = self.connection
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DELETE FROM reservations WHERE reservation_id LIKE 'schedule:%'")
            conn.executemany("INSERT INTO reservations VALUES (?, ?, ?, ?, ?)", prepared)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def get_draft(self, request_id, material_id):
        row = self.connection.execute(
            "SELECT result_json FROM drafts WHERE request_id=? AND material_id=?",
            (request_id, material_id),
        ).fetchone()
        return json.loads(row["result_json"]) if row else None

    def conflicts(self, start, end, channel="default"):
        begin, finish = utc_text(parse_time(start)), utc_text(parse_time(end))
        # All stored timestamps use UTC, so lexical ordering is safe.
        rows = self.connection.execute("""
            SELECT reservation_id AS id, start_time AS start, end_time AS end
            FROM reservations WHERE channel=? AND start_time < ? AND end_time > ?
            UNION ALL
            SELECT draft_id AS id, start_time AS start, end_time AS end
            FROM drafts WHERE channel=? AND start_time < ? AND end_time > ?
        """, (channel, finish, begin, channel, finish, begin)).fetchall()
        return [dict(row) for row in rows]

    def add_reservation(self, start, end, channel="default", reservation_id=None):
        self.connection.execute(
            "INSERT OR IGNORE INTO reservations VALUES (?, ?, ?, ?, ?)",
            (reservation_id or "injected:" + uuid.uuid4().hex, "external", channel,
             utc_text(parse_time(start)), utc_text(parse_time(end))),
        )

    def save_draft(self, request, start, end, channel, confirmation_status, review, fail_before_commit=False):
        """Return an existing draft first; otherwise atomically check occupancy and save."""
        conn = self.connection
        conn.execute("BEGIN IMMEDIATE")
        try:
            bound = conn.execute(
                "SELECT payload_hash FROM requests WHERE request_id=?",
                (request["request_id"],),
            ).fetchone()
            if not bound or bound["payload_hash"] != payload_hash(request):
                raise StoreConflict("original request is not bound or has changed")
            previous = self.get_draft(request["request_id"], request["material_id"])
            if previous:
                conn.execute("COMMIT")
                return {"status": "saved", "idempotent": True, "draft": previous}
            conflicts = self.conflicts(start, end, channel)
            if conflicts:
                conn.execute("ROLLBACK")
                return {"status": "conflict", "code": "slot_conflict", "retryable": True,
                        "conflicts": conflicts, "message": "slot changed; query_slots again"}
            draft = {
                "draft_id": "draft_" + uuid.uuid4().hex,
                "request_id": request["request_id"], "material_id": request["material_id"],
                "original_time": request["requested_time"], "suggested_time": start,
                "end_time": end, "channel": channel, "confirmation_status": confirmation_status,
                "save_status": "saved", "review": review,
                "task_status": "pending_confirmation" if confirmation_status == "pending" else "completed",
            }
            conn.execute("INSERT INTO drafts VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (
                draft["draft_id"], request["request_id"], request["material_id"], channel,
                utc_text(parse_time(start)), utc_text(parse_time(end)), confirmation_status,
                json.dumps(draft, ensure_ascii=False),
            ))
            if fail_before_commit:
                raise OSError("injected save failure before commit")
            conn.execute("COMMIT")
            return {"status": "saved", "idempotent": False, "draft": draft}
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
