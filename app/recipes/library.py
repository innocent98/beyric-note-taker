"""The eighteen starter recipes, as code constants (spec 5.3 and 5.3a), and the rules for the
choices a recipe offers the person who makes a draft.

A starter is not a database row: teams and people COPY one into a `Recipe` row (`key` remembers
which) and edit the copy. `STARTERS` is read by `app/recipes/service.py` (`starter_for`) when a
draft names a starter, and by `app/api_recipe_library.py` for the list.

Every instruction is a prompt for `app/recipes/generate.py`, which already tells the model the
truthfulness rules, the block types and that meeting text is data. The instruction adds what this
document is, and ends with the same grounding sentence (`GROUND`): use only the material, never
invent, write a `[need: ...]` blank for a missing fact. Code finds those blanks and the number audit
checks the figures (`app/recipes/audit.py`); the words here are the first line of defence, not the
only one.

`options` are the choices the Make dialog draws for the recipe (tone, recipient, ...): a dict of
`key -> {"label", "kind": "text" | "choice", "choices", "default"}`. What the person picks is sent
as `options` of `POST /api/documents` and reaches the model as data, so an instruction names a
choice by its key ("the `tone` choice") and says what to do when it is missing.
"""

import re
from dataclasses import dataclass, field

MAX_INSTRUCTION_CHARS = 4000
MAX_NAME_CHARS = 100
MAX_RECIPES = 100  # live recipes one person (or one team) keeps; archived ones do not count
MAX_OPTIONS = 6
MAX_LABEL_CHARS = 60
MAX_CHOICES = 6
MAX_CHOICE_CHARS = 40
OPTION_KINDS = ("text", "choice")
_OPTION_KEY = re.compile(r"^[a-z][a-z0-9_]{0,23}$")

GROUND = (
    "Use only the meeting material you are given (the transcript, notes, decisions, tasks and "
    "open questions) and the team brief when one is given. Never invent a name, number, date, "
    "price, commitment or answer. Where a fact you need is not there, write a blank such as "
    "`[need: <what is missing>]` instead of guessing."
)

_TONE = {
    "label": "Tone",
    "kind": "choice",
    "choices": ["Friendly", "Formal"],
    "default": "Friendly",
}
_RECIPIENT = {"label": "Recipient or client name", "kind": "text"}


@dataclass(frozen=True)
class Starter:
    key: str
    pack: str  # "general" or "school"
    name: str
    instruction: str
    output_type: str  # document, email, table or board
    scope: str = "one"  # "one" meeting, or "many" (cross-meeting: notes, tasks, decisions only)
    uses_brief: bool = True
    options: dict = field(default_factory=dict)


def clean_options(raw: object) -> dict:
    """The choice definitions of a recipe, checked and tidied; `ValueError` for anything else.
    Idempotent: cleaning a clean value changes nothing."""
    if not isinstance(raw, dict):
        raise ValueError("options must be an object")
    if len(raw) > MAX_OPTIONS:
        raise ValueError(f"at most {MAX_OPTIONS} choices")
    clean: dict[str, dict] = {}
    for key, item in raw.items():
        if not isinstance(key, str) or not _OPTION_KEY.match(key):
            raise ValueError("a choice key is a short lowercase word")
        if not isinstance(item, dict):
            raise ValueError("a choice is an object")
        label = str(item.get("label", "")).strip()
        kind = item.get("kind")
        if not label or len(label) > MAX_LABEL_CHARS:
            raise ValueError("a choice needs a short label")
        if kind not in OPTION_KINDS:
            raise ValueError("a choice is text or choice")
        if kind == "text":
            clean[key] = {"label": label, "kind": "text"}
            continue
        choices = item.get("choices")
        if (
            not isinstance(choices, list)
            or not 1 <= len(choices) <= MAX_CHOICES
            or not all(
                isinstance(c, str) and 0 < len(c.strip()) <= MAX_CHOICE_CHARS for c in choices
            )
        ):
            raise ValueError("a choice offers between one and six short words")
        values = [c.strip() for c in choices]
        if len(set(values)) != len(values):
            raise ValueError("choices must differ")
        default = item.get("default")
        if default is None:
            default = values[0]
        if default not in values:
            raise ValueError("the default is one of the choices")
        clean[key] = {"label": label, "kind": "choice", "choices": values, "default": default}
    return clean


def _starter(key: str, pack: str, name: str, instruction: str, output_type: str, **kw) -> Starter:
    return Starter(
        key=key,
        pack=pack,
        name=name,
        instruction=f"{instruction}\n\n{GROUND}",
        output_type=output_type,
        **kw,
    )


