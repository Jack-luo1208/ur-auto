#!/usr/bin/env python3
"""CLI + CI pipeline: tick the pool, render a Clash subscription, push it via PushPlus."""
import argparse
import html
import json
import os
import sys
import time

from . import notify
from . import pool as pool_mod
from . import subscription

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_CONFIG = os.path.join(ROOT, "config.json")


def make_pool(args):
    cfg = pool_mod.load_config(args.config or DEFAULT_CONFIG)
    if getattr(args, "db", None):
        cfg["db_path"] = args.db
    return pool_mod.Pool(cfg)


def ensure_bootstrap(p):
    """First run in CI: create the database and fill the standby pool."""
    rows = p.store.rows()
    if rows:
        return False
    print("bootstrap: empty pool, creating %d accounts" % p.cfg["standby_target"])
    acc = p.register_one(status="active")
    p.store.log("bootstrap_active", acc["id"], acc["network_name"])
    p.refill_standby(int(p.cfg["standby_target"]), log=print)
    return True


# ---------------- commands ----------------
def cmd_status(args):
    p = make_pool(args)
    print(p.render())


def cmd_tick(args):
    p = make_pool(args)
    ensure_bootstrap(p)
    print(p.render(p.tick()))


def cmd_simulate(args):
    p = make_pool(args)
    if args.standby:
        for r in p.standby()[: args.standby]:
            p.store.update_balance(r["id"], 0, status="exhausted", simulated=1)
            p.store.log("simulate_exhaust", r["id"], "local simulation")
    else:
        act = p.active()
        if not act:
            sys.exit("no active account")
        p.store.update_balance(act["id"], 0, status="exhausted", simulated=1)
        p.store.log("simulate_exhaust", act["id"], "local simulation")
    print(p.render(p.tick()))


def build_nodes(p, cfg, log=print):
    acc = p.ensure_active()
    if not acc:
        log("no active account")
        return []
    want = int(cfg.get("node_count", 1))
    # free plan caps a network at 5 active proxies: drop last run's proxies before minting
    try:
        for pr in p.api.proxies(acc["api_key"]):
            p.api.remove_client(acc["api_key"], pr["client_id"])
            log("  prune %s" % pr["client_id"][:8])
    except Exception as exc:
        log("  prune skipped: %s" % str(exc)[:80])
    nodes = []
    used_cc = set()
    hint = bool(cfg.get("require_residential_hint", False))
    strict_city = bool(cfg.get("strict_city", False))
    budget = float(cfg.get("mint_budget_seconds", 900))
    started = time.time()

    # 记住上一轮判定失败的 provider，本轮直接跳过（平台参数 exclude_client_ids）
    try:
        p.store.purge_bad()
        exclude = set(p.store.bad_provider_ids(max_age_seconds=int(cfg.get("bad_provider_ttl_seconds", 86400))))
    except Exception:
        exclude = set()
    if exclude:
        log("  skipping %d providers judged bad in the last 24h" % len(exclude))

    def mark_bad(pid, reason):
        try:
            p.store.mark_bad(pid, reason)
        except Exception:
            pass

    def out_of_budget():
        return time.time() - started > budget

    if cfg.get("location_mode", "auto") == "auto":
        plan = subscription.discover_locations(p.api, cfg, log=log)
        for loc in plan:
            if len(nodes) >= want:
                break
            if out_of_budget():
                log("  mint budget reached (%.0fs), publishing what we have" % budget)
                break
            if loc.get("_cc") in used_cc:
                continue  # 尽量一个国家只出一个节点，覆盖面更广
            log("pin: %s/%s" % (loc.get("_cc"), loc.get("city")))
            node = subscription.mint_node(p.api, acc["api_key"], loc, rank=len(nodes), log=log,
                                          require_hint=hint, strict_city=strict_city,
                                          exclude=exclude, mark_bad=mark_bad)
            if node:
                nodes.append(node)
                used_cc.add(loc.get("_cc"))
    else:
        for query in cfg.get("node_locations", []):
            if len(nodes) >= want or out_of_budget():
                break
            log("pin: %s" % query)
            node = subscription.mint_node(p.api, acc["api_key"], query, rank=len(nodes), log=log,
                                          require_hint=hint, strict_city=strict_city,
                                          exclude=exclude, mark_bad=mark_bad)
            if node:
                nodes.append(node)
    return nodes


