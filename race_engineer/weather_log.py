"""Print every change in the weather value or forecast across a saved session.
Read-only. For checking the wet test:

  python3 -m race_engineer.weather_log PATH/parsed.jsonl
"""
import json
import sys


def transitions(path):
    last = None
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if '"packet_name": "Session"' not in line[:120]:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            fc = [(x.get("sessionType"), x["timeOffset"], x["weather"], x["rainPercentage"])
                  for x in d.get("weatherForecastSamples", [])]
            key = (d["weather"], d.get("sessionType"), tuple(fc))
            if key != last:
                yield (d["sessionTime"], d["weather"], d.get("trackTemperature"), fc,
                       d.get("sessionType"))
                last = key


def main(argv=None):
    path = (argv or sys.argv[1:])[0]
    n = 0
    for st, w, tt, fc, stype in transitions(path):
        n += 1
        print(f"t={st:8.1f}s weather={w} track={tt}C sessionType={stype} "
              f"forecast(sessionType,offset,weather,rain%)={fc}")
    print(f"{n} distinct weather states")


if __name__ == "__main__":
    main()