_GENERAL = (
    _starter(
        "follow_up_email",
        "general",
        "Follow-up email",
        "Write the follow-up email the requester can send after this meeting. The first paragraph "
        "is `Subject: ` and a short, specific subject. Then a greeting to the person in the "
        "`recipient` choice (write `[need: recipient name]` if none was given), a line of thanks, "
        "a short recap of what was discussed, and a list of what each side agreed, each item with "
        "its owner, taken from the action items and decisions. Give a next step and a date only "
        "if the meeting set them; otherwise write `[need: next step date]`. Match the `tone` "
        "choice (friendly or formal). Never promise a price, a delivery date, a discount or a term "
        "that is not in the meeting or the brief.",
        "email",
        options={"recipient": _RECIPIENT, "tone": _TONE},
    ),
    _starter(
        "proposal",
        "general",
        "Proposal",
        "Write a client proposal from this meeting and the brief, with these headings in order: "
        "Background; What we heard; Proposed solution; Scope and deliverables; Timeline; "
        "Investment; Terms and assumptions; Next steps. Timeline is a table (columns: Phase, What "
        "happens, When). Investment is a table (columns: Item, Price). What we heard comes from "
        "the transcript and notes. Prices, rates and terms come only from the brief or from what "
        "was said in the meeting; where neither states one, write a blank such as `[need: price "
        "for <item>]`. Name the client from the `recipient` choice, or `[need: client name]`. "
        "Match the `tone` choice.",
        "document",
        options={"recipient": _RECIPIENT, "tone": _TONE},
    ),
    _starter(
        "requirements",
        "general",
        "Requirements document",
        "Write a requirements document from this meeting with these headings in order: Goals; "
        "Users; Requirements; Open questions; Out of scope; Risks; Acceptance. Requirements is a "
        "table (columns: Requirement, Priority, Owner); fill Priority and Owner only when the "
        "meeting said them, else `[need: priority]` or `[need: owner]`. Open questions come from "
        "the open questions and from anything left undecided. Acceptance lists how the team said "
        "it will know the work is done, or `[need: acceptance criteria]`.",
        "document",
    ),
    _starter(
        "marketing_post",
        "general",
        "Marketing post",
        "Write a post (a LinkedIn post or a short blog draft) from this product or customer "
        "meeting, for the audience in the `audience` choice (write for a general reader if none "
        "was given) and of the length in the `length` choice. Start with a hook, then the story "
        "or the point, then one call to action. Make no claim, statistic or customer quote that "
        "is not in the meeting or the brief. Name a customer or a person only if the meeting or "
        "the brief clearly allows it; otherwise write `[need: customer name]`.",
        "document",
        options={
            "audience": {"label": "Audience", "kind": "text"},
            "length": {
                "label": "Length",
                "kind": "choice",
                "choices": ["Short", "Medium", "Long"],
                "default": "Short",
            },
        },
    ),
    _starter(
        "decision_memo",
        "general",
        "Decision memo",
        "Write a decision memo built from the decisions recorded for this meeting, with these "
        "headings in order: Context; Options discussed; The decision; Who decided; Consequences; "
        "Follow-ups. Take the decision and who made it from the decisions and the transcript. "
        "List only the options that were actually discussed. Follow-ups come from the action "
        "items, with owners.",
        "document",
        uses_brief=False,
    ),
    _starter(
        "sop",
        "general",
        "SOP / how-to",
        "Turn this walkthrough meeting into a standard operating procedure with these headings in "
        "order: Purpose; Before you start; Steps; Checks; Who does what. Steps is a numbered "
        "sequence in the order they were explained, one action each, naming the owner where the "
        "meeting did. Checks are how someone confirms a step worked, only where the meeting said "
        "so. Do not add steps that were not explained.",
        "document",
        uses_brief=False,
    ),
    _starter(
        "action_tracker",
        "general",
        "Action tracker",
        "Make one table of the meeting's action items from its tasks, with the columns Task, "
        "Owner, Due date and Status, one row per task, in the order of the tasks. Copy the task "
        "wording. Owner, due date and status come only from the task; where one is not shown "
        "write `[need: owner]`, `[need: due date]` or `[need: status]`. Write a due date exactly "
        "as the task gives it.",
        "table",
        uses_brief=False,
    ),
    _starter(
        "board",
        "general",
        "Board",
        "Make a one-page board of this meeting. Group the decisions, action items, open questions "
        "and key points into between three and six themes, one column per theme with a short "
        "heading. Each card is one decision, action, question or note in plain words, with its "
        "owner when the meeting names one, and the line it came from. Do not repeat a card.",
        "board",
        uses_brief=False,
    ),
    _starter(
        "status_report",
        "general",
        "Status report",
        "Write a status report over the meetings you are given, with these headings in order: "
        "Progress; Decisions; Blockers; Next steps. Progress says what was done, taken from the "
        "notes and tasks, by meeting. Decisions is a table (columns: Decision, Meeting). Blockers "
        "and Next steps are bullet lists with owners where known. Say what each meeting "
        "recorded; give no percent complete and no forecast the meetings did not state.",
        "document",
        scope="many",
    ),
    _starter(
        "client_briefing",
        "general",
        "Client briefing",
        "Write a briefing for the next call with the same client, from the earlier meetings you "
        "are given, with these headings in order: History; What we promised; Open items; "
        "Questions to ask. History is short and in date order. What we promised lists only "
        "commitments the meetings record, with who made them. Open items come from the open "
        "tasks and open questions. Questions to ask are things the meetings left unresolved.",
        "document",
        scope="many",
    ),
)

