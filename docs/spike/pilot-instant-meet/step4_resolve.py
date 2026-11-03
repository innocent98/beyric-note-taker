"""S4: from each received conference.v2.started event, call conferenceRecords.get then spaces.get.

    python3 step4_resolve.py
Also prints the S2 table: event arrival latency = publishTime minus the conference startTime.
"""
import json

import common as c

MEET = "https://meet.googleapis.com/v2/"


def main():
    if not c.EVENTS_FILE.exists():
        c.die("no local/events.jsonl yet; run watch.py during the calls first")
    rows = [json.loads(l) for l in c.EVENTS_FILE.read_text().splitlines()]
    pulls = [r for r in rows if r["kind"] == "pull" and r["attributes"].get("ce-type") == c.STARTED]
    if not pulls:
        print("RESULT S2/S4: no conference.v2.started event was received.")
        return
    print(f"{'conference record':36} {'startTime':27} {'publishTime':27} latency  meetingCode")
    done = set()
    for p in pulls:
        name = (p["data"] or {}).get("conferenceRecord", {}).get("name")
        if not name or name in done:
            continue
        done.add(name)
        st, rec = c.call("step4_resolve", "GET", MEET + name)
        sp_st, sp = (None, {})
        if st == 200:
            sp_st, sp = c.call("step4_resolve", "GET", MEET + rec["space"])
        lat = ""
        if st == 200 and rec.get("startTime") and p.get("publishTime"):
            lat = f"{(c.parse_ts(p['publishTime']) - c.parse_ts(rec['startTime'])).total_seconds():.0f}s"
        code = sp.get("meetingCode") if sp_st == 200 else f"spaces.get HTTP {sp_st}"
        print(f"{c.redact(name):36} {rec.get('startTime', '?'):27} {p.get('publishTime', '?'):27} {lat:8} {code}  "
              f"(conferenceRecords.get HTTP {st}, meetingUri={sp.get('meetingUri') if sp_st == 200 else None})")
    print("\nCompare each row with your labels (python3 -c 'print(open(\"local/events.jsonl\").read())') to name the call type.")


if __name__ == "__main__":
    main()
