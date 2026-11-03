"""S6: record what the subscription looks like now. Run now, then once a day for 7 days.

    python3 step6_check.py            # read-only: state, expireTime, suspensionReason
    python3 step6_check.py --renew    # PATCH ttl=0 (documented: 0 means the maximum); only if expireTime is near
    python3 step6_check.py --reactivate   # only if state is SUSPENDED

Each run appends one line to local/[account/]s6.jsonl. After 7 days the Testing-mode refresh token dies
(that is itself a finding: note whether the subscription went SUSPENDED when it did).
"""
import json
import sys

import common as c

WE = "https://workspaceevents.googleapis.com/v1/"


def main():
    name = c.load_state().get("subscription") or c.die("no subscription in state.json; run step1_subscribe.py")
    if "--renew" in sys.argv:
        st, r = c.call("step6_check", "PATCH", WE + name, body={"ttl": "0s"}, params={"updateMask": "ttl"})
        c.show(r)
    if "--reactivate" in sys.argv:
        st, r = c.call("step6_check", "POST", WE + name + ":reactivate", body={})
        c.show(r)
    st, sub = c.call("step6_check", "GET", WE + name)
    c.show(sub)
    line = {"at": c.now_iso(), "status": st, "state": sub.get("state") if isinstance(sub, dict) else None,
            "expireTime": sub.get("expireTime") if isinstance(sub, dict) else None,
            "suspensionReason": sub.get("suspensionReason") if isinstance(sub, dict) else None,
            "updateTime": sub.get("updateTime") if isinstance(sub, dict) else None}
    with open(c.S6_FILE, "a") as f:
        f.write(json.dumps(line) + "\n")
    print("\nS6 line:", line)


if __name__ == "__main__":
    main()
