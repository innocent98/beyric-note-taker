"""Words for matching, not for show: a crude stem so "decide" finds "decided" and "pricing" finds
"price", and the filler words that never identify a topic. Shared by Ask Beyric (`app/ask.py`) and
the repeat topics (`app/topics.py`, `app/series.py`)."""

from app.extraction import normalise

STOP = frozenset(
    """a about after all also am an and any anything are as at be been before but by can could did
    do does done for from get give had has have how i if in into is it its me more most my of on
    or our out say said says she should so some tell than that the their them then there these they
    this those to up us was we were what when where which who whom why will with would you your
    yesterday today tomorrow week weeks monday tuesday wednesday thursday friday saturday sunday
    last next ago meeting meetings summarise summarize summary recap happened made make show
    find list latest recent""".split()
)
WORD_CAP = 12  # question words used


def stem(word: str) -> str:
    """A crude stem so "decide" finds "decided" and "decisions", "pricing" finds "price"."""
    if word.startswith(("decid", "decis")):
        return "deci"
    for suffix in ("ations", "ation", "ings", "ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            break
    if word.endswith("e") and len(word) > 3:
        word = word[:-1]
    return word


def stems(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for word in normalise(text).split():
        if word not in STOP:
            root = stem(word)
            if len(root) >= 2:
                seen[root] = None
    return list(seen)
