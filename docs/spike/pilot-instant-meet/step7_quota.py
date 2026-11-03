"""S7: how many conferenceRecords.list calls fit in a minute? Hits the API until the first 429.

    python3 step7_quota.py [--max 200]
Prints calls-before-429, elapsed time, and the quota block Google returns; then waits for the window to reopen.
"""
import sys
import time

import common as c

MEET = "https://meet.googleapis.com/v2/"


def main():
    cap = int(sys.argv[sys.argv.index("--max") + 1]) if "--max" in sys.argv else 200
    t0, ok = time.time(), 0
    for i in range(cap):
        st, r = c.call("step7_quota", "GET", MEET + "conferenceRecords",
                       params={"filter": "end_time IS NULL", "pageSize": 1}, log=False)
        if st == 200:
            ok += 1
            continue
        el = time.time() - t0
        c.write_log("step7_quota", "GET", MEET + "conferenceRecords?filter=end_time IS NULL&pageSize=1", None, st, r, el)
        print(f"\nFirst non-200 on call {i + 1}: HTTP {st} after {ok} successes in {el:.1f}s")
        c.show(r)
        if st == 429:
            print("\nWaiting for the window to reopen (checks every 10 s, up to 130 s) ...")
            w0 = time.time()
            while time.time() - w0 < 130:
                time.sleep(10)
                s2, _ = c.call("step7_quota", "GET", MEET + "conferenceRecords",
                               params={"filter": "end_time IS NULL", "pageSize": 1}, log=False)
                if s2 == 200:
                    print(f"Reopened after {time.time() - w0:.0f}s of waiting.")
                    break
        break
    else:
        print(f"\nNo 429 in {cap} calls over {time.time() - t0:.1f}s: per-minute limit is above {cap / ((time.time() - t0) / 60):.0f}/min.")
    print(f"\nS7: {ok} calls succeeded back to back. Polling 10 users every 30 s needs 20 calls/min.")


if __name__ == "__main__":
    main()