_SCHOOL = (
    _starter(
        "study_guide",
        "school",
        "Study guide",
        "Write a study guide from this class, in the order it was taught, with these headings: "
        "Key ideas; Definitions; Worked examples; What to revise. Give each definition as the "
        "lecturer gave it and each example as it was worked in class. Add nothing from a "
        "textbook or general knowledge. What to revise lists what the lecturer said to focus "
        "on, or the points that took the longest, each with the line it came from.",
        "document",
        uses_brief=False,
    ),
    _starter(
        "flashcards",
        "school",
        "Flashcards",
        "Make flashcards from this class as one table with the columns Front and Back, one idea "
        "per card. The Back is what the class said, in a sentence or two. When the class left "
        "the answer to a front open, write `[need: answer]` on the Back.",
        "table",
        uses_brief=False,
    ),
    _starter(
        "practice_quiz",
        "school",
        "Practice quiz",
        "Write a practice quiz from this class: a mix of multiple choice, true or false and "
        "short answer questions, each followed by `Answer:`. Every answer must come from what "
        "was said in class and cite the line where it was said. If the class never gave the "
        "answer to a question, write `Answer: [need: answer]`. Do not write a question you "
        "could only answer from general knowledge.",
        "document",
        uses_brief=False,
    ),
    _starter(
        "glossary",
        "school",
        "Key terms glossary",
        "Make a glossary of the terms taught in this class as one table with the columns Term, "
        "Meaning and Example. Give the meaning as the lecturer gave it. Give the example from "
        "class, or `[need: example]` when none was given. Order the terms as they were first "
        "taught.",
        "table",
        uses_brief=False,
    ),
    _starter(
        "assignments",
        "school",
        "Assignments and deadlines",
        "List what the lecturer asked the students to do as one table with the columns Task, Due "
        "date and Notes. Write a due date exactly as it was said, for example `next Friday`; "
        "never work out a calendar date yourself. When no due date was said write `[need: due "
        "date]`. Notes holds length, format or marks only when the lecturer stated them.",
        "table",
        uses_brief=False,
    ),
    _starter(
        "revision_plan",
        "school",
        "Revision plan",
        "Write a revision plan over the classes you are given, with these headings: Topics; "
        "Revise first; Day by day. Topics lists the topics across the classes. Revise first "
        "names the topics the lecturer stressed or that came up in more than one class. Day by "
        "day is a table (columns: Day, Topic, What to do) labelled Day 1, Day 2 and so on, "
        "ending on the date in the `until` choice. If the `until` choice is not given, write "
        "`[need: exam date]` and give the order without dates. Never work out a calendar date.",
        "document",
        scope="many",
        uses_brief=False,
        options={"until": {"label": "Revise up to (date)", "kind": "text"}},
    ),
    _starter(
        "class_recap",
        "school",
        "Class recap for students",
        "Write the email a lecturer sends their students after this class. The first paragraph "
        "is `Subject: ` and a short subject. Then what was covered, the key points as bullets, "
        "the homework, and the next class. Give the homework and its date only as the lecturer "
        "said them; otherwise write `[need: homework]` and `[need: due date]`. Give the next "
        "class only if it was said, else `[need: next class]`. Match the `tone` choice.",
        "email",
        uses_brief=False,
        options={"tone": _TONE},
    ),
    _starter(
        "class_faq",
        "school",
        "Class FAQ",
        "Write a FAQ from this class: the questions students asked and the answers the lecturer "
        "gave in class, grouped by topic, each with the line it came from. Add the questions "
        "that were raised and left open; for each, write `[need: answer given in class]` as the "
        "answer. Do not answer a question yourself.",
        "document",
        uses_brief=False,
    ),
)

STARTERS: tuple[Starter, ...] = _GENERAL + _SCHOOL


def starter_by_key(key: str) -> Starter | None:
    return next((s for s in STARTERS if s.key == key), None)
