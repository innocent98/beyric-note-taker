#!/usr/bin/env python3
"""Rewrite the server's .env into the layout of .env.example. Standard library only.

    python3 scripts/clean_env.py /opt/beyric-note-taker/.env            dry run (default)
    python3 scripts/clean_env.py /opt/beyric-note-taker/.env --apply    back up, then rewrite

It prints key NAMES only: never a value, never any part of one. The values are carried over
byte for byte; only the comments, the blank lines and the keys listed below as removable change.
Details and the audit behind the lists: docs/sop/2026-11-20-env-and-nginx-cleanup.md.
"""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
import tempfile
from datetime import datetime, timezone

# The sections of .env.example, in order. tests/test_clean_env.py keeps this equal to that file.
LAYOUT = [
    (
        "Core",
        [
            "APP_ENV",
            "COOKIE_SECURE",
            "SESSION_SECRET",
            "EDGE_SHARED_SECRET",
            "OWNER_EMAIL",
            "OWNER_PASSWORD",
            "PUBLIC_BASE_URL",
        ],
    ),
    ("Database", ["POSTGRES_PASSWORD"]),
    ("Mail", ["RESEND_API_KEY", "MAIL_FROM"]),
    (
        "Google sign-in and calendar",
        ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "TOKEN_ENC_KEY", "CALENDAR_ENABLED"],
    ),
    (
        "Microsoft",
        [
            "MICROSOFT_CLIENT_ID",
            "MICROSOFT_CLIENT_SECRET",
            "MICROSOFT_SECRET_EXPIRES_ON",
            "MICROSOFT_CALENDAR_ENABLED",
        ],
    ),
    (
        "Bot provider (Skribby)",
        [
            "SKRIBBY_API_KEY",
            "SKRIBBY_ACCOUNT_ID",
            "SKRIBBY_ACCOUNT_NAME",
            "SKRIBBY_BOT_NAME",
            "BOT_INVITE_EMAIL",
            "BOT_CHAT_NOTICE_ENABLED",
        ],
    ),
    (
        "Transcription and notes",
        ["OPENAI_API_KEY", "TRANSCRIPT_SOURCE", "SKRIBBY_TRANSCRIPTION_MODEL"],
    ),
    (
        "Billing",
        [
            "BILLING_ENABLED",
            "BILLING_ALLOWED_EMAILS",
            "TRIAL_SECONDS",
            "HOURS_MODE",
            "FEATURE_GATES_MODE",
            "PAYSTACK_SECRET_KEY",
            "PAYSTACK_PUBLIC_KEY",
            "PAYSTACK_WEBHOOK_SECRET",
            "FLUTTERWAVE_SECRET_KEY",
            "FLUTTERWAVE_SECRET_HASH",
            "FLUTTERWAVE_PUBLIC_KEY",
        ],
    ),
    ("Feature switches", ["MULTI_TEAM_ENABLED", "SERIES_FOLLOW_MEETINGS"]),
    (
        "Optional",
        [
            "AUDIO_RETENTION_DAYS",
            "AUDIO_DIR",
            "MAX_POLL_HOURS",
            "POLL_INTERVAL_SECONDS",
            "BOT_LEFT_ALONE_SECONDS",
            "BOT_MAX_LOCAL",
            "USER_BOT_LIMIT",
            "HOURS_RESERVE_MINUTES",
            "TEAMS_JOIN_ENABLED",
            "AUTOJOIN_ENABLED",
            "AUTOJOIN_LEAD_SECONDS",
            "SCHEDULER_DISPATCH_SECONDS",
            "SCHEDULER_SYNC_SECONDS",
            "SPEAKER_LINE_CORRECTION",
            "PREFER_ORGANIZER_BOT",
            "ORGANIZER_BOT_WAIT_SECONDS",
            "NAME_BY_MEET_TIMELINE",
            "SKRIBBY_NAME_BY_ELIMINATION",
            "SKRIBBY_REGION",
            "SKRIBBY_DOWNLOAD_HOSTS",
            "BOT_NAME_APPLIES_WHEN_SIGNED_IN",
            "TRANSCRIPT_SHADOW_PERCENT",
            "TRANSCRIPTION_MODEL",
            "NOTES_MODEL",
            "EXTRACT_MODEL",
            "EXTRACT_MAX_OUTPUT_TOKENS",
            "TRANSLATE_MODEL",
            "ASK_MODEL",
            "ASK_DAILY_LIMIT",
            "RECIPE_MODEL",
            "RECIPE_DAILY_LIMIT",
            "BRIEF_MAX_CHARS",
            "DRAFT_MAX_BLOCKS",
            "IMPORT_MAX_BYTES",
            "IMPORT_MAX_PAGES",
            "TOPICS_MODEL",
            "MAIL_PROVIDER",
            "MAIL_DAILY_CAP",
            "MAIL_RECIPIENT_DAILY_CAP",
            "MAIL_HOURLY_BUDGET",
            "MAIL_HOURLY_RESERVE",
            "ADMIN_ALERT_EMAIL",
            "GOOGLE_OAUTH_TESTING",
            "GOOGLE_APP_VERIFIED",
            "TOKEN_ENC_KEY_PREVIOUS",
            "TOKEN_ENC_KEY_VERSION",
            "ICS_ALLOWED_HOSTS",
            "MICROSOFT_AUTHORITY",
            "MICROSOFT_ALLOWED_TENANTS",
            "MICROSOFT_PUBLISHER_VERIFIED",
            "MICROSOFT_ORGANIZER_DOMAIN_TRUST",
            "TRIAL_DAYS",
            "TRIAL_DAILY_BUDGET_SECONDS",
            "BILLING_LIVE",
            "BILLING_LIVE_SINCE",
            "BILLING_COUNTRY_ROUTES",
            "BILLING_RETURN_URL",
            "BILLING_SELLER_NAME",
            "BILLING_SELLER_ADDRESS",
            "BILLING_SELLER_REGISTRATION_NO",
            "BILLING_SELLER_TAX_ID",
            "BILLING_SELLER_EMAIL",
            "BILLING_VAT_RATE_BP",
        ],
    ),
    ("Local development (outside Docker)", ["DATABASE_URL"]),
]
UNRECOGNISED_TITLE = "Not recognised (kept)"

