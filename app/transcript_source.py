"""Where a meeting's transcript comes from: our own OpenAI transcription of the audio (`own`, the
default and the original behaviour) or the bot provider's transcript (`skribby`).

`TRANSCRIPT_SOURCE=skribby` (docs/sop/2026-11-09-transcript-source-switch.md) builds the stored
transcript straight from Skribby's segments, as `[Name]: text` turns in the format
`notes.label_transcript` gives today, and skips our transcription and the model-based labelling. It
never trusts the segments blindly: `fallback_reason` names why ours must run instead (nothing from
Skribby, or far too little text for the length of the recording).

Nothing here logs or stores transcript text. `Candidate` and `Metrics` hold numbers and counts only.
"""

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import VocabularyTerm
from app.notes import UNKNOWN_SPEAKER, _clean_segments

OWN = "own"
SKRIBBY = "skribby"

# A conversation runs at about 120 to 160 words a minute. Fewer than this many words a minute of
# recording is taken as "Skribby's transcript is missing a lot": the recording would have to be
# about 85% silence for a complete transcript to fall under it, which a meeting does not do, and a
# near-empty answer (a failed or cut-off transcription) falls far under it. It does NOT catch a
# transcript that lost only part of the call: the sampled shadow check measures that.
MIN_WORDS_PER_MINUTE = 20

SHADOW_BUCKETS = 100

_WORD = re.compile(r"[a-z0-9']+")


@dataclass(frozen=True)
class Candidate:
    """Skribby's transcript, ready to store. `text` is what `meeting.transcript` would hold."""

    text: str
    spoken: str  # the same words without the "[Name]:" labels, for counting and comparing
    words: int  # of the spoken text only, never the "[Name]:" labels
    speakers: int  # distinct named speakers
    segments: int  # segments that carried words
    unnamed_segments: int  # of those, the ones with no (usable) speaker name
    named: bool  # at least one named speaker: the text carries "[Name]:" turns

    @property
    def complete(self) -> bool:
        """What `label_transcript` calls complete: someone could be named."""
        return self.named


def candidate(segments: list[dict]) -> Candidate | None:
    """Build the transcript from Skribby's segments (`{"speaker", "text"}`), or None when there is
    no text at all.

    A turn is `[Name]: text`, one per line; consecutive segments of one speaker are merged into one
    turn. A segment with no usable name is `[Unknown speaker]`, as `label_transcript` writes it.
    When nobody at all is named the text is plain, one segment a line and no labels, as
    `label_transcript` returns it when it has no one to label with. Names and text go through
    `notes._clean_segments` (control characters, brackets and colons out of names; every run of
    whitespace, line breaks included, collapsed in text), so a participant's name or words can
    never start a forged line of their own."""
    turns = _clean_segments(segments or [])
    if not turns:
        return None
    named = {name for name, _ in turns if name != UNKNOWN_SPEAKER}
    unnamed = sum(1 for name, _ in turns if name == UNKNOWN_SPEAKER)
    words = sum(len(text.split()) for _, text in turns)
    if named:
        merged: list[list[str]] = []
        for name, text in turns:
            if merged and merged[-1][0] == name:
                merged[-1][1] += " " + text
            else:
                merged.append([name, text])
        text = "\n".join(f"[{name}]: {spoken}" for name, spoken in merged)
    else:
        text = "\n".join(spoken for _, spoken in turns)
    return Candidate(
        text=text,
        spoken=" ".join(spoken for _, spoken in turns),
        words=words,
        speakers=len(named),
        segments=len(turns),
        unnamed_segments=unnamed,
        named=bool(named),
    )


def needed_words(seconds: int, floor: int) -> int:
    """The fewest words a recording of `seconds` may have before Skribby's transcript is not used:
    `MIN_WORDS_PER_MINUTE` of audio, and never fewer than `floor` (the pipeline's own no-speech
    floor)."""
    return max(floor, math.ceil(MIN_WORDS_PER_MINUTE * max(seconds, 0) / 60))


def fallback_reason(found: Candidate | None, seconds: int, floor: int) -> str | None:
    """None when Skribby's transcript may be used; else a short fixed word naming why not."""
    if found is None:
        return "no transcript"
    if found.words < needed_words(seconds, floor):
        return "too short for the recording"
    return None


# ---- the sampled shadow check ----


def shadow_bucket(meeting_id: int) -> int:
    """Which of 100 buckets a meeting falls in, fixed by its id alone."""
    digest = hashlib.sha256(f"transcript-shadow:{meeting_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % SHADOW_BUCKETS


def shadow_selected(meeting_id: int, percent: int) -> bool:
    """Whether our own transcription is also run for this meeting, to compare. Deterministic by
    meeting id: 0 is never, 100 is always, in between a fixed share spread across ids."""
    return shadow_bucket(meeting_id) < percent


def _words(text: str) -> list[str]:
    """Words reduced to what they say: lower case, punctuation gone (an apostrophe stays)."""
    return _WORD.findall(text.lower().replace("’", "'"))


@dataclass(frozen=True)
class Overlap:
    skribby_words: int
    own_words: int
    shared_words: int

    @property
    def share(self) -> float:
        """Words in common over the larger count (0 to 1; 1 when both are empty)."""
        larger = max(self.skribby_words, self.own_words)
        return self.shared_words / larger if larger else 1.0


def overlap(skribby_spoken: str, own_text: str) -> Overlap:
    """How many words the two share, each word counted as often as both have it, with their word
    counts. `skribby_spoken` is `Candidate.spoken`. Order is not compared."""
    a, b = _words(skribby_spoken), _words(own_text)
    shared = sum((Counter(a) & Counter(b)).values())
    return Overlap(len(a), len(b), shared)


def send_options(
    session: Session | None, provider, owner_user_id: int, settings: Settings
) -> dict[str, Any]:
    """Extra keyword arguments for `provider.send_bot`: the owner's vocabulary, only in `skribby`
    mode and only for a provider that takes it (Skribby's `custom_vocabulary`), because there it is
    Skribby's model that transcribes. Our own transcription reads the vocabulary itself. Reading
    it can fail without stopping the send: no vocabulary is then sent."""
    if (
        session is None
        or settings.transcript_source != SKRIBBY
        or not getattr(type(provider), "accepts_vocabulary", False)
    ):
        return {}
    try:
        terms = list(
            session.scalars(
                select(VocabularyTerm.term)
                .where(VocabularyTerm.owner_user_id == owner_user_id)
                .order_by(VocabularyTerm.id)
            )
        )
    except Exception:  # a database hiccup must not stop the bot from being sent
        return {}
    return {"vocabulary": terms} if terms else {}
