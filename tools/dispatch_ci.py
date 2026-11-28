#!/usr/bin/env python3
"""Trigger the CI pool pipeline via workflow_dispatch (used by the external timer).

The PAT is NOT stored in this repo: set GH_PAT in the environment, or write it
to ~/.ur-auto/gh_pat (chmod 600) on the machine that runs the timer.
"""
import json
import os
import sys
import urllib.error
import urllib.request

REPO = "Jack-luo1208/ur-auto"
WORKFLOW = "pool.yml"
REF = "main"
TOKEN_FILE = os.path.expanduser("~/.ur-auto/gh_pat")


def load_token():
    t = os.environ.get("GH_PAT", "").strip()
    if t:
        return t
    try:
        with open(TOKEN_FILE, encoding="utf-8") as fh:
            return fh.read().strip()
    except FileNotFoundError:
        sys.exit("no GH_PAT: set env GH_PAT or write %s" % TOKEN_FILE)


def main():
    req = urllib.request.Request(
        "https://api.github.com/repos/%s/actions/workflows/%s/dispatches" % (REPO, WORKFLOW),
        data=json.dumps({"ref": REF}).encode(),
        headers={
            "Authorization": "Bearer " + load_token(),
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "ur-auto-dispatch",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print("dispatch ok: HTTP %s" % resp.status)
    except urllib.error.HTTPError as exc:
        sys.exit("dispatch failed: HTTP %s %s" % (exc.code, exc.read().decode(errors="replace")[:300]))
    except Exception as exc:
        sys.exit("dispatch failed: %s" % exc)


if __name__ == "__main__":
    main()
