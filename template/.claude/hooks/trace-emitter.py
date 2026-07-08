#!/usr/bin/env python
"""trace-emitter.py  —  registered on ALL lifecycle events

Maps hook events to observability spans/scores (Architecture.md §8):
  session_id                     -> Langfuse trace id
  PreToolUse(Task)/SubagentStop  -> subagent span open/close (no SubagentStart
                                    exists, so open is approximated from dispatch)
  tool calls                     -> child events
  review verdicts                -> Langfuse scores; failed reviews -> dataset flag

Fail-open, always. Observability must never become an availability dependency:
every event is appended to .claude/traces/<session>.jsonl locally, and Langfuse
delivery is a best-effort urllib POST wrapped in try/except with a short timeout.
If Langfuse is unreachable, the local trace is the source of truth. Exit 0 no
matter what — a telemetry failure must never block the pipeline.
"""

import base64
import glob
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_lib as lib  # noqa: E402


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def local_log(root, session, record):
    try:
        d = os.path.join(root, ".claude", "traces")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, (session or "session") + ".jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def latest_review(feature_dir):
    files = glob.glob(os.path.join(feature_dir, "review.*.json")) if feature_dir else []
    if not files:
        return None
    files.sort(key=os.path.getmtime)
    return lib.read_json(files[-1])


def langfuse_post(host, public, secret, batch):
    body = json.dumps({"batch": batch}).encode("utf-8")
    auth = base64.b64encode(("%s:%s" % (public, secret)).encode()).decode()
    req = urllib.request.Request(
        host.rstrip("/") + "/api/public/ingestion",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": "Basic " + auth},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=3).read()


def env_ref(value):
    """Config values may be `env:VAR` references (never literal secrets on disk)."""
    if isinstance(value, str) and value.startswith("env:"):
        return os.environ.get(value[4:], "")
    return value or ""


def main():
    hook_input = lib.read_hook_input()
    root = lib.project_dir(hook_input)
    session = hook_input.get("session_id") or "session"
    event = hook_input.get("hook_event_name", "unknown")
    tool = hook_input.get("tool_name", "")

    record = {"ts": now_iso(), "event": event, "tool": tool, "session": session}
    feature_dir = lib.current_feature_dir(root)
    if feature_dir:
        record["feature"] = os.path.basename(feature_dir)

    review = latest_review(feature_dir) if event == "SubagentStop" else None
    if review:
        record["review"] = {
            "task": review.get("task"),
            "status": review.get("status"),
            "failure_class": review.get("failure_class"),
        }

    local_log(root, session, record)

    config = lib.load_config(root)
    obs = config.get("observability", {})
    if not obs.get("enabled", True) or obs.get("provider") != "langfuse":
        lib.allow()

    host = env_ref(obs.get("host")) or os.environ.get("LANGFUSE_HOST", "")
    public = env_ref(obs.get("public_key")) or os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret = env_ref(obs.get("secret_key")) or os.environ.get("LANGFUSE_SECRET_KEY", "")
    if not (host and public and secret):
        lib.allow()  # unconfigured -> local trace only, silently

    batch = [
        {"type": "trace-create", "id": session, "timestamp": record["ts"],
         "body": {"id": session, "name": record.get("feature", "loop-engineering")}},
        {"type": "event-create", "id": session + ":" + record["ts"], "timestamp": record["ts"],
         "body": {"traceId": session, "name": event, "metadata": record}},
    ]
    if review and review.get("status"):
        batch.append({
            "type": "score-create", "id": session + ":score:" + record["ts"],
            "timestamp": record["ts"],
            "body": {"traceId": session, "name": "review",
                     "value": 1 if review.get("status") == "pass" else 0,
                     "comment": review.get("failure_class") or ""},
        })

    try:
        langfuse_post(host, public, secret, batch)
    except Exception:
        pass  # fail-open: local trace already written

    lib.allow()


if __name__ == "__main__":
    main()
