"""Print what a meeting hour really costs us, from the database plus labelled estimates.

Run it where the app's `.env` is (the last 30 days; `--days N` changes that):

    docker compose exec -T web python scripts/ai_cost_report.py </dev/null

READ-ONLY: it opens its transaction with `SET TRANSACTION READ ONLY` (Postgres refuses any write
after that) and only runs SELECTs. It never reads or prints a transcript, a title, a name or an
email; people appear only as "user 1", "user 2" (ranked by hours) and teams only as a size.

What is measured and what is not:
  1. Hours: `meetings.recorded_seconds` (length of the recording the pipeline prepared, the unit of
     the hours cap), for meetings created in the window. `usage_entries.seconds` is printed as a
     cross-check: it also counts meetings that were deleted later.
  2. AI spend: the `ai_runs` table has NO cost column, only tokens (`input_tokens`,
     `output_tokens`) and `audio_seconds`. Cost is tokens or audio times the price constants
     below, so it is only as right as those constants. Rows are those created in the window; a
     meeting made earlier but notes-written inside it counts here and not in the hours.
  3. NOT in the database: Skribby's bot and its transcription model, and ElevenLabs. Those are
     ESTIMATES from the constants below, printed in their own block.
  4. The total per hour is a range because the constants are ranges (see each comment).
"""

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import AiRun, Meeting, TeamMember, UsageEntry  # noqa: E402

# ---- Prices. Dollars. Change a number here when its source changes. ----

# OpenAI chat models, per MILLION tokens (input, output). Source: OpenAI pricing page as read in
# docs/sop/2026-11-02-notes-model-gpt-6-luna.md, 2026-11-02. A model not in this table is listed
# as UNPRICED and left out of the totals (never guessed).
OPENAI_TOKEN_PRICES = {
    "gpt-6-luna": (0.10, 0.50),
    "gpt-5.4-mini": (0.75, 4.50),
}

# OpenAI transcription, per audio MINUTE, as (low, high). Low 0.0045 is `gpt-transcribe` from
# secondary sources (docs/sop/2026-11-05-notes-cost-ledger.md, 2026-11-05; NOT confirmed on
# OpenAI's own page); high 0.006 is what OpenAI's page showed for `gpt-4o-transcribe`
# (docs/sop/2026-11-10-diarize-experiment.md, read 2026-11-10), kept as the pessimistic end.
OPENAI_AUDIO_PER_MINUTE = {
    "gpt-transcribe": (0.0045, 0.006),
}

# Skribby bot time, per hour. Source: skribby.io/docs/guides/billing, "$0.35/hour", billed per
# minute (fetched 2026-11-14). It is billed for the time the bot is IN the call, which can be
# longer than the recording (lobby wait, a bot that stays after the meeting, a retried join).
SKRIBBY_BOT_PER_HOUR = 0.35
# Factor on recorded hours to get billed bot hours, as (low, high). Low 1.0 = no overhead. High
# 1.25 is a JUDGEMENT with no source: replace it with the real Skribby invoice hours.
BOT_TIME_FACTOR = (1.0, 1.25)

# Skribby's transcription model on top of the bot, per hour of audio. Skribby makes a transcript
# for every bot meeting (we use it for speaker names, or as the transcript with
# TRANSCRIPT_SOURCE=skribby), so it is billed whatever TRANSCRIPT_SOURCE is.
# `soniox/stt-async-v5`, used until the flip: $0.10 (docs/sop/2026-11-08-skribby-provider.md).
SKRIBBY_OLD_MODEL_PER_HOUR = 0.10
# `elevenlabs/scribe-v2`: $0.20 an hour, from ElevenLabs' own pricing page
# (elevenlabs.io/pricing/api, "Price per hour" for Scribe v2, the same on every plan; keyterm
# prompting is a separate $0.05 an hour and not used here), fetched 2026-11-14. Skribby's billing
# page lists "ElevenLabs v2" at the same $0.20 an hour. We reach ElevenLabs only through Skribby,
# so this is NOT a second charge on top of Skribby's: it is the model line of Skribby's bill.
# Set to None to print "unknown".
ELEVENLABS_PER_HOUR = 0.20
# Meetings created from here on were sent to Skribby with `elevenlabs/scribe-v2`: the server's
# `.env` was flipped at 18:31 WAT on 2026-11-13 (docs/checklist/master-checklist.md, addendum).
ELEVENLABS_SINCE = datetime(2026, 11, 13, 17, 31, tzinfo=UTC)