# An old name the app still reads (app/config.py, `bot_max_local`): kept as written, placed next to
# the name that replaced it.
LEGACY_ALIASES = {"MAX_ACTIVE_BOTS": "BOT_MAX_LOCAL"}

# Missing (or empty) one of these and compose or the app refuses to start (or cannot work):
# compose.yaml `${NAME:?...}` for POSTGRES_PASSWORD, app/auth.py `check_start` for SESSION_SECRET
# (32 characters), `skribby.require_configured` (web, worker and scheduler refuse to start) for
# SKRIBBY_API_KEY, and no notes or transcript without OPENAI_API_KEY.
REQUIRED = ("POSTGRES_PASSWORD", "SESSION_SECRET", "SKRIBBY_API_KEY", "OPENAI_API_KEY")

# Names the app and compose.yaml no longer read at all (the self-hosted bot host, Vexa, was removed
# on 2026-11-20, docs/sop/2026-11-20-remove-vexa.md): a leftover line is removed whatever its value.
# Everything starting `VEXA_` goes (its database password, storage secret, API key, image tag, base
# URL), and so do `BOT_NAME` (the name of that host's bot) and `BOT_PROVIDER` (there is one
# provider). `COMPOSE_PROFILES` goes only when it is exactly `vexa`: that was the profile that
# started the host; any other value is someone else's and is kept.
# The pilot-expiry settings went the same way on 2026-11-21
# (docs/sop/2026-11-21-remove-pilot-expiry.md): the job is gone, so a leftover line of any value
# is removed. A server .env that still holds one starts fine either way: `Settings` ignores names
# it does not know.
UNUSED_PREFIX = "VEXA_"
UNUSED_NAMES = (
    "BOT_NAME",
    "BOT_PROVIDER",
    "PAID_PLANS_OPEN_ON",
    "PILOT_EXPIRY_DAYS",
    "PILOT_EXPIRY_NOTICE_DAYS",
    "PILOT_EXPIRY_MIN_NOTICE_DAYS",
)

