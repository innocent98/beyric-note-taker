"""One real Skribby bot in one real meeting, watched and stopped, with the provider's status mapping
printed. Run it by hand before setting `BOT_PROVIDER=skribby` (not run by anyone yet).

    PYTHONPATH=. .venv/bin/python scripts/skribby_live_check.py --meet-url https://meet.google.com/abc-defg-hij

What it does, in order: reads the keys, sends a bot with `SkribbyProvider` (the same code the app
runs), checks every few seconds and prints what Skribby said and what the app would read it as,
waits for the bot to be admitted (up to `--lobby-limit` seconds, then stops it), lets it record for
`--stay` seconds, stops it with `SkribbyProvider.stop_bot`, waits for the recording, and prints the
result. `--download` also fetches the audio (and prints its size, never the link); `--delete` then
removes the bot's data at Skribby, the call the app's cleanup makes, and then ASKS SKRIBBY AGAIN
(the bot, and the recording link's HTTP status) and prints whether it is gone: run it before the
privacy page says the audio is deleted. Without `--delete` the data stays at Skribby (7 days by
default) and the dashboard shows it.

Keys: `SKRIBBY_API_KEY` (required), `SKRIBBY_ACCOUNT_ID` (the signed-in bot; leave it out, or pass
`--guest`, for a guest) and `SKRIBBY_REGION` (`eu` or `jp`), read from the environment first and
else from `~/.config/beyric-bakeoff/keys.env` (the bake-off's file, `NAME=value` per line). No key
is ever printed, logged or put in an error.

It talks only to Skribby, never to the app's database or Vexa. It uses Skribby time: the free plan
has 5 bot hours a month; a check of two minutes costs about two minutes of that.
"""

import argparse
import os
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from app.bot_provider import BotProviderError
from app.meeting_url import UnsupportedMeetingUrl, parse_meeting_url
from app.skribby import SkribbyClient, SkribbyNotFound, SkribbyProvider, map_bot

DEFAULT_KEYS_FILE = Path.home() / ".config" / "beyric-bakeoff" / "keys.env"
KEY_NAMES = ("SKRIBBY_API_KEY", "SKRIBBY_ACCOUNT_ID", "SKRIBBY_REGION", "OPENAI_API_KEY")
TERMINAL_RAW = (
    "finished",
    "not_admitted",
    "auth_required",
    "invalid_credentials",
    "invalid_api_key",
    "failed",
)
MAX_POLL_FAILURES = 5


def utcnow() -> datetime:
    """The time the status mapping reads (tests replace it)."""
    return datetime.now(UTC)


class CheckError(Exception):
    """Something the person running it can fix; printed plainly, no traceback."""


class NoLedger:
    """There is no database here: the bot this run sent is remembered by the provider itself."""

    def live_bots(self, platform, code):
        return []

    def bots_for_prefix(self, prefix):
        return []


def load_keys(env=None, keys_file=None) -> dict:
    """The three keys, the environment winning over the file. Nothing here prints a value."""
    env = os.environ if env is None else env
    keys: dict[str, str] = {}
    path = Path(keys_file) if keys_file else DEFAULT_KEYS_FILE
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            name = name.strip().removeprefix("export ").strip()
            value = value.strip().strip("'\"")
            if name in KEY_NAMES and value:
                keys[name] = value
    for name in KEY_NAMES:
        if env.get(name, "").strip():
            keys[name] = env[name].strip()
    return keys


def scrub(text: str, keys: dict) -> str:
    for name in KEY_NAMES:
        value = keys.get(name)
        if value:
            text = text.replace(value, "[redacted]")
    return text


def build(args, keys) -> SkribbyProvider:
    if not keys.get("SKRIBBY_API_KEY"):
        raise CheckError(
            "SKRIBBY_API_KEY is missing. Put it in ~/.config/beyric-bakeoff/keys.env "
            "or set it as an environment variable."
        )
    account = "" if args.guest else keys.get("SKRIBBY_ACCOUNT_ID", "")
    client = SkribbyClient(
        api_key=keys["SKRIBBY_API_KEY"],
        region=(keys.get("SKRIBBY_REGION") or "eu").lower(),
        account_id=account,
        bot_name=args.bot_name,
        transcription_model=args.model,
        left_alone_seconds=300,
        max_poll_hours=1,
    )
    return SkribbyProvider(client, NoLedger())