# A recording longer than this is counted as unusually long: a bot that never left would
# distort the average.
LONG_SECONDS = 3 * 3600

# Team-size buckets: (upper bound inclusive, label).
SIZE_BUCKETS = ((1, "1 (private or alone)"), (3, "2-3"), (9, "4-9"), (10**9, "10 or more"))


# ---- Rows read from the database (numbers and ids only, never text) ----


@dataclass(frozen=True)
class MeetingRow:
    owner_user_id: int
    created_at: datetime
    recorded_seconds: int | None
    bot_provider: str
    team_size: int


@dataclass(frozen=True)
class RunRow:
    meeting_id: int | None
    user_id: int | None
    kind: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    audio_seconds: int | None
    status: str


@dataclass(frozen=True)
class MeetingWithId:
    id: int
    row: MeetingRow


# ---- Reading (SELECTs only) ----


def open_read_only(session: Session) -> None:
    """Make this session's transaction read-only. Must be the first statement."""
    session.execute(text("SET TRANSACTION READ ONLY"))


def fetch_meetings(session: Session, since: datetime) -> list[MeetingWithId]:
    team_id = func.coalesce(Meeting.owner_team_id, Meeting.shared_team_id)
    size = (
        select(func.count())
        .select_from(TeamMember)
        .where(TeamMember.team_id == team_id)
        .correlate(Meeting)
        .scalar_subquery()
    )
    rows = session.execute(
        select(
            Meeting.id,
            Meeting.owner_user_id,
            Meeting.created_at,
            Meeting.recorded_seconds,
            Meeting.bot_provider,
            func.greatest(size, 1),
        ).where(Meeting.created_at >= since)
    ).all()
    return [MeetingWithId(r[0], MeetingRow(r[1], r[2], r[3], r[4], int(r[5]))) for r in rows]


def fetch_runs(session: Session, since: datetime) -> list[RunRow]:
    rows = session.execute(
        select(
            AiRun.meeting_id,
            AiRun.user_id,
            AiRun.kind,
            AiRun.model,
            AiRun.input_tokens,
            AiRun.output_tokens,
            AiRun.audio_seconds,
            AiRun.status,
        ).where(AiRun.created_at >= since)
    ).all()
    return [RunRow(*r) for r in rows]


def fetch_usage_seconds(session: Session, since: datetime) -> tuple[int, int]:
    """`(entries, seconds)` counted against hours in the window (survives deleted meetings)."""
    count, seconds = session.execute(
        select(func.count(), func.coalesce(func.sum(UsageEntry.seconds), 0)).where(
            UsageEntry.counted_at >= since
        )
    ).one()
    return int(count), int(seconds)


# ---- Arithmetic (pure: the tests call these) ----


def hours(seconds: float) -> float:
    return seconds / 3600


def price_run(run: RunRow) -> tuple[float, float] | None:
    """`(low, high)` dollars for one ledger row, or None when its model has no price here and it
    did use tokens or audio. A row with no tokens and no audio costs nothing."""
    if run.audio_seconds:
        prices = OPENAI_AUDIO_PER_MINUTE.get(run.model)
        if prices is None:
            return None
        minutes = run.audio_seconds / 60
        return minutes * prices[0], minutes * prices[1]
    if run.input_tokens or run.output_tokens:
        prices = OPENAI_TOKEN_PRICES.get(run.model)
        if prices is None:
            return None
        cost = (run.input_tokens or 0) * prices[0] / 1e6 + (run.output_tokens or 0) * prices[
            1
        ] / 1e6
        return cost, cost
    return 0.0, 0.0


@dataclass
class Spend:
    calls: int = 0
    failed: int = 0
    unpriced: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    audio_seconds: int = 0
    low: float = 0.0
    high: float = 0.0


def ai_spend(runs: list[RunRow]) -> tuple[Spend, dict[tuple[str, str], Spend]]:
    """The total and the split by `(kind, model)`. Failed rows count: their tokens were paid for."""
    total = Spend()
    split: dict[tuple[str, str], Spend] = defaultdict(Spend)
    for run in runs:
        price = price_run(run)
        for bucket in (total, split[(run.kind, run.model)]):
            bucket.calls += 1
            bucket.failed += run.status == "failed"
            bucket.input_tokens += run.input_tokens or 0
            bucket.output_tokens += run.output_tokens or 0
            bucket.audio_seconds += run.audio_seconds or 0
            if price is None:
                bucket.unpriced += 1
            else:
                bucket.low += price[0]
                bucket.high += price[1]
    return total, dict(split)