# Removed only when the value IS the code's default (app/config.py), so the app behaves the same
# without the line. Left out on purpose, although they have a default: BILLING_LIVE,
# BILLING_LIVE_SINCE (the go-live cutover, written down on purpose) and TOKEN_ENC_KEY_VERSION
# (explicit safety and rotation state).
# kind: s = text, i = whole number, b = true/false. tests/test_clean_env.py checks each default
# against Settings, so this table cannot drift from the code.
DEFAULTS = {
    "AUDIO_RETENTION_DAYS": ("i", 7),
    "AUDIO_DIR": ("s", "data/audio"),
    "MAX_POLL_HOURS": ("i", 6),
    "POLL_INTERVAL_SECONDS": ("i", 30),
    "BOT_LEFT_ALONE_SECONDS": ("i", 300),
    "BOT_MAX_LOCAL": ("i", 20),
    "USER_BOT_LIMIT": ("i", 2),
    "HOURS_RESERVE_MINUTES": ("i", 30),
    "TEAMS_JOIN_ENABLED": ("b", True),
    "AUTOJOIN_ENABLED": ("b", True),
    "AUTOJOIN_LEAD_SECONDS": ("i", 60),
    "SCHEDULER_DISPATCH_SECONDS": ("i", 10),
    "SCHEDULER_SYNC_SECONDS": ("i", 60),
    "SPEAKER_LINE_CORRECTION": ("b", True),
    "PREFER_ORGANIZER_BOT": ("b", True),
    "ORGANIZER_BOT_WAIT_SECONDS": ("i", 120),
    "NAME_BY_MEET_TIMELINE": ("b", True),
    "SKRIBBY_NAME_BY_ELIMINATION": ("b", True),
    "SKRIBBY_REGION": ("s", "eu"),
    "SKRIBBY_DOWNLOAD_HOSTS": ("s", ""),
    "BOT_NAME_APPLIES_WHEN_SIGNED_IN": ("b", False),
    "TRANSCRIPT_SHADOW_PERCENT": ("i", 0),
    "TRANSCRIPTION_MODEL": ("s", "gpt-transcribe"),
    "NOTES_MODEL": ("s", "gpt-6-luna"),
    "EXTRACT_MODEL": ("s", "gpt-6-luna"),
    "EXTRACT_MAX_OUTPUT_TOKENS": ("i", 32000),
    "TRANSLATE_MODEL": ("s", "gpt-6-luna"),
    "ASK_MODEL": ("s", "gpt-6-luna"),
    "ASK_DAILY_LIMIT": ("i", 30),
    "RECIPE_MODEL": ("s", "gpt-6-luna"),
    "RECIPE_DAILY_LIMIT": ("i", 20),
    "BRIEF_MAX_CHARS": ("i", 12000),
    "DRAFT_MAX_BLOCKS": ("i", 60),
    "IMPORT_MAX_BYTES": ("i", 5000000),
    "IMPORT_MAX_PAGES": ("i", 40),
    "TOPICS_MODEL": ("s", "gpt-6-luna"),
    "MAIL_PROVIDER": ("s", ""),
    "MAIL_DAILY_CAP": ("i", 80),
    "MAIL_RECIPIENT_DAILY_CAP": ("i", 5),
    "MAIL_HOURLY_BUDGET": ("i", 30),
    "MAIL_HOURLY_RESERVE": ("i", 10),
    "ADMIN_ALERT_EMAIL": ("s", ""),
    "GOOGLE_OAUTH_TESTING": ("b", False),
    "GOOGLE_APP_VERIFIED": ("b", False),
    "TOKEN_ENC_KEY_PREVIOUS": ("s", ""),
    "ICS_ALLOWED_HOSTS": ("s", "calendar.google.com,outlook.office365.com,outlook.live.com"),
    "MICROSOFT_AUTHORITY": ("s", "consumers"),
    "MICROSOFT_ALLOWED_TENANTS": ("s", ""),
    "MICROSOFT_PUBLISHER_VERIFIED": ("b", False),
    "MICROSOFT_ORGANIZER_DOMAIN_TRUST": ("b", False),
    "TRIAL_DAYS": ("i", 14),
    "TRIAL_DAILY_BUDGET_SECONDS": ("i", 0),
    "BILLING_COUNTRY_ROUTES": ("s", "NG=NGN:paystack,*=USD:flutterwave"),
    "BILLING_RETURN_URL": ("s", ""),
    "BILLING_SELLER_NAME": ("s", "Beyrichq LTD"),
    "BILLING_SELLER_ADDRESS": ("s", ""),
    "BILLING_SELLER_REGISTRATION_NO": ("s", ""),
    "BILLING_SELLER_TAX_ID": ("s", ""),
    "BILLING_SELLER_EMAIL": ("s", ""),
    "BILLING_VAT_RATE_BP": ("i", 0),
}

# An empty value and an absent one behave the same (app/billing/paystack.py strips the value and
# tests it for truth; app/config.py only rejects a value that is blank but not empty).
EMPTY_IS_ABSENT = {"PAYSTACK_WEBHOOK_SECRET"}