def owner_words(info) -> str:
    """What the owner's page would say for this state (the app's own words), when the app can be
    imported; the state alone otherwise."""
    try:
        from app import pipeline, services

        state = pipeline._live_state(info)
        text = services.BOT_STATE_TEXT.get(state or "", "")
        return f"{state}: {text.format(bot='the bot')}" if state else "(not a live state)"
    except Exception:
        return ""


def main(argv=None, *, env=None, out=None, sleep=time.sleep, clock=time.monotonic) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(description="Send one Skribby bot into one meeting and watch.")
    parser.add_argument("--meet-url", required=True, help="a Google Meet (or Zoom) link")
    parser.add_argument("--keys-file", default=None, help=f"default {DEFAULT_KEYS_FILE}")
    parser.add_argument("--guest", action="store_true", help="ignore SKRIBBY_ACCOUNT_ID")
    parser.add_argument("--bot-name", default="Beyric Meeting Support")
    parser.add_argument("--model", default="groq/whisper-large-v3-turbo")
    parser.add_argument("--interval", type=float, default=5.0, help="seconds between checks")
    parser.add_argument(
        "--lobby-limit", type=float, default=300.0, help="seconds to wait at the door"
    )
    parser.add_argument("--stay", type=float, default=60.0, help="seconds to record once in")
    parser.add_argument("--max-minutes", type=float, default=15.0, help="the whole run's limit")
    parser.add_argument(
        "--download", action="store_true", help="fetch the audio and print its size"
    )
    parser.add_argument(
        "--delete", action="store_true", help="delete the bot's data at Skribby after"
    )
    args = parser.parse_args(argv)
    keys = load_keys(env, args.keys_file)

    def say(text: str = "") -> None:
        print(scrub(text, keys), file=out, flush=True)

    try:
        platform, code = parse_meeting_url(args.meet_url)
        provider = build(args, keys)
    except UnsupportedMeetingUrl:
        say("That is not a Google Meet or Zoom meeting link.")
        return 2
    except CheckError as exc:
        say(str(exc))
        return 2
    client = provider._client
    mode = "signed in" if keys.get("SKRIBBY_ACCOUNT_ID") and not args.guest else "guest"
    say(f"Sending a {mode} bot to {platform} meeting {code} (key and link not shown further).")

    started = clock()
    bot_id = None
    try:
        bot_id = provider.send_bot(args.meet_url)
        say(f"Bot created: {bot_id}")
        return watch(provider, client, bot_id, platform, code, args, say, sleep, clock, started)
    except KeyboardInterrupt:
        say("Interrupted: stopping the bot.")
        return 130
    except BotProviderError as exc:
        say(f"Skribby problem ({type(exc).__name__}): {exc}")
        return 1
    finally:
        if bot_id is not None:
            try:
                provider.stop_bot(platform, code)  # a stop of a bot that is over is fine
            except Exception as exc:
                say(
                    "Could not confirm the bot was stopped "
                    f"({type(exc).__name__}): check the dashboard."
                )


def watch(provider, client, bot_id, platform, code, args, say, sleep, clock, started) -> int:
    """Poll until the bot is over, stopping it at the lobby limit, after `--stay`, or at the cap."""
    say(f"{'time':>6}  {'skribby says':<24} {'the app reads it as':<34} lobby")
    last = None
    admitted_at = stopped_at = None
    failures = 0
    final = None
    while True:
        now = clock() - started
        try:
            raw = client.get_bot(bot_id)
            failures = 0
        except BotProviderError as exc:
            failures += 1
            say(f"{now:6.0f}s  (check failed: {type(exc).__name__})")
            if failures >= MAX_POLL_FAILURES:
                say("Too many failed checks in a row; stopping.")
                return 1
            sleep(args.interval)
            continue
        info = map_bot(raw, utcnow())
        shown = (raw.get("status"), info.status, info.reached_lobby)
        if shown != last:
            words = owner_words(info)
            say(
                f"{now:6.0f}s  {str(raw.get('status')):<24} "
                f"{info.status:<34} {'yes' if info.reached_lobby else 'no'}"
                + (f"   [{words}]" if words else "")
            )
            if info.failure_reason or info.completion_reason:
                say(f"        reason: {info.failure_reason or info.completion_reason}")
            last = shown
        if raw.get("status") in TERMINAL_RAW:
            final = info
            break
        if admitted_at is None and info.status == "active":
            admitted_at = now
            say(f"        admitted after {now:.0f}s; recording for {args.stay:.0f}s")
        if stopped_at is None:
            limit = None
            if admitted_at is None and now >= args.lobby_limit:
                limit = "not admitted within the lobby limit"
            elif admitted_at is not None and now - admitted_at >= args.stay:
                limit = "stay time reached"
            elif now >= args.max_minutes * 60:
                limit = "run time limit"
            if limit:
                say(f"        stopping the bot ({limit})")
                provider.stop_bot(platform, code)
                stopped_at = now
        elif now - stopped_at > 600:
            say("The bot has not finished 10 minutes after the stop; giving up watching.")
            return 1
        sleep(args.interval)

    return finish(provider, bot_id, final, args, say)