def recorded_seconds(meetings: list[MeetingRow]) -> int:
    return sum(m.recorded_seconds or 0 for m in meetings)


@dataclass
class BotEstimate:
    skribby_hours: float
    elevenlabs_hours: float
    old_model_hours: float
    bot_low: float
    bot_high: float
    model_cost: float | None  # None when ElevenLabs' price is unknown
    elevenlabs_known: bool


def bot_estimate(meetings: list[MeetingRow]) -> BotEstimate:
    """Skribby bot hours and its transcription model line, from recorded hours of the meetings the
    Skribby bot attended (a Vexa-era meeting costs no vendor money)."""
    skribby = [m for m in meetings if m.bot_provider == "skribby"]
    new = hours(recorded_seconds([m for m in skribby if m.created_at >= ELEVENLABS_SINCE]))
    old = hours(recorded_seconds([m for m in skribby if m.created_at < ELEVENLABS_SINCE]))
    all_hours = new + old
    known = ELEVENLABS_PER_HOUR is not None
    model = old * SKRIBBY_OLD_MODEL_PER_HOUR + (new * ELEVENLABS_PER_HOUR if known else 0.0)
    return BotEstimate(
        skribby_hours=all_hours,
        elevenlabs_hours=new,
        old_model_hours=old,
        bot_low=all_hours * SKRIBBY_BOT_PER_HOUR * BOT_TIME_FACTOR[0],
        bot_high=all_hours * SKRIBBY_BOT_PER_HOUR * BOT_TIME_FACTOR[1],
        model_cost=model if known else None,
        elevenlabs_known=known,
    )


@dataclass
class Cost:
    hours: float
    ai_low: float
    ai_high: float
    unpriced: int
    bot: BotEstimate

    @property
    def complete(self) -> bool:
        """False when a part is missing (an unpriced model, an unknown ElevenLabs price): the
        totals are then lower bounds only."""
        return self.unpriced == 0 and self.bot.elevenlabs_known

    @property
    def total_low(self) -> float:
        return self.ai_low + self.bot.bot_low + (self.bot.model_cost or 0.0)

    @property
    def total_high(self) -> float:
        return self.ai_high + self.bot.bot_high + (self.bot.model_cost or 0.0)

    def per_hour(self, value: float) -> float | None:
        return value / self.hours if self.hours > 0 else None


def cost(meetings: list[MeetingRow], runs: list[RunRow]) -> Cost:
    total, _ = ai_spend(runs)
    return Cost(
        hours=hours(recorded_seconds(meetings)),
        ai_low=total.low,
        ai_high=total.high,
        unpriced=total.unpriced,
        bot=bot_estimate(meetings),
    )


def bucket_label(team_size: int) -> str:
    for upper, label in SIZE_BUCKETS:
        if team_size <= upper:
            return label
    return SIZE_BUCKETS[-1][1]


def long_meetings(meetings: list[MeetingRow]) -> tuple[int, int]:
    """`(how many, their seconds)` for recordings over LONG_SECONDS."""
    long = [m.recorded_seconds for m in meetings if (m.recorded_seconds or 0) > LONG_SECONDS]
    return len(long), sum(long)


def without_long(
    meetings: list[MeetingWithId], runs: list[RunRow]
) -> tuple[list[MeetingRow], list[RunRow]]:
    """The meetings that are not over LONG_SECONDS, and the runs left once those meetings' own
    runs are taken out (runs with no meeting, or a meeting outside the window, stay)."""
    long_ids = {m.id for m in meetings if (m.row.recorded_seconds or 0) > LONG_SECONDS}
    kept = [m.row for m in meetings if m.id not in long_ids]
    return kept, [r for r in runs if r.meeting_id not in long_ids]


def only_provider(
    meetings: list[MeetingWithId], runs: list[RunRow], provider: str
) -> tuple[list[MeetingRow], list[RunRow]]:
    """The meetings a given bot provider attended, and the runs tied to those meetings: the cost
    of one hour of the current setup, without the older Vexa hours that cost no vendor money."""
    ids = {m.id for m in meetings if m.row.bot_provider == provider}
    return [m.row for m in meetings if m.id in ids], [r for r in runs if r.meeting_id in ids]


