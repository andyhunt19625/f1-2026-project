"""Ask the running engineer a question from the command line.

  python3 -m race_engineer.ask status
  python3 -m race_engineer.ask gap hamilton
  python3 -m race_engineer.ask tyres [driver]
  python3 -m race_engineer.ask pit [pit_loss_seconds] [buffer_seconds]
  python3 -m race_engineer.ask weather
  python3 -m race_engineer.ask lap | laps | aideploy | reminders
  python3 -m race_engineer.ask lose [ai] | deploymore [ai] | deploy
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

from .server import DEFAULT_HOST, DEFAULT_PORT

USAGE = ("Use: status | gap <driver> | tyres [driver] | damage [driver] | pit [loss] [buffer] | weather"
         " | events | pitloss | field | lap | laps | aideploy | reminders | lose [ai] | deploymore [ai] | deploy")


def build_path(words):
    cmd, args = words[0], words[1:]
    if cmd == "status":
        return "/status"
    if cmd == "gap":
        return "/ask?" + urllib.parse.urlencode({"tool": "gap_to", "driver": " ".join(args)})
    if cmd == "tyres":
        q = {"tool": "tyre_status"}
        if args:
            q["driver"] = " ".join(args)
        return "/ask?" + urllib.parse.urlencode(q)
    if cmd == "pit":
        q = {"tool": "pit_window"}
        if len(args) > 0:
            q["pit_loss"] = args[0]
        if len(args) > 1:
            q["buffer"] = args[1]
        return "/ask?" + urllib.parse.urlencode(q)
    if cmd == "weather":
        return "/ask?tool=weather_change"
    if cmd == "damage":
        q = {"tool": "damage_status"}
        if args:
            q["driver"] = " ".join(args)
        return "/ask?" + urllib.parse.urlencode(q)
    if cmd == "events":
        return "/ask?tool=race_events"
    if cmd == "pitloss":
        return "/ask?tool=pit_loss"
    if cmd == "field":
        return "/ask?tool=tyre_field"
    if cmd in ("lap", "laps", "aideploy", "reminders"):
        return "/ask?tool=" + {"lap": "last_lap", "laps": "laps", "aideploy": "ai_deploy",
                               "reminders": "reminders"}[cmd]
    if cmd in ("lose", "deploymore"):
        q = {"tool": "where_losing" if cmd == "lose" else "deploy_more"}
        if args and args[0] == "ai":
            q["ref"] = "ai"
        return "/ask?" + urllib.parse.urlencode(q)
    if cmd == "deploy":
        return "/ask?tool=deploy_now"
    raise SystemExit(USAGE)


def fetch(path, host=DEFAULT_HOST, port=DEFAULT_PORT, timeout=5):
    try:
        with urllib.request.urlopen(f"http://{host}:{port}{path}", timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
    except (urllib.error.URLError, OSError):
        return None, {"error": "The engineer is not running. Turn it on first."}


def main(argv=None):
    words = (argv if argv is not None else sys.argv[1:])
    if not words:
        raise SystemExit(USAGE)
    code, data = fetch(build_path(words))
    if "answer" in data:
        print(data["answer"])
    print(json.dumps({k: v for k, v in data.items() if k != "answer"}, indent=1))
    return 0 if code == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
