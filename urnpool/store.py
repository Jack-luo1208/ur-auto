#!/usr/bin/env python3
"""SQLite state for the account pool."""
import os
import sqlite3
import time
import uuid

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id              TEXT PRIMARY KEY,
    wallet_address  TEXT NOT NULL UNIQUE,
    seed_b64        TEXT NOT NULL,
    by_jwt          TEXT NOT NULL,
    api_key         TEXT NOT NULL,
    network_id      TEXT,
    network_name    TEXT,
    status          TEXT NOT NULL DEFAULT 'standby',  -- standby|active|exhausted|dead
    start_bytes     INTEGER NOT NULL DEFAULT 0,
    remaining_bytes INTEGER NOT NULL DEFAULT 0,
    open_bytes      INTEGER NOT NULL DEFAULT 0,
    window_end      TEXT,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL,
    note            TEXT DEFAULT '',
    simulated       INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         INTEGER NOT NULL,
    account_id TEXT,
    kind       TEXT NOT NULL,
    detail     TEXT
);
CREATE INDEX IF NOT EXISTS idx_accounts_status ON accounts(status);
"""


class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        cols = [r[1] for r in self.db.execute("PRAGMA table_info(accounts)")]
        if "simulated" not in cols:
            self.db.execute("ALTER TABLE accounts ADD COLUMN simulated INTEGER NOT NULL DEFAULT 0")
        self.db.commit()

    # ---------- writes ----------
    def add_account(self, acc):
        now = int(time.time())
        aid = str(uuid.uuid4())
        self.db.execute(
            "INSERT INTO accounts (id, wallet_address, seed_b64, by_jwt, api_key, network_id, network_name,"
            " status, start_bytes, remaining_bytes, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (aid, acc["wallet_address"], acc["seed_b64"], acc["by_jwt"], acc["api_key"],
             acc.get("network_id"), acc.get("network_name"), acc.get("status", "standby"),
             acc.get("start_bytes", 0), acc.get("remaining_bytes", 0), now, now),
        )
        self.db.commit()
        return aid

    def update_balance(self, aid, remaining, start=None, open_bytes=None, window_end=None, status=None, simulated=None):
        fields = ["remaining_bytes = ?", "updated_at = ?"]
        args = [int(remaining), int(time.time())]
        if start is not None:
            fields.append("start_bytes = ?"); args.append(int(start))
        if open_bytes is not None:
            fields.append("open_bytes = ?"); args.append(int(open_bytes))
        if window_end is not None:
            fields.append("window_end = ?"); args.append(window_end)
        if status is not None:
            fields.append("status = ?"); args.append(status)
        if simulated is not None:
            fields.append("simulated = ?"); args.append(int(simulated))
        args.append(aid)
        self.db.execute("UPDATE accounts SET " + ", ".join(fields) + " WHERE id = ?", args)
        self.db.commit()

    def set_status(self, aid, status, note=None):
        self.db.execute("UPDATE accounts SET status = ?, updated_at = ?, note = COALESCE(?, note) WHERE id = ?",
                        (status, int(time.time()), note, aid))
        self.db.commit()

    def log(self, kind, account_id=None, detail=""):
        self.db.execute("INSERT INTO events (ts, account_id, kind, detail) VALUES (?,?,?,?)",
                        (int(time.time()), account_id, kind, detail))
        self.db.commit()

    # ---------- reads ----------
    def rows(self, status=None):
        q = "SELECT * FROM accounts"
        args = ()
        if status:
            q += " WHERE status = ?"
            args = (status,)
        q += " ORDER BY created_at ASC"
        return [dict(r) for r in self.db.execute(q, args).fetchall()]

    def get(self, aid):
        r = self.db.execute("SELECT * FROM accounts WHERE id = ?", (aid,)).fetchone()
        return dict(r) if r else None

    def counts(self):
        c = {r["status"]: r["n"] for r in self.db.execute(
            "SELECT status, COUNT(*) AS n FROM accounts GROUP BY status")}
        return c

    def recent_events(self, limit=15):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM events ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()]