TRUE_WORDS = {"1", "true", "yes", "on", "t", "y"}
FALSE_WORDS = {"0", "false", "no", "off", "f", "n"}
KEY_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_.-]*)\s*=(.*)$")
AFTER_QUOTE = re.compile(r"^\s*(#.*)?$")
RECREATE = "docker compose up -d --force-recreate --no-deps web worker scheduler"
GATE_CHECK = """bash -c '. deploy/gate.sh; join_due_soon && echo "no join is due"'"""


class EnvError(Exception):
    """A reason to refuse. The message names a key or a line number, never a value."""


def _closing_quote(text: str, start: int) -> int:
    """Index of the quote that closes the one at `start`, or -1."""
    quote = text[start]
    i = start + 1
    while i < len(text):
        if quote == '"' and text[i] == "\\":
            i += 2
            continue
        if text[i] == quote:
            return i
        i += 1
    return -1


def parse(text: str) -> list[tuple[str, str]]:
    """The (key, right-hand side) pairs in file order, comments and blank lines dropped. The
    right-hand side is the exact text after the `=`, including quotes and any text after them."""
    lines = text.replace("\r\n", "\n").split("\n")
    pairs: list[tuple[str, str]] = []
    i = 0
    while i < len(lines):
        number = i + 1
        line = lines[i]
        i += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        found = KEY_LINE.match(line)
        if not found:
            raise EnvError(f"line {number} is not a KEY=value line")
        key, rhs = found.group(1), found.group(2)
        lead = rhs.lstrip()
        if lead[:1] in ("'", '"'):
            offset = len(rhs) - len(lead)
            end = _closing_quote(rhs, offset)
            while end < 0:
                if i >= len(lines):
                    raise EnvError(f"line {number}: a quoted value is never closed")
                rhs += "\n" + lines[i]
                i += 1
                end = _closing_quote(rhs, offset)
            if not AFTER_QUOTE.match(rhs[end + 1 :].split("\n")[0]):
                raise EnvError(f"line {number}: text after the closing quote")
        pairs.append((key, rhs))
    return pairs


def effective(rhs: str) -> tuple[str, bool]:
    """(the value as written between the quotes or before a ` #` comment, whether it is plain).
    Plain means no escape and no `$`: only then is it safe to compare with a default."""
    text = rhs.strip()
    if text[:1] in ("'", '"'):
        inner = text[1 : _closing_quote(text, 0)]
        return inner, "\\" not in inner and "$" not in inner
    comment = re.search(r"\s#", text)
    if comment:
        text = text[: comment.start()]
    text = text.strip()
    return text, "$" not in text


def equals_default(key: str, rhs: str) -> bool:
    if key not in DEFAULTS:
        return False
    value, plain = effective(rhs)
    if not plain:
        return False
    kind, default = DEFAULTS[key]
    if kind == "b":
        word = value.strip().lower()
        if word in TRUE_WORDS or word in FALSE_WORDS:
            return (word in TRUE_WORDS) == default
        return False
    if kind == "i":
        try:
            return int(value) == default
        except ValueError:
            return False
    if key in ("SKRIBBY_REGION", "MICROSOFT_AUTHORITY"):
        return value.strip().lower() == default
    return value == default


def is_unused(key: str, rhs: str) -> bool:
    """Whether a line is a leftover of something the app no longer reads (`UNUSED_NAMES`)."""
    if key.startswith(UNUSED_PREFIX) or key in UNUSED_NAMES:
        return True
    return key == "COMPOSE_PROFILES" and effective(rhs)[0].strip().lower() == "vexa"


def is_empty(rhs: str) -> bool:
    value, plain = effective(rhs)
    return plain and value == ""


def is_true(rhs: str) -> bool:
    return effective(rhs)[0].strip().lower() in TRUE_WORDS


