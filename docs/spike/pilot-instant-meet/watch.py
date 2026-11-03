"""S2 + S5 in one terminal: pulls Pub/Sub events AND samples live conference records.

    python3 watch.py [--minutes 90] [--every 20]

Leave it running while you host the calls. Type a short label and press Enter whenever you
do something ("B: started meet.new, I am in", "joined D", "left D"); labels go into the log
next to the events and samples, which is what turns the raw lines into pass/fail.
Everything goes to local/events.jsonl. Ctrl-C stops it.
"""
import base64
import json
import sys
import threading
import time

import common as c

PS = "https://pubsub.googleapis.com/v1/"
MEET = "https://meet.googleapis.com/v2/"
stop = threading.Event()


def puller():
    sub = c.sub_path()
    while not stop.is_set():
        st, r = c.call("watch_pull", "POST", PS + sub + ":pull", body={"maxMessages": 10}, log=False)
        if st != 200:
            print(f"pull HTTP {st}: {c.redact(str(r))[:200]}")
            time.sleep(5)
            continue
        msgs = r.get("receivedMessages", [])
        if not msgs:
            continue
        for m in msgs:
            pm = m["message"]
            raw = base64.b64decode(pm.get("data", "")).decode(errors="replace") if pm.get("data") else ""
            try:
                data = json.loads(raw)
            except ValueError:
                data = raw
            c.append_event("pull", messageId=pm.get("messageId"), publishTime=pm.get("publishTime"),
                           attributes=pm.get("attributes", {}), data=data)
            print(f"\n>>> {c.now_iso()} EVENT {pm.get('attributes', {}).get('ce-type')} "
                  f"publishTime={pm.get('publishTime')}\n    {c.redact(json.dumps(data))}")
        c.call("watch_pull", "POST", PS + sub + ":acknowledge", body={"ackIds": [m["ackId"] for m in msgs]}, log=False)


def sampler(every):
    seen = {}
    while not stop.is_set():
        st, r = c.call("watch_sample", "GET", MEET + "conferenceRecords",
                       params={"filter": "end_time IS NULL", "pageSize": 25}, log=False)
        recs = r.get("conferenceRecords", []) if st == 200 else None
        if recs is None:
            c.append_event("sample", status=st, error=r)
            print(f"sample HTTP {st}: {c.redact(str(r))[:200]}")
        else:
            out = []
            for rec in recs:
                name = rec["name"]
                if name not in seen:
                    sst, sp = c.call("watch_sample", "GET", MEET + rec["space"])
                    seen[name] = {"space_status": sst, "space": sp if sst == 200 else sp}
                out.append({"record": rec, **seen[name]})
            c.append_event("sample", status=st, live=out)
            codes = [(o["space"].get("meetingCode") if isinstance(o["space"], dict) else "?") for o in out]
            print(f"  [{time.strftime('%H:%M:%S')}] live records: {len(recs)} {codes}")
        stop.wait(every)


def main():
    minutes = float(sys.argv[sys.argv.index("--minutes") + 1]) if "--minutes" in sys.argv else 90
    every = float(sys.argv[sys.argv.index("--every") + 1]) if "--every" in sys.argv else 20
    threading.Thread(target=puller, daemon=True).start()
    threading.Thread(target=sampler, args=(every,), daemon=True).start()
    threading.Timer(minutes * 60, stop.set).start()
    print(f"Watching for {minutes:.0f} min. Type a label + Enter at each step. Ctrl-C to stop.")
    try:
        for line in sys.stdin:
            if line.strip():
                c.append_event("label", text=line.strip())
                print(f"  (label saved: {line.strip()})")
            if stop.is_set():
                break
    except KeyboardInterrupt:
        pass
    stop.set()


if __name__ == "__main__":
    main()