def split_by_user(
    meetings: list[MeetingWithId], runs: list[RunRow]
) -> dict[int, tuple[list[MeetingRow], list[RunRow]]]:
    """Meetings by owner; a run goes to `run.user_id`, else to its meeting's owner, else nowhere."""
    owner_of = {m.id: m.row.owner_user_id for m in meetings}
    groups: dict[int, tuple[list[MeetingRow], list[RunRow]]] = defaultdict(lambda: ([], []))
    for m in meetings:
        groups[m.row.owner_user_id][0].append(m.row)
    for run in runs:
        owner = run.user_id if run.user_id is not None else owner_of.get(run.meeting_id)
        if owner is not None:
            groups[owner][1].append(run)
    return dict(groups)


def split_by_team_size(
    meetings: list[MeetingWithId], runs: list[RunRow]
) -> dict[str, tuple[list[MeetingRow], list[RunRow]]]:
    """Meetings by the size bucket of the team they belong to; a run goes with its meeting's
    bucket (a run with no meeting in the window belongs to none)."""
    bucket_of = {m.id: bucket_label(m.row.team_size) for m in meetings}
    groups: dict[str, tuple[list[MeetingRow], list[RunRow]]] = defaultdict(lambda: ([], []))
    for m in meetings:
        groups[bucket_of[m.id]][0].append(m.row)
    for run in runs:
        label = bucket_of.get(run.meeting_id)
        if label is not None:
            groups[label][1].append(run)
    return dict(groups)


# ---- Printing ----


def usd(value: float | None) -> str:
    return "n/a" if value is None else f"${value:,.3f}"


def span(low: float | None, high: float | None) -> str:
    if low is None or high is None:
        return "n/a (no recorded hours)"
    return usd(low) if abs(high - low) < 0.0005 else f"{usd(low)} to {usd(high)}"


def per_hour_span(c: Cost, low: float, high: float) -> str:
    return span(c.per_hour(low), c.per_hour(high))