class Plan:
    def __init__(self, pairs: list[tuple[str, str]]):
        self.values: dict[str, str] = {}
        self.duplicates: list[str] = []
        for key, rhs in pairs:
            if key in self.values and key not in self.duplicates:
                self.duplicates.append(key)
            self.values[key] = rhs  # compose reads the last one
        self.removed_default = [k for k, v in self.values.items() if equals_default(k, v)]
        self.removed_empty = [
            k for k, v in self.values.items() if k in EMPTY_IS_ABSENT and is_empty(v)
        ]
        self.removed_unused = [k for k, v in self.values.items() if is_unused(k, v)]
        removed = set(self.removed_default) | set(self.removed_empty) | set(self.removed_unused)
        known = {k for _, keys in LAYOUT for k in keys} | set(LEGACY_ALIASES)
        self.kept = [k for k in self.values if k in known and k not in removed]
        self.unrecognised = [k for k in self.values if k not in known and k not in removed]
        self.missing = [k for k in REQUIRED if k not in self.values or is_empty(self.values[k])]
        # compose expands `$NAME` in an unquoted or double-quoted value, so a kept value that holds
        # a `$` might depend on another key; moving or removing keys could change what it means.
        self.dollar_keys = [
            k
            for k in self.kept + self.unrecognised
            if "$" in self.values[k] and self.values[k].strip()[:1] != "'"
        ]
        self.warnings: list[str] = []
        self._conditional_rules()

    def _conditional_rules(self) -> None:
        env = effective(self.values["APP_ENV"])[0] if "APP_ENV" in self.values else "production"
        env = env.strip().lower()
        wants_key = any(
            k in self.values and not is_empty(self.values[k])
            for k in ("GOOGLE_CLIENT_ID", "MICROSOFT_CLIENT_ID")
        ) or ("CALENDAR_ENABLED" in self.values and is_true(self.values["CALENDAR_ENABLED"]))
        if env == "production" and wants_key:
            if "TOKEN_ENC_KEY" not in self.values or is_empty(self.values["TOKEN_ENC_KEY"]):
                self.missing.append("TOKEN_ENC_KEY")
        if (
            "SESSION_SECRET" in self.values
            and len(effective(self.values["SESSION_SECRET"])[0]) < 32
        ):
            self.warnings.append("SESSION_SECRET is shorter than 32 characters")
        if "EDGE_SHARED_SECRET" not in self.values or is_empty(self.values["EDGE_SHARED_SECRET"]):
            self.warnings.append(
                "EDGE_SHARED_SECRET is empty: every visitor shares one login limit"
            )

    def render(self) -> str:
        placed = set(self.kept)
        blocks = []
        for title, keys in LAYOUT:
            lines = []
            for key in keys:
                for name in [key] + [a for a, target in LEGACY_ALIASES.items() if target == key]:
                    if name in placed:
                        lines.append(f"{name}={self.values[name]}")
            if lines:
                blocks.append("\n".join([f"# ---- {title} ----"] + lines))
        if self.unrecognised:
            lines = [f"{k}={self.values[k]}" for k in self.unrecognised]
            blocks.append("\n".join([f"# ---- {UNRECOGNISED_TITLE} ----"] + lines))
        return "\n\n".join(blocks) + "\n"

    def check_nothing_lost(self, rendered: str) -> None:
        """Parse what would be written and compare it with the input, key by key."""
        again = dict(parse(rendered))
        removed = set(self.removed_default) | set(self.removed_empty) | set(self.removed_unused)
        for key, rhs in self.values.items():
            if key in removed:
                if key in again:
                    raise EnvError(f"{key} should have been removed but is still there")
            elif again.get(key) != rhs:
                raise EnvError(f"{key} would lose or change its value: nothing was written")
        extra = set(again) - set(self.values)
        if extra:
            raise EnvError("the result holds a key the input did not have: nothing was written")


def read_checked(path: str) -> tuple[bytes, os.stat_result]:
    """The file's bytes, after refusing a symlink, a non-regular file or one not owned by us."""
    try:
        if stat.S_ISLNK(os.lstat(path).st_mode):
            raise EnvError("the path is a symlink: refusing to touch it")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        raise EnvError("the file does not exist") from None
    except OSError as exc:
        raise EnvError(f"cannot open the file ({exc.strerror})") from None
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise EnvError("the path is not a regular file")
        if info.st_uid != os.geteuid():
            raise EnvError("the file belongs to another user: run this as its owner")
        return handle.read(), info


