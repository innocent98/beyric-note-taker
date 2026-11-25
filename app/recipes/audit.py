"""The number audit and the blanks of a draft: code, no model, advisory (spec 5.4, points 4 and 5).

`audit` finds every figure in the draft (digits, currency amounts, percentages, dates, times, and
English number words) and checks it appears in the corpus: the transcript, the notes, the tasks and
the brief. A figure that does not is a `Flag`: kind `derived` when it looks like a sum, a
difference, a product or a quotient of figures that are in the corpus, or a length of time in
another unit (two weeks from 14 days); kind `figure` otherwise. It over-flags on purpose, so a
person looks; it never blocks. The audit runs on whatever text the draft holds when it is asked
(after a person's edit too).

A figure is written as a set of keys (`normalise_figures`), so "50k", "50,000" and "fifty thousand"
are the same figure: `v:<value>` always, `n:<value>` with no currency, `c:<CUR>:<value>` with one,
`p:<value>` for a percentage, `d:<date>` and `t:<time>`. Number words and month names are English
only; digits and currency symbols work in any language, so a draft in another language is still
audited on its digits. Line numbers (`line 12`, `[L12]`), list ordinals (`1.`, `2nd`), version-like
tokens (`v2.1`, `A4`), bare years and the numbering column of a table are not figures; a blank
(`[need: ...]`) is not read for figures.
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from itertools import combinations
from typing import Literal

from app.recipes.blocks import Block, Table, texts

BLANK = re.compile(r"\[\s*need\s*:\s*([^\]]*)\]", re.IGNORECASE)

_UNITS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
_SCALES = {"thousand": 1000, "million": 10**6, "billion": 10**9}
_WORDS = [*_UNITS, *_TENS, "hundred", *_SCALES]
_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_DAYS_IN = {"day": 1, "week": 7, "month": 30, "year": 365}
_CURRENCY_SYMBOL = {"₦": "NGN", "n": "NGN", "ngn": "NGN", "$": "USD", "us$": "USD", "usd": "USD"}
_CURRENCY_SYMBOL |= {"€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP"}
_CURRENCY_WORD = {
    "naira": "NGN",
    "ngn": "NGN",
    "dollar": "USD",
    "dollars": "USD",
    "usd": "USD",
    "euro": "EUR",
    "euros": "EUR",
    "eur": "EUR",
    "pound": "GBP",
    "pounds": "GBP",
    "gbp": "GBP",
}

_MONTH = "(?:" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?"
_ORD = r"(?:st|nd|rd|th)?"
_IGNORED = re.compile(
    r"""(?ix)
    \blines?\s+\d+(?:\s*(?:to|-|–|and)\s*\d+)?
    | \[L\d+\]
    | ^\s*\d+[.)]\s
    """,
    re.MULTILINE,
)
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_SLASH = re.compile(r"(?<![\w.])(\d{1,2})[/.-](\d{1,2})[/.-](\d{4}|\d{2})(?!\w|\.\d)")
_DAY_MONTH = re.compile(rf"(?i)\b(\d{{1,2}}){_ORD}(?:\s+of)?\s+({_MONTH})(?:\s*,?\s*(\d{{4}}))?\b")
_MONTH_DAY = re.compile(rf"(?i)\b({_MONTH})\s+(\d{{1,2}}){_ORD}\b(?:\s*,?\s*(\d{{4}})\b)?")
_TIME = re.compile(
    r"(?i)(?<![\w.:])(\d{1,2})(?::(\d{2}))?\s?([ap])(?:m\b|\.m\b\.?)|(?<![\w.:])(\d{1,2}):(\d{2})(?![\w:])"
)
_CUR_PREFIX = r"(?:(?<![A-Za-z])(?:US\$|NGN|USD|EUR|GBP)\s?|[₦$€£]\s?|(?<![A-Za-z])N(?=\d))"
_NUMBER = re.compile(
    rf"""(?ix)
    (?:(?P<cur>{_CUR_PREFIX})|(?<![\w.,]))
    (?P<num>(?:\d{{1,3}}(?:,\d{{3}})+|\d+)(?:\.\d+)?)
    (?!\d*(?:st|nd|rd|th)\b)
    (?P<scale>\s?(?:k|m|bn)\b|[\s-]?(?:thousand|million|billion)\b)?
    (?P<pct>\s?%|\s+percent\b)?
    (?:\s+(?P<word>naira|ngn|usd|dollars?|euros?|eur|pounds?|gbp|(?:day|week|month|year)s?))?
    """
)
_WORD_RUN = re.compile(
    r"(?i)\b(?:{w})(?:(?:[\s-]+|\s+and\s+)(?:{w}))*\b(?:\s+(?P<word>naira|dollars?|euros?|pounds?"
    r"|(?:day|week|month|year)s?))?".format(w="|".join(sorted(_WORDS, key=len, reverse=True)))
)
_ORDINAL_HEADINGS = {"#", "no", "no.", "s/n", "sn", "nr", "item no", "item no."}


@dataclass(frozen=True)
class Flag:
    block_index: int
    text: str
    kind: Literal["figure", "derived"]


@dataclass(frozen=True)
class Blank:
    block_index: int
    what: str


@dataclass(frozen=True)
class _Figure:
    text: str
    keys: frozenset[str]  # what it is, written for a corpus
    needs: tuple[str, ...]  # any one of these in the corpus supports it
    value: Decimal | None = None
    days: Decimal | None = None  # a length of time in days
    plain_value: bool = False  # a plain number: derived rules apply


def _dec(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None


def _fmt(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _scale(word: str | None) -> int:
    word = (word or "").strip(" -").lower()
    return {"k": 1000, "m": 10**6, "bn": 10**9, "thousand": 1000, "million": 10**6}.get(
        word, 10**9 if word == "billion" else 1
    )


def _words_value(run: str) -> int | None:
    total = current = 0
    for word in re.findall(r"[A-Za-z]+", run.lower()):
        if word == "and":
            continue
        if word in _UNITS:
            current += _UNITS[word]
        elif word in _TENS:
            current += _TENS[word]
        elif word == "hundred":
            current = max(current, 1) * 100
        elif word in _SCALES:
            total += max(current, 1) * _SCALES[word]
            current = 0
        else:
            return None
    return total + current


def _value_figure(text: str, value: Decimal, *, currency=None, unit=None, pct=False) -> _Figure:
    v = _fmt(value)
    keys = {f"v:{v}"}
    if pct:
        keys.add(f"p:{v}")
    if currency:
        keys.add(f"c:{currency}:{v}")
        needs = (f"c:{currency}:{v}", f"n:{v}")
    else:
        keys.add(f"n:{v}")
        needs = (f"v:{v}",)
    days = value * _DAYS_IN[unit] if unit in _DAYS_IN else None
    return _Figure(text, frozenset(keys), needs, value, days, plain_value=not (currency or pct))


def _date_figure(text: str, month: int, day: int, year: int | None, alt=None) -> list[_Figure]:
    """A date, or both readings of an ambiguous one (`alt` is the other (month, day))."""
    out = []
    for m, d in [(month, day)] + ([alt] if alt else []):
        try:
            date(year or 2000, m, d)
        except ValueError:
            continue
        md = f"--{m:02d}-{d:02d}"
        if year:
            keys = frozenset({f"d:{year:04d}-{m:02d}-{d:02d}", f"dy:{md}"})
            needs = (f"d:{year:04d}-{m:02d}-{d:02d}", f"d:{md}")
        else:
            keys = frozenset({f"d:{md}"})
            needs = (f"d:{md}", f"dy:{md}")
        out.append(_Figure(text, keys, needs))
    return out


def _merge_readings(figures: list[_Figure]) -> _Figure | None:
    """One ambiguous date as one figure: supported when any reading is."""
    if not figures:
        return None
    keys = frozenset().union(*(f.keys for f in figures))
    needs = tuple(n for f in figures for n in f.needs)
    return _Figure(figures[0].text, keys, needs)


def _year(raw: str | None) -> int | None:
    if not raw:
        return None
    year = int(raw)
    return year + 2000 if year < 100 else year


def _blank_out(text: str, span: tuple[int, int]) -> str:
    return text[: span[0]] + " " * (span[1] - span[0]) + text[span[1] :]


def _figures(text: str) -> list[_Figure]:
    """Every figure in `text`, in order of appearance."""
    text = BLANK.sub(lambda m: " " * len(m.group(0)), text)
    text = _IGNORED.sub(lambda m: " " * len(m.group(0)), text)
    found: list[tuple[int, _Figure]] = []

    def take(pattern, build):
        nonlocal text
        for match in list(pattern.finditer(text)):
            figure = build(match)
            if figure is not None:
                found.append((match.start(), figure))
                text = _blank_out(text, match.span())

    take(
        _ISO,
        lambda m: _merge_readings(
            _date_figure(m.group(0), int(m.group(2)), int(m.group(3)), int(m.group(1)))
        ),
    )

    def slash(m):
        a, b, year = int(m.group(1)), int(m.group(2)), _year(m.group(3))
        if a > 12:
            readings = _date_figure(m.group(0), b, a, year)
        elif b > 12:
            readings = _date_figure(m.group(0), a, b, year)
        else:
            readings = _date_figure(m.group(0), a, b, year, alt=(b, a))
        return _merge_readings(readings)

    take(_SLASH, slash)
    take(
        _DAY_MONTH,
        lambda m: _merge_readings(
            _date_figure(
                m.group(0),
                _MONTHS[m.group(2).lower().rstrip(".")],
                int(m.group(1)),
                _year(m.group(3)),
            )
        ),
    )
    take(
        _MONTH_DAY,
        lambda m: _merge_readings(
            _date_figure(
                m.group(0),
                _MONTHS[m.group(1).lower().rstrip(".")],
                int(m.group(2)),
                _year(m.group(3)),
            )
        ),
    )

    def clock(m):
        if m.group(3):
            hour, minute, half = int(m.group(1)), int(m.group(2) or 0), m.group(3).lower()
            hour = hour % 12 + (12 if half == "p" else 0)
        else:
            hour, minute = int(m.group(4)), int(m.group(5))
        if hour > 23 or minute > 59:
            return None
        key = f"t:{hour:02d}:{minute:02d}"
        return _Figure(m.group(0).strip(), frozenset({key}), (key,))

    take(_TIME, clock)

    def number(m):
        value = _dec(m.group("num"))
        if value is None:
            return None
        value *= _scale(m.group("scale"))
        word = (m.group("word") or "").lower()
        currency = None
        if m.group("cur"):
            currency = _CURRENCY_SYMBOL.get(m.group("cur").strip().lower())
        elif word in _CURRENCY_WORD:
            currency = _CURRENCY_WORD[word]
        unit = word.rstrip("s") if word.rstrip("s") in _DAYS_IN else None
        if 1900 <= value <= 2100 and not (currency or unit or m.group("scale") or m.group("pct")):
            return None  # a bare year
        return _value_figure(
            m.group(0).strip(), value, currency=currency, unit=unit, pct=bool(m.group("pct"))
        )

    take(_NUMBER, number)

    def words(m):
        run = m.group(0)
        if m.group("word"):
            run = run[: len(run) - len(m.group("word"))].rstrip()
        value = _words_value(run)
        if value is None:
            return None
        word = (m.group("word") or "").lower()
        currency = _CURRENCY_WORD.get(word)
        unit = word.rstrip("s") if word.rstrip("s") in _DAYS_IN else None
        if value < 10 and not (currency or unit):
            return None  # "one of the options"
        return _value_figure(m.group(0).strip(), Decimal(value), currency=currency, unit=unit)

    take(_WORD_RUN, words)
    return [figure for _, figure in sorted(found, key=lambda pair: pair[0])]


def normalise_figures(text: str) -> set[str]:
    """The keys of every figure in `text`: the same figure written two ways gives the same keys."""
    return {key for figure in _figures(text) for key in figure.keys}


def _corpus(corpus: list[str]) -> tuple[set[str], list[Decimal], set[Decimal]]:
    keys: set[str] = set()
    values: list[Decimal] = []
    days: set[Decimal] = set()
    for text in corpus:
        for figure in _figures(text):
            keys |= figure.keys
            if figure.value is not None:
                values.append(figure.value)
            if figure.days is not None:
                days.add(figure.days)
    return keys, values, days


def _combinations(values: list[Decimal]) -> set[Decimal]:
    """What two or three figures of the meeting make together, or one makes of another."""
    distinct = sorted(set(values))[:60]
    out: set[Decimal] = set()
    for a in values[:80]:
        for b in values[:80]:
            out |= {a + b, abs(a - b), a * b}
            if b:
                out.add(a / b)
    for trio in combinations(distinct[:40], 3):
        out.add(sum(trio))
    return out


def _derived(figure: _Figure, values: list[Decimal], days: set[Decimal], cache: list) -> bool:
    if figure.days is not None and figure.days in days:
        return True
    if figure.value is None:
        return False
    if not cache:
        cache.append(_combinations(values))
    return any(abs(figure.value - c) < Decimal("0.005") for c in cache[0])


def audit(blocks: list[Block], corpus: list[str]) -> list[Flag]:
    """The figures in `blocks` the corpus does not support, one flag per figure per block."""
    keys, values, days = _corpus(corpus)
    cache: list = []
    flags: list[Flag] = []
    seen: set[tuple[int, str]] = set()
    for index, text, ordinal in _pieces(blocks):
        for figure in _figures(text):
            if ordinal and figure.plain_value and figure.value == int(figure.value or 0):
                continue
            if any(need in keys for need in figure.needs) or (index, figure.text) in seen:
                continue
            seen.add((index, figure.text))
            kind = "derived" if _derived(figure, values, days, cache) else "figure"
            flags.append(Flag(index, figure.text, kind))
    return flags


def _pieces(blocks: list[Block]):
    """(block index, text, is a numbering cell): a table's first column headed "#" is only
    counting rows."""
    for index, block in enumerate(blocks):
        if isinstance(block, Table):
            numbered = bool(block.columns) and block.columns[0].strip().lower() in _ORDINAL_HEADINGS
            for heading in block.columns:
                yield index, heading, False
            for row in block.rows:
                for position, cell in enumerate(row):
                    yield index, cell.text, numbered and position == 0
        else:
            for _, text in (t for t in texts([block])):
                yield index, text, False


def blanks(blocks: list[Block]) -> list[Blank]:
    """Every `[need: ...]` token in the draft, in order, with the block it is in."""
    return [
        Blank(index, match.group(1).strip())
        for index, text in texts(blocks)
        for match in BLANK.finditer(text)
    ]