def render_and_push(p, cfg, out_dir, do_push, log=print):
    os.makedirs(out_dir, exist_ok=True)
    nodes = build_nodes(p, cfg, log=log)
    links = subscription.to_links(nodes)
    # 订阅源若已经是 Clash 就原样透传，否则按同一套节点参数转成 Clash 再推
    clash, mode = subscription.ensure_clash(links, nodes, mixed_port=int(cfg.get("mixed_port", 7890)))
    path_yaml = os.path.join(out_dir, "clash.yaml")
    path_txt = os.path.join(out_dir, "raw.txt")
    open(path_yaml, "w", encoding="utf-8", newline="\n").write(clash)
    open(path_txt, "w", encoding="utf-8", newline="\n").write(links)
    log("subscription: %d nodes, format=%s -> %s" % (len(nodes), mode, path_yaml))

    summary = {"nodes": [{k: n.get(k) for k in ("name", "ip", "cc", "country", "city", "isp", "g204", "xcom")}
                         for n in nodes],
               "clash_bytes": len(clash), "format": mode,
               "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    # public-safe copy for CI artifacts: node metadata only, never the credential
    open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8", newline="\n").write(
        json.dumps(summary, ensure_ascii=False, indent=1) + "\n")

    if do_push:
        stamp = time.strftime("%m-%d %H:%M", time.localtime())
        head = "<h3>URnetwork 订阅（Clash）</h3>"
        if nodes:
            rows = "".join(
                "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                    html.escape(n["name"]), html.escape(str(n.get("ip"))),
                    html.escape("%s %s" % (n.get("cc") or "", n.get("city") or "")),
                    html.escape(str(n.get("isp"))), html.escape("%s/%s" % (n.get("g204"), n.get("xcom"))))
                for n in nodes)
            head += ("<table border=1 cellspacing=0 cellpadding=4><tr><th>节点</th><th>出口 IP</th>"
                     "<th>国家/城市</th><th>ISP</th><th>204/x</th></tr>%s</table>" % rows)
        else:
            head += "<p>本轮未取到合格住宅出口（全部被淘汰）。</p>"
        content = (head + "<p>Clash 配置（%s 生成，共 %d 个节点）：</p><pre style=\"white-space:pre-wrap\">%s</pre>"
                   % (stamp, len(nodes), html.escape(clash)))
        res = notify.send("%s %s" % (cfg.get("pushplus_title_prefix", "URnetwork 订阅"), stamp), content,
                          token=notify.token_from(cfg.get("pushplus_token")))
        summary["push"] = {"ok": res.get("ok"), "reason": res.get("reason"), "skipped": res.get("skipped")}
        log("pushplus: %s" % json.dumps(summary["push"], ensure_ascii=False))

    return summary


def cmd_sub(args):
    p = make_pool(args)
    ensure_bootstrap(p)
    cfg = p.cfg
    summary = render_and_push(p, cfg, args.out or os.path.join(ROOT, "sub"), args.push)
    print(json.dumps(summary, ensure_ascii=False))


def cmd_pipeline(args):
    p = make_pool(args)
    ensure_bootstrap(p)
    print(p.render(p.tick(verbose=False)), flush=True)
    summary = render_and_push(p, p.cfg, args.out or os.path.join(ROOT, "sub"), not args.no_push)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


def cmd_selftest(args):
    """Offline checks: Clash rendering, link rendering, PushPlus skip path."""
    fake = [{"name": "UR-US-Test-01", "type": "socks5", "server": "crisp.bringyour.com", "port": 7117,
             "username": "tok_demo", "password": "", "udp": True, "ip": "1.2.3.4", "city": "Test",
             "isp": "Test ISP"}]
    y = subscription.to_clash(fake)
    need = ["mixed-port: 7890", "proxies:", "type: socks5", 'server: "crisp.bringyour.com"',
            "port: 7117", 'username: "tok_demo"', "proxy-groups:", "name: PROXY", "name: AUTO",
            "rules:", "MATCH,PROXY"]
    missing = [n for n in need if n not in y]
    assert not missing, "clash output missing: %s" % missing
    link = subscription.to_links(fake).strip()
    assert link.startswith("socks://tok_demo:@crisp.bringyour.com:7117#"), link
    skipped = notify.send("t", "c", token="")
    assert skipped.get("skipped") and not skipped.get("ok"), skipped
    assert subscription.ensure_clash(y, fake)[1] == "passthrough"
    assert subscription.ensure_clash("socks://a:@b:1#c", fake)[1] == "converted"
    print("selftest ok: clash=%d bytes, link ok, push skip ok, format detect ok" % len(y))


def cmd_run(args):
    p = make_pool(args)
    ensure_bootstrap(p)
    while True:
        p.tick()
        render_and_push(p, p.cfg, args.out or os.path.join(ROOT, "sub"), not args.no_push)
        time.sleep(float(args.interval))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="urnpool")
    ap.add_argument("--config", default=None)
    ap.add_argument("--db", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("tick").set_defaults(fn=cmd_tick)

    a = sub.add_parser("simulate")
    a.add_argument("--standby", type=int, default=0)
    a.set_defaults(fn=cmd_simulate)

    a = sub.add_parser("sub")
    a.add_argument("--out", default=None)
    a.add_argument("--push", action="store_true")
    a.set_defaults(fn=cmd_sub)

    a = sub.add_parser("pipeline")
    a.add_argument("--out", default=None)
    a.add_argument("--no-push", action="store_true")
    a.set_defaults(fn=cmd_pipeline)

    sub.add_parser("selftest").set_defaults(fn=cmd_selftest)

    a = sub.add_parser("run")
    a.add_argument("--interval", type=float, default=3600)
    a.add_argument("--out", default=None)
    a.add_argument("--no-push", action="store_true")
    a.set_defaults(fn=cmd_run)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
