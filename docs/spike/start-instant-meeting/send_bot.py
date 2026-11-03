"""Send the note-taker bot from the Vexa on THIS Mac into a meeting, and record what happens.

Run:  python3 send_bot.py abc-defg-hij
      (a meeting code, or the full https://meet.google.com/... link)

Only ever talks to http://localhost (it refuses anything else): never the production server.
Watches for up to 3 minutes, stops the bot, then asks you two questions and saves the result
to local/bot-<code>.txt. Leave at least 10 minutes between two sends.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
from pathlib import Path

import instant_common as ic

DEFAULT_KEY_FILE = Path.home() / "Documents/dev/beyric-note-taker/spike/.env.api"
BOT_NAME = "Beyric Note Taker (recording)"  # the name the app sends
WATCH_SECONDS = 180
POLL_SECONDS = 3


def meeting_url(arg: str) -> tuple[str, str]:
    arg = arg.strip()
    if ic.MEET_CODE.fullmatch(arg):
        return arg, f"https://meet.google.com/{arg}"
    parts = urllib.parse.urlsplit(arg)
    code = parts.path.strip("/")
    if parts.hostname != "meet.google.com" or not ic.MEET_CODE.fullmatch(code):
        raise ic.SpikeError("Give a meeting code like abc-defg-hij or a meet.google.com link.")
    return code, f"https://meet.google.com/{code}"


def vexa(method: str, base: str, key: str, path: str, payload: dict | None = None):
    status, text = ic.request(
        method,
        base + path,
        headers={"X-API-Key": key, "Content-Type": "application/json"},
        body=json.dumps(payload).encode() if payload is not None else None,
    )
    try:
        return status, json.loads(text)
    except ValueError:
        return status, {"raw": ic.redact(text[:200])}


def ask(question: str) -> str:
    while True:
        answer = input(question + " [y/n/skip] ").strip().lower()
        if answer in ("y", "n", "skip"):
            return answer


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("meeting")
    parser.add_argument("--base-url", default="http://localhost:8056")
    parser.add_argument("--key-file", type=Path, default=DEFAULT_KEY_FILE)
    args = parser.parse_args()

    base = ic.require_local_vexa(args.base_url)
    key = ic.read_vexa_key(args.key_file)
    code, url = meeting_url(args.meeting)

    status, answer = vexa("POST", base, key, "/bots", {"meeting_url": url, "bot_name": BOT_NAME})
    if status not in (200, 201):
        raise ic.SpikeError(
            f"Vexa on this Mac answered HTTP {status}: {answer}. Is the stack up "
            "(runbook step 10)? Is a bot from an earlier test still in the room?"
        )
    print(f"Bot sent to {code} at {time.strftime('%H:%M:%S')}. Watch the meeting window now:")
    print("you should see (or not see) a request saying the bot wants to join.\n")

    lines: list[str] = []
    changes: list[str] = []  # only the lines where the status changed
    started = time.monotonic()
    last = None
    try:
        while time.monotonic() - started < WATCH_SECONDS:
            elapsed = time.monotonic() - started
            status, body = vexa("GET", base, key, f"/transcripts/google_meet/{code}")
            data = body.get("data") if isinstance(body.get("data"), dict) else {}
            state = str(body.get("status"))
            reason = data.get("reason")
            line = f"t+{elapsed:5.1f}s status={state} reason={reason} stage=None"
            if state != last:
                print(time.strftime("%H:%M:%S"), line)
                changes.append(line)
                last = state
            lines.append(line)
            if state in ("active", "failed", "completed"):
                time.sleep(POLL_SECONDS * 2)  # let a refusal or a late admit show up
                break
            time.sleep(POLL_SECONDS)
    finally:
        stop_status, _ = vexa("DELETE", base, key, f"/bots/google_meet/{code}")
        print(f"\nStopped the bot (Vexa answered HTTP {stop_status}).")

    summary = ic.summarise_timeline(ic.parse_timeline(lines))
    print("\nWhat the bot did:", summary["plain"])
    saw_prompt = ask("Did you (the host) see a 'wants to join' / Admit prompt for the bot?")
    clicked = "skip"
    if saw_prompt == "y":
        clicked = ask("Did you have to click Admit for the bot to get in?")
    record = {
        "meeting_code": code,
        "sent_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "bot_name": BOT_NAME,
        "api_summary": summary,
        "host_saw_knock_prompt": saw_prompt,
        "host_had_to_admit": clicked,
        "timeline": changes,
    }
    ic.LOCAL.mkdir(exist_ok=True)
    (ic.LOCAL / f"bot-{code}.txt").write_text(json.dumps(record, indent=2))
    print(f"\nSaved local/bot-{code}.txt. Paste the lines above into the chat.")
    print(
        json.dumps(
            {k: record[k] for k in ("meeting_code", "host_saw_knock_prompt")}
            | {"outcome": summary["outcome"], "host_had_to_admit": clicked}
        )
    )


if __name__ == "__main__":
    try:
        main()
    except ic.SpikeError as exc:
        raise SystemExit(f"\nSTOP: {exc}") from None
