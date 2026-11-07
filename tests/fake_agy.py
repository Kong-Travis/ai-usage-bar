"""Stands in for `agy` print mode. FAKE_AGY_MODE: ok, five_hour, missing_fraction, error_status,
garbage, slow. FAKE_AGY_ARGS: file that receives the argv as one JSON line."""
import json
import os
import sys
import time

MODE = os.environ.get("FAKE_AGY_MODE", "ok")


def group(name, *buckets):
    return {"name": name, "description": "", "buckets": list(buckets)}


def bucket(window, fraction, reset="2026-11-14T05:44:52Z", **extra):
    b = {"id": window, "name": "Limit Remaining", "window": window, "reset_time": reset}
    if fraction is not None:
        b["remaining_fraction"] = fraction
    b.update(extra)
    return b


def reply(status="SUCCESS", groups=None, error=None):
    out = {"conversation_id": "", "status": status, "response": "", "duration_seconds": 0,
           "num_turns": 0, "usage": {}}
    if error:
        out["error"] = error
    if groups is not None:
        out["command"] = {"name": "usage", "data": {"description": "", "groups": groups}}
    return out


def main():
    args_file = os.environ.get("FAKE_AGY_ARGS")
    if args_file:
        with open(args_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(sys.argv[1:]) + "\n")
    if "--log-file" in sys.argv:
        with open(sys.argv[sys.argv.index("--log-file") + 1], "a", encoding="utf-8") as f:
            f.write("fake agy ran\n")
    if MODE == "slow":
        time.sleep(30)
    if MODE == "garbage":
        print("not json at all")
        return 3
    if MODE == "error_status":
        print(json.dumps(reply("ERROR", error="/credits failed: " + "x" * 300)))
        return 1
    if MODE == "five_hour":
        groups = [group("Gemini Models",
                        bucket("weekly", 0.75), bucket("five_hour", 0.5, "2026-11-07T10:00:00.1234567+07:00")),
                  group("Claude and GPT models", bucket("daily", 1.5), bucket("monthly", -0.2))]
    elif MODE == "missing_fraction":
        groups = [group("Gemini Models", bucket("weekly", None), bucket("five_hour", 0.9)),
                  group("Claude and GPT models", bucket("weekly", None))]
    else:
        groups = [group("Gemini Models", bucket("weekly", 1)),
                  group("Claude and GPT models", bucket("weekly", 0.25))]
    print("some banner line")
    print(json.dumps(reply(groups=groups)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
