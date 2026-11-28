#!/usr/bin/env python3
"""PushPlus delivery: one HTTP call, html or txt payload."""
import json
import os
import urllib.error
import urllib.request

ENDPOINT = "https://www.pushplus.plus/send"


def token_from(cfg_token=None):
    return (os.environ.get("PUSHPLUS_TOKEN") or cfg_token or "").strip()


def topic_from(cfg_topic=None):
    """PushPlus group topic; empty means the token's default channel."""
    return (os.environ.get("PUSHPLUS_TOPIC") or cfg_topic or "").strip()


def send(title, content, token=None, topic=None, template="html", timeout=30):
    """Returns dict {'ok': bool, 'raw': ...}. Missing token is reported, never raised."""
    token = (token or "").strip()
    topic = (topic or "").strip()
    if not token:
        return {"ok": False, "skipped": True, "reason": "PUSHPLUS_TOKEN not set"}
    payload = {
        "token": token,
        "title": title[:100],
        "content": content,
        "template": template,
        "channel": "wechat",
    }
    if topic:
        payload["topic"] = topic
    body = json.dumps(payload).encode()
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
    # code=200 只代表 PushPlus 接受了请求；送达与否要看官网消息记录。
    # 把 msg/data（记录 ID）一并带出，CI 日志里就能直接看到服务端怎么说。
    ok = data.get("code") == 200
    return {"ok": ok, "msg": data.get("msg"), "data": data.get("data"), "raw": data}
