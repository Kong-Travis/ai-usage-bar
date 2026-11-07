"""A stand-in for `codex app-server`: newline-delimited JSON-RPC on stdio.

Like the real one it quits on stdin EOF without answering what is still pending.
FAKE_CODEX_MODE: ok, error, early_exit, hang, other_limit.
FAKE_CODEX_LOG: a file that receives the CODEX_HOME the server saw.
"""
import json
import os
import sys
import time

NOW = int(time.time())  # resets in the future, as a live server reports them
MODE = os.environ.get("FAKE_CODEX_MODE", "ok")

SNAPSHOT = {"limitId": "codex", "limitName": None,
            "primary": {"usedPercent": 78, "windowDurationMins": 300, "resetsAt": NOW + 3600},
            "secondary": {"usedPercent": 73, "windowDurationMins": 10080, "resetsAt": NOW + 3 * 86400},
            "planType": "plus"}
SPARK = {"limitId": "codex_spark", "limitName": "Spark",
         "primary": {"usedPercent": 5, "windowDurationMins": 300, "resetsAt": NOW + 1800},
         "secondary": None, "planType": "plus"}


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def result(ident, params):
    if ident == 1:
        return {"userAgent": "fake"}
    if ident == 2:
        return {"account": {"type": "chatgpt", "email": "x@y.com", "planType": "plus",
                            "workspaceName": None}, "requiresOpenaiAuth": True}
    limits = {"codex": SNAPSHOT}
    if MODE == "other_limit":
        limits["codex_spark"] = SPARK
    return {"rateLimits": SNAPSHOT, "rateLimitsByLimitId": limits}


def main():
    log = os.environ.get("FAKE_CODEX_LOG")
    if log:
        with open(log, "w") as f:
            f.write(os.environ.get("CODEX_HOME", ""))
    sys.stdout.write("\nnot json at all\n")
    send({"jsonrpc": "2.0", "method": "remoteControl/status/changed", "params": {}})
    for line in sys.stdin:
        request = json.loads(line)
        ident = request.get("id")
        if ident is None:
            continue
        if MODE == "hang" and ident != 1:
            time.sleep(60)
        if MODE == "early_exit" and ident != 1:
            return
        if MODE == "error" and ident == 3:
            send({"jsonrpc": "2.0", "id": ident,
                  "error": {"code": -32000, "message": "not signed in"}})
            continue
        send({"jsonrpc": "2.0", "id": ident, "result": result(ident, request.get("params"))})


main()