def unchanged_since(path: str, info: os.stat_result) -> bool:
    now = os.lstat(path)
    same = (now.st_dev, now.st_ino, now.st_size, now.st_mtime_ns)
    return same == (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def write_private(
    path: str,
    data: bytes,
    *,
    exclusive: bool,
    owner: tuple[int, int] | None,
    expect: os.stat_result | None = None,
) -> None:
    """Write `data` to `path` with mode 600. `exclusive` refuses to overwrite (the backup) and
    removes what it made if writing fails. `expect` is the target's stat from when it was read:
    if the target changed since, nothing is replaced."""
    directory = os.path.dirname(path) or "."
    if exclusive:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        tmp = None
    else:
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".env.", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        if owner is not None:
            try:
                os.fchown(fd, owner[0], owner[1])
            except PermissionError:
                pass  # the group is the only part we may not be able to copy; the owner is ours
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if tmp is not None:
            if expect is not None and not unchanged_since(path, expect):
                raise EnvError("the file changed while it was being rewritten: nothing replaced")
            os.replace(tmp, path)
            dir_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    except BaseException:
        leftover = tmp if tmp is not None else (path if exclusive else None)
        if leftover is not None and os.path.exists(leftover):
            os.unlink(leftover)
        raise


def names(keys: list[str]) -> str:
    return ", ".join(keys) if keys else "none"


def run(path: str, apply: bool, out) -> int:
    raw, info = read_checked(path)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise EnvError("the file is not valid UTF-8") from None
    plan = Plan(parse(text))
    print(f"clean_env: {path}", file=out)
    print(f"kept ({len(plan.kept)}): {names(plan.kept)}", file=out)
    print(
        f"removed, same as the code default ({len(plan.removed_default)}): "
        f"{names(plan.removed_default)}",
        file=out,
    )
    print(
        f"removed, empty line that means the same as none ({len(plan.removed_empty)}): "
        f"{names(plan.removed_empty)}",
        file=out,
    )
    print(
        f"removed, no longer read by the app ({len(plan.removed_unused)}): "
        f"{names(plan.removed_unused)}",
        file=out,
    )
    print(f"kept, not recognised ({len(plan.unrecognised)}): {names(plan.unrecognised)}", file=out)
    print(
        f"duplicate keys, the last one wins and is the one kept ({len(plan.duplicates)}): "
        f"{names(plan.duplicates)}",
        file=out,
    )
    for warning in plan.warnings:
        print(f"warning: {warning}", file=out)
    if stat.S_IMODE(info.st_mode) != 0o600:
        print(
            f"warning: the file mode is {stat.S_IMODE(info.st_mode):o}; the new file is 600",
            file=out,
        )
    if plan.dollar_keys:
        print(
            f"warning: kept values that contain $, which compose may expand "
            f"({len(plan.dollar_keys)}): {names(plan.dollar_keys)}. --apply refuses while any "
            f"is left; edit them by hand first",
            file=out,
        )
    if plan.missing:
        print(f"MISSING REQUIRED ({len(plan.missing)}): {names(plan.missing)}", file=out)
        raise EnvError("a required key is missing or empty: nothing was written")
    rendered = plan.render()
    plan.check_nothing_lost(rendered)
    if rendered.encode("utf-8") == raw and stat.S_IMODE(info.st_mode) == 0o600:
        print("already clean: nothing to do", file=out)
        return 0
    if not apply:
        print("dry run: nothing was written. Add --apply to write the new file.", file=out)
        return 0
    if plan.dollar_keys:
        raise EnvError("a kept value contains $ (names above): nothing was written")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # noqa: UP017 (host python3)
    backup = f"{path}.bak-{stamp}"
    owner = (info.st_uid, info.st_gid)
    write_private(backup, raw, exclusive=True, owner=owner)
    write_private(path, rendered.encode("utf-8"), exclusive=False, owner=owner, expect=info)
    back, _ = read_checked(path)
    if back != rendered.encode("utf-8"):
        raise EnvError(f"the file read back differs; the original is saved as {backup}")
    print(f"written. The old file is saved as {backup} (mode 600).", file=out)
    print(
        "An env change is read only when a container is recreated (a restart does not).",
        file=out,
    )
    print("Run this ONLY when no meeting is being recorded and no join is due:", file=out)
    print(f"  cd {os.path.dirname(os.path.abspath(path))}", file=out)
    print(f"  {GATE_CHECK}    # a time printed instead means a join is due: wait", file=out)
    print(f"  {RECREATE}", file=out)
    print("To go back: copy the backup over .env, then run the same recreate command.", file=out)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rewrite a server .env into the .env.example layout."
    )
    parser.add_argument("path", help="the .env file to clean")
    parser.add_argument("--apply", action="store_true", help="write it (default: dry run)")
    args = parser.parse_args(argv)
    try:
        return run(args.path, args.apply, sys.stdout)
    except EnvError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"failed: {exc.strerror or type(exc).__name__}", file=sys.stderr)
        return 2
    except Exception as exc:  # never a traceback: it could quote a line of the file
        print(f"failed: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