def render(
    *,
    days: int,
    now: datetime,
    meetings: list[MeetingWithId],
    runs: list[RunRow],
    usage: tuple[int, int],
) -> str:
    rows = [m.row for m in meetings]
    recorded = [m for m in rows if (m.recorded_seconds or 0) > 0]
    total_cost = cost(rows, runs)
    total_spend, split = ai_spend(runs)
    since = now - timedelta(days=days)
    out: list[str] = []
    add = out.append

    add(f"AI cost report, last {days} days ({since:%Y-%m-%d %H:%M} to {now:%Y-%m-%d %H:%M} UTC)")
    add("Read-only. No transcript, title, name or email is read or printed.")

    add("")
    add("== 1. Meeting hours ==")
    add(f"  Meetings created in the window: {len(rows)}")
    add(f"  Meetings with a recording:      {len(recorded)}")
    add(
        f"  Recorded hours: {total_cost.hours:.2f}"
        "   (column used: meetings.recorded_seconds, length of the prepared recording)"
    )
    entries, seconds = usage
    add(
        f"  Cross-check, usage_entries.seconds counted in the window: {hours(seconds):.2f} h "
        f"in {entries} entries (also counts meetings deleted since)"
    )

    add("")
    add("== 2. AI spend from the ai_runs table (priced here from tokens and audio seconds) ==")
    add("  ai_runs has no cost column: cost = tokens or audio x the price constants in this file.")
    add(
        f"  Rows: {total_spend.calls} ({total_spend.failed} failed, still counted: tokens were "
        f"paid), UNPRICED rows (model not in the table): {total_spend.unpriced}"
    )
    add(
        f"  Total: {span(total_spend.low, total_spend.high)}"
        f"   per recorded hour: {per_hour_span(total_cost, total_spend.low, total_spend.high)}"
    )
    add("  By kind and model:")
    add(
        f"    {'kind':<11}{'model':<16}{'calls':>6}{'tokens in':>11}{'tokens out':>11}"
        f"{'audio min':>10}  cost"
    )
    for (kind, model), s in sorted(split.items(), key=lambda kv: -kv[1].high):
        note = f"  ({s.unpriced} unpriced)" if s.unpriced else ""
        add(
            f"    {kind:<11}{model:<16}{s.calls:>6}{s.input_tokens:>11}{s.output_tokens:>11}"
            f"{s.audio_seconds / 60:>10.1f}  {span(s.low, s.high)}{note}"
        )
    add(
        "  Rows with no meeting (Ask Beyric questions, deleted meetings) are in the total but "
        "not tied to hours."
    )

    b = total_cost.bot
    add("")
    add("== 3. ESTIMATE, not in the database (constants at the top of this file) ==")
    add(
        f"  Skribby bot meetings: {b.skribby_hours:.2f} recorded hours "
        f"(Vexa-era meetings cost no vendor money)."
    )
    add(
        f"  Skribby bot time: {span(b.bot_low, b.bot_high)}   "
        f"(${SKRIBBY_BOT_PER_HOUR}/h x {BOT_TIME_FACTOR[0]} to {BOT_TIME_FACTOR[1]} billed-hours "
        f"factor; the factor is a judgement, check the Skribby invoice)"
    )
    add(
        f"  Skribby model before the ElevenLabs flip: {b.old_model_hours:.2f} h x "
        f"${SKRIBBY_OLD_MODEL_PER_HOUR:.2f}/h"
    )
    if b.elevenlabs_known:
        add(
            f"  ElevenLabs scribe-v2 (billed through Skribby, not an extra charge): "
            f"{b.elevenlabs_hours:.2f} h x ${ELEVENLABS_PER_HOUR:.2f}/h  "
            f"(price from elevenlabs.io/pricing/api, read 2026-11-14)"
        )
    else:
        add(
            f"  ElevenLabs scribe-v2: {b.elevenlabs_hours:.2f} h, price unknown "
            f"(constant is None): totals below are lower bounds"
        )
    add(f"  Skribby model line: {usd(b.model_cost) if b.model_cost is not None else 'unknown'}")

    add("")
    add("== 4. Total cost per recorded hour (AI from the database + estimate above) ==")
    add(
        f"  Total: {span(total_cost.total_low, total_cost.total_high)}"
        f"   per hour: {per_hour_span(total_cost, total_cost.total_low, total_cost.total_high)}"
    )
    if not total_cost.complete:
        add("  INCOMPLETE: an unpriced model or an unknown price is left out, so this is a floor.")
    skribby_meetings, skribby_runs = only_provider(meetings, runs, "skribby")
    sk = cost(skribby_meetings, skribby_runs)
    add(
        f"  Skribby-bot meetings only ({sk.hours:.2f} of the {total_cost.hours:.2f} h; the rest "
        f"cost no vendor money), AI of those meetings + estimate: "
        f"{per_hour_span(sk, sk.total_low, sk.total_high)} per hour"
    )
    add("  Same split, counts and dollars only:")
    users = split_by_user(meetings, runs)
    ranked = sorted(users.values(), key=lambda g: -recorded_seconds(g[0]))
    add(f"    Accounts with a meeting or an AI call: {len(users)}")
    for number, (u_meetings, u_runs) in enumerate(ranked, 1):
        c = cost(u_meetings, u_runs)
        add(
            f"    user {number}: {len(u_meetings)} meeting(s), {c.hours:.2f} h, "
            f"per hour {per_hour_span(c, c.total_low, c.total_high)}"
        )
    add("    By team size (members of the team the meeting belongs to):")
    sizes = split_by_team_size(meetings, runs)
    for _, label in SIZE_BUCKETS:
        if label not in sizes:
            continue
        s_meetings, s_runs = sizes[label]
        c = cost(s_meetings, s_runs)
        add(
            f"      team of {label}: {len(s_meetings)} meeting(s), {c.hours:.2f} h, "
            f"per hour {per_hour_span(c, c.total_low, c.total_high)}"
        )

    add("")
    add(f"== 5. Unusually long recordings (over {LONG_SECONDS // 3600} hours) ==")
    count, long_seconds = long_meetings(rows)
    add(f"  {count} meeting(s), {hours(long_seconds):.2f} h of the {total_cost.hours:.2f} h total")
    if count:
        without = cost(*without_long(meetings, runs))
        add(
            "  A runaway bot would distort the average. Without them: "
            f"{per_hour_span(without, without.total_low, without.total_high)} per hour "
            f"over {without.hours:.2f} h."
        )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=30, help="how far back to look (default 30)")
    args = parser.parse_args(argv)
    if args.days < 1:
        parser.error("--days must be at least 1")

    from app.db import SessionLocal

    now = datetime.now(UTC)
    since = now - timedelta(days=args.days)
    with SessionLocal() as session:
        open_read_only(session)
        meetings = fetch_meetings(session, since)
        runs = fetch_runs(session, since)
        usage = fetch_usage_seconds(session, since)
        session.rollback()
    print(render(days=args.days, now=now, meetings=meetings, runs=runs, usage=usage))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
