#!/usr/bin/env python3
"""Send three contrasting PushPlus messages to find why deliveries go missing.

Runs in CI where PUSHPLUS_TOKEN is available. Only code/msg/data are printed;
the token itself is never logged.
"""
import json
import os
import urllib.request

ENDPOINT = "https://www.pushplus.plus/send"


def send(title, content, template="txt", channel=None, topic=None):
    payload = {
        "token": os.environ.get("PUSHPLUS_TOKEN", "").strip(),
        "title": title,
        "content": content,
        "template": template,
    }
    if channel:
        payload["channel"] = channel
    if topic:
        payload["topic"] = topic
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode(errors="replace"))


def main():
    if not os.environ.get("PUSHPLUS_TOKEN", "").strip():
        raise SystemExit("PUSHPLUS_TOKEN not set")
    cases = [
        ("probe-A-minimal-txt", "A: minimal txt, default channel", "txt", None),
        ("probe-B-wechat-html", "<b>B: html via wechat channel</b>", "html", "wechat"),
        ("probe-C-wechat-txt", "C: txt via wechat channel", "txt", "wechat"),
        ("probe-D-group-clash", "D: to group topic=clash", "txt", None, "clash"),
    ]
    for case in cases:
        title, content, template, channel = case[:4]
        topic = case[4] if len(case) > 4 else None
        try:
            r = send(title, content, template=template, channel=channel, topic=topic)
            print("PROBE %-20s code=%s msg=%s data=%s" %
                  (title, r.get("code"), r.get("msg"), r.get("data")))
        except Exception as exc:
            print("PROBE %-20s ERROR %s" % (title, str(exc)[:120]))


if __name__ == "__main__":
    main()
