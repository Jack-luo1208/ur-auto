#!/usr/bin/env python3
"""Pool logic: keep N standby accounts, promote on balance exhaustion, refill when low."""
import json
import os
import time

from . import store as store_mod
from . import urnapi

DEFAULT_CONFIG = {
    "api_base": "https://api.bringyour.com",
    "db_path": "pool.db",
    "standby_target": 5,
    "refill_when_standby_at_or_below": 1,
    "exhaust_threshold_bytes": 268435456,   # 256 MiB left counts as used up
    "max_creates_per_tick": 5,              # platform: 5 new objects per minute
    "register_delay_seconds": 2.0,
}


def load_config(path):
    cfg = dict(DEFAULT_CONFIG)
    if path and os.path.exists(path):
        cfg.update(json.load(open(path, encoding="utf-8-sig")))
    base = os.path.dirname(os.path.abspath(path or "."))
    if not os.path.isabs(cfg["db_path"]):
        cfg["db_path"] = os.path.join(base, cfg["db_path"])
    return cfg


def human(n):
    n = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}"
        n /= 1024


class Pool:
    def __init__(self, cfg):
        self.cfg = cfg
        self.api = urnapi.Urn(base=cfg["api_base"])
        self.store = store_mod.Store(cfg["db_path"])
        self.threshold = int(cfg["exhaust_threshold_bytes"])

    # ---------- registration ----------
    def register_one(self, status="standby", log=print, attempts=3):
        last = None
        for attempt in range(attempts):
            try:
                acc = self.api.signup()
                acc["api_key"] = self.api.create_api_key(acc["by_jwt"], "urnpool")
                bal = self.api.balance(acc["api_key"])
                acc.update(status=status, start_bytes=bal["start_bytes"], remaining_bytes=bal["remaining_bytes"])
                aid = self.store.add_account(acc)
                self.store.log("register", aid, f"{acc['network_name']} balance={human(bal['remaining_bytes'])}")
                log("  registered %s (%s)" % (acc["network_name"], human(bal["remaining_bytes"])))
                return self.store.get(aid)
            except Exception as exc:  # rate limit / transient platform error
                last = exc
                self.store.log("register_error", None, str(exc)[:160])
                log("  register retry %d/%d: %s" % (attempt + 1, attempts, str(exc)[:90]))
                time.sleep(6 * (attempt + 1))
        raise last

    # ---------- balances ----------
    def refresh(self, aid=None):
        rows = [self.store.get(aid)] if aid else self.store.rows()
        for row in rows:
            if not row or row["status"] == "dead" or row.get("simulated"):
                continue
            try:
                bal = self.api.balance(row["api_key"])
            except Exception as exc:
                self.store.log("balance_error", row["id"], str(exc)[:120])
                continue
            self.store.update_balance(row["id"], bal["remaining_bytes"], bal["start_bytes"],
                                      bal["open_bytes"], bal["window_end"])
            if row["status"] in ("standby", "active") and bal["remaining_bytes"] <= self.threshold:
                self.store.set_status(row["id"], "exhausted", "balance under threshold")
                self.store.log("exhausted", row["id"], f"remaining={human(bal['remaining_bytes'])}")
            elif row["status"] == "exhausted" and bal["remaining_bytes"] > self.threshold:
                self.store.set_status(row["id"], "standby", "window rolled over")
                self.store.log("renewed", row["id"], f"remaining={human(bal['remaining_bytes'])}")

    # ---------- pool shape ----------
    def usable(self, status):
        return [r for r in self.store.rows(status) if r["remaining_bytes"] > self.threshold]

    def active(self):
        act = [r for r in self.store.rows("active") if r["remaining_bytes"] > self.threshold]
        return act[0] if act else None

    def standby(self):
        return self.usable("standby")

    def promote(self):
        """Make the oldest usable standby active; returns it."""
        cands = self.standby()
        if not cands:
            return None
        acc = cands[0]
        self.store.set_status(acc["id"], "active")
        self.store.log("promote", acc["id"], "standby -> active")
        return self.store.get(acc["id"])

    def ensure_active(self):
        act = self.active()
        if act:
            return act
        acc = self.promote()
        if acc:
            return acc
        acc = self.register_one(status="active")
        self.store.log("emergency_register", acc["id"], "no standby left")
        return acc

    def refill_standby(self, budget, log=print):
        """Register until standby == target. Returns number created."""
        made = 0
        failures = 0
        target = int(self.cfg["standby_target"])
        while len(self.standby()) < target and made < budget and made < int(self.cfg["max_creates_per_tick"]):
            try:
                self.register_one(status="standby", log=log)
            except Exception as exc:
                failures += 1
                self.store.log("refill_error", None, str(exc)[:160])
                log("  refill aborted: %s" % str(exc)[:90])
                if failures >= 2:
                    break
                continue
            made += 1
            failures = 0
            time.sleep(float(self.cfg["register_delay_seconds"]))
        return made

    # ---------- one control pass ----------
    def tick(self, verbose=True):
        report = {"refreshed": 0, "exhausted": [], "promoted": None, "created": 0}
        before = {r["id"]: r["status"] for r in self.store.rows()}
        self.refresh()
        report["refreshed"] = len(before)
        for r in self.store.rows():
            if before.get(r["id"]) in ("active", "standby") and r["status"] == "exhausted":
                report["exhausted"].append(r["id"])

        act = self.active()
        if not act:
            acc = self.ensure_active()
            if acc:
                report["promoted"] = acc["id"]
            act = acc

        standby_n = len(self.standby())
        if standby_n <= int(self.cfg["refill_when_standby_at_or_below"]):
            budget = int(self.cfg["standby_target"]) - standby_n
            report["created"] = self.refill_standby(budget)
        if verbose:
            print(self.render(report), flush=True)
        return report

    # ---------- rendering ----------
    def render(self, report=None):
        c = self.store.counts()
        lines = []
        lines.append(f"active={c.get('active', 0)} standby={c.get('standby', 0)} "
                     f"exhausted={c.get('exhausted', 0)} dead={c.get('dead', 0)}")
        for r in self.store.rows():
            tag = ">>" if r["status"] == "active" else "  "
            pct = 100.0 * r["remaining_bytes"] / r["start_bytes"] if r["start_bytes"] else 0
            lines.append(f"{tag} {r['id'][:8]} {r['status']:<9} {human(r['remaining_bytes']):>10} "
                         f"({pct:5.1f}%) {r['network_name'] or ''}")
        if report:
            lines.append("tick: " + json.dumps({k: (v if not isinstance(v, list) else len(v))
                                                for k, v in report.items()}))
        return "\n".join(lines)

    # ---------- consumer side ----------
    def acquire(self):
        """Hand out the active account; promotes/creates when needed."""
        return self.ensure_active()