def finish(provider, bot_id, final, args, say) -> int:
    say()
    say(f"Over: Skribby `{final.status}`, recording {final.recording_status or 'none'}.")
    client = provider._client
    info = provider.get_meeting_by_id(bot_id)
    names = sorted({seg["speaker"] or "(no name)" for seg in info.segments})
    say(
        f"Segments for speaker labelling: {len(info.segments)}; "
        f"speakers: {', '.join(names) or 'none'}"
    )
    if info.segments and names == ["(no name)"]:
        say("  -> every segment has NO speaker name: meetings would be unlabelled.")
    ok = info.status == "completed"
    raw = client.get_bot(bot_id)
    link = raw.get("recording_url") if isinstance(raw.get("recording_url"), str) else None
    if link:
        say(f"Recording host (the only part of the link printed): {urlsplit(link).hostname}")
    if args.download and info.recording_id is not None:
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "recording"
            provider.download_audio(info.recording_id, info.media_id, target)
            say(f"Audio downloaded: {target.stat().st_size} bytes (deleted from this computer).")
    elif args.download:
        say("No recording to download.")
        ok = False
    if args.delete:
        ok = delete_and_prove(provider, client, bot_id, info, link, say) and ok
    return 0 if ok else 1


def delete_and_prove(provider, client, bot_id, info, link, say) -> bool:
    """Delete the bot's data the way the app does, then ask Skribby again and print what it says:
    the bot (`GET /bot/{id}`) and the recording link, so the data can be seen to be gone BEFORE the
    privacy page says so. True only when it is shown to be gone: the bot answers 404, or answers
    with no recording link and no transcript AND the link answers 404 or 410. A link that answers
    403 proves nothing (an expired signed link answers 403 too): that is "inconclusive" and is
    not counted as gone; one that still answers 200 is not gone."""
    if info.recording_id is not None:
        provider.delete_recording(info.recording_id)
    else:
        provider.delete_bot_record(bot_id)
    say("DELETE /bot/{id} answered success and the provider's own re-check passed. Asking again:")
    bot_gone = False
    try:
        after = client.get_bot(bot_id)
        has_link = bool(after.get("recording_url"))
        segments = len(after.get("transcript") or [])
        say(
            f"  GET /bot/{{id}}: 200, recording link {'STILL THERE' if has_link else 'absent'}, "
            f"{segments} transcript segment(s), status `{after.get('status')}`"
        )
        stripped = not has_link and segments == 0
    except SkribbyNotFound:
        say("  GET /bot/{id}: 404 (the bot is gone)")
        bot_gone, stripped = True, True
    link_state = None
    if link:
        try:
            status = client.probe(link)
        except BotProviderError as exc:
            say(f"  the recording link could not be checked ({type(exc).__name__}): {exc}")
            link_state = "inconclusive"
        else:
            if status in (404, 410):
                link_state = "gone"
            elif status == 200:
                link_state = "there"
            else:
                link_state = "inconclusive"
            note = {
                "gone": "",
                "there": "  (the recording is STILL REACHABLE)",
                "inconclusive": "  (inconclusive: an expired link answers 403 too)",
            }[link_state]
            say(f"  the recording link now answers HTTP {status}{note}")
    if link_state == "there" or not stripped:
        verdict = "NOT shown to be gone: do not promise deletion yet."
        gone = False
    elif bot_gone or link_state in (None, "gone"):
        verdict = "gone."
        gone = True
    else:
        verdict = "inconclusive (the link's answer proves nothing): do not promise deletion yet."
        gone = False
    say("  -> " + verdict)
    return gone


if __name__ == "__main__":
    sys.exit(main())
