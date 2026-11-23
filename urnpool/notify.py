#!/usr/bin/env python3
"""PushPlus delivery: one HTTP call, html or txt payload."""
import json
import os
import urllib.error
import urllib.request

ENDPOINT = "https://www.pushplus.plus/send"


def token_from(cfg_token=None):
    return (os.environ.get("PUSHPLUS_TOKEN") or cfg_token or "").strip()


def send(title, content, token=None, template="html", timeout=30):
    """Returns dict {'ok': bool, 'raw': ...}. Missing token is reported, never raised."""
    token = (token or "").strip()
    if not token:
        return {"ok": False, "skipped": True, "reason": "PUSHPLUS_TOKEN not set"}
    body = json.dumps({
        "token": token,
        "title": title[:100],
        "content": content,
        "template": template,
        "channel": "wechat",
    }).encode()
    req = urllib.request.Request(ENDPOINT, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode(errors="replace")
    except urllib.error.HTTPError as exc:
        return {"ok": False, "reason": "http %s" % exc.code, "raw": exc.read().decode(errors="replace")[:300]}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)[:160]}
    try:
        data = json.loads(raw)
    except Exception:
        return {"ok": False, "reason": "bad json", "raw": raw[:200]}
    ok = data.get("code") == 200
    return {"ok": ok, "raw": data}
