"""The real-model half of `scripts/recipes_live.py`: the key, the spend guard, the recording client
and the summary. Pure logic with no network, so `tests/test_recipes_live_real.py` runs without a key.

The rules (set by the lead and Adebayo):

* The key is read ONLY from the process environment (`OPENAI_API_KEY`), refused with a plain message
  when missing, and never printed, logged, written to a file or sent anywhere except the OpenAI
  client. Anything echoed passes through `mask`.
* A hard spend stop: once the estimated cost reaches `RECIPE_LIVE_MAX_USD` (default 3) no further
  model call is made. The estimate uses the REAL token counts of each response and the price table
  the project already has (`scripts/ai_cost_report.py` `OPENAI_TOKEN_PRICES`). A model with no price
  there is never guessed: the run says so and stops after `RECIPE_LIVE_MAX_DRAFTS` drafts (default 5).
* Per draft it records real input and output tokens (from the response's `usage`), wall time, the cost
  estimate, blanks, audit flags, how many source references the model sent that did not survive the
  checks, and the first 300 characters of the draft.
"""

# ruff: noqa: E501

import importlib.util
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

KEY_NAME = "OPENAI_API_KEY"
DEFAULT_MAX_USD = 3.0
DEFAULT_MAX_DRAFTS_UNPRICED = 5
PREVIEW_CHARS = 300
HERE = Path(__file__).resolve().parent


class LiveRealRefused(Exception):
    """A refusal that is safe to print as it is (it never holds the key)."""


class SpendCapReached(Exception):
    """Raised instead of a model call once the cap is reached."""


def require_key(env: dict | None = None) -> str:
    """The key from the environment, or a plain refusal. Nothing else is ever consulted (no `.env`
    file, no argument)."""
    key = ((env if env is not None else os.environ).get(KEY_NAME) or "").strip()
    if not key:
        raise LiveRealRefused(
            f"{KEY_NAME} is not set. Set it for this one command, for example:\n"
            f"    {KEY_NAME}=sk-... PYTHONPATH=. python scripts/recipes_live.py pass ADMIN_URL --real\n"
            "Nothing was started and no call was made."
        )
    return key


def mask(text: object, key: str | None) -> str:
    """`text` as a string with the key (and anything shaped like an OpenAI key) hidden."""
    out = str(text)
    if key:
        out = out.replace(key, "sk-...(hidden)")
    return re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "sk-...(hidden)", out)


def load_prices() -> dict[str, tuple[float, float]]:
    """The project's own price table: USD per million tokens, (input, output)."""
    spec = importlib.util.spec_from_file_location("ai_cost_report", HERE / "ai_cost_report.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return dict(module.OPENAI_TOKEN_PRICES)


def env_float(name: str, default: float, env: dict | None = None) -> float:
    raw = (env if env is not None else os.environ).get(name)
    try:
        value = float(raw) if raw not in (None, "") else default
    except ValueError as exc:
        raise LiveRealRefused(f"{name} must be a number, not {raw!r}.") from exc
    if value <= 0:
        raise LiveRealRefused(f"{name} must be above zero.")
    return value


@dataclass
class SpendGuard:
    """Adds up the estimated cost of real calls and says when to stop."""

    model: str
    max_usd: float = DEFAULT_MAX_USD
    prices: dict = field(default_factory=dict)
    max_drafts_unpriced: int = DEFAULT_MAX_DRAFTS_UNPRICED
    cost: float = 0.0
    drafts: int = 0

    @property
    def priced(self) -> bool:
        return self.model in self.prices

    def price_of(self, input_tokens: int, output_tokens: int) -> float | None:
        if not self.priced:
            return None
        per_in, per_out = self.prices[self.model]
        return input_tokens * per_in / 1e6 + output_tokens * per_out / 1e6

    def add(self, input_tokens: int | None, output_tokens: int | None) -> float | None:
        """Count one finished call; the estimate of that call (None when the model has no price)."""
        self.drafts += 1
        spent = self.price_of(input_tokens or 0, output_tokens or 0)
        if spent is not None:
            self.cost += spent
        return spent

    def reason_to_stop(self) -> str | None:
        """Why no further call may be made, or None."""
        if self.priced and self.cost >= self.max_usd:
            return (
                f"estimated spend ${self.cost:.4f} reached the cap of ${self.max_usd:.2f} "
                "(RECIPE_LIVE_MAX_USD)"
            )
        if not self.priced and self.drafts >= self.max_drafts_unpriced:
            return (
                f"the model {self.model!r} has no price in scripts/ai_cost_report.py, so cost cannot "
                f"be estimated; stopped after {self.drafts} drafts (RECIPE_LIVE_MAX_DRAFTS)"
            )
        return None

    def note(self) -> str:
        if self.priced:
            return (
                f"cap ${self.max_usd:.2f}, prices for {self.model} from scripts/ai_cost_report.py"
            )
        return (
            f"NO PRICE for {self.model!r}: costs below are unknown, and the run stops after "
            f"{self.max_drafts_unpriced} drafts"
        )


class RecordingClient:
    """Wraps the real OpenAI client: refuses a call once the cap is reached, times each call and
    keeps the real `usage` and the raw reply. Quacks like the part of the client the engine uses."""

    def __init__(self, real, guard: SpendGuard, key: str | None = None) -> None:
        self._real = real
        self.guard = guard
        self._key = key
        self.calls: list[dict] = []
        self.chat = type("Chat", (), {"completions": type("C", (), {"create": self._create})()})()

    def _create(self, **kwargs):
        stop = self.guard.reason_to_stop()
        if stop:
            raise SpendCapReached(stop)
        started = time.time()
        try:
            response = self._real.chat.completions.create(**kwargs)
        except Exception as exc:  # the text is masked before anything can show it
            self.calls.append({"error": mask(f"{type(exc).__name__}: {exc}", self._key)})
            raise
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", None)
        output_tokens = getattr(usage, "completion_tokens", None)
        self.guard.add(input_tokens, output_tokens)
        choice = response.choices[0]
        self.calls.append(
            {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "seconds": round(time.time() - started, 2),
                "finish_reason": getattr(choice, "finish_reason", None),
                "raw": getattr(choice.message, "content", None),
            }
        )
        return response


def count_sources(blocks: object) -> int:
    """How many source references a list of blocks (raw reply or stored) holds."""
    total = 0

    def walk(node):
        nonlocal total
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "sources" and isinstance(value, list):
                    total += len(value)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(blocks)
    return total


def sources_dropped(raw_reply: str | None, stored_blocks: list) -> int | None:
    """How many source references the model sent that are not in the stored draft: ids it was never
    shown, duplicates and the cap of six a place. 0 means every reference survived the checks. None
    when the raw reply is not JSON."""
    try:
        sent = count_sources(json.loads(raw_reply or "")["blocks"])
    except (ValueError, KeyError, TypeError):
        return None
    return max(0, sent - count_sources(stored_blocks))


def preview(text: str, limit: int = PREVIEW_CHARS) -> str:
    return " ".join(text.split())[:limit]


def failed_reply_text(key: str, doc: dict, calls: list[dict], secret: str | None) -> str:
    """What to read when a draft failed: the checks' reason and every raw reply the model sent for it
    (the live meetings are synthetic), masked. Never holds the key."""
    parts = [
        f"recipe: {key}",
        f"failure_code: {doc.get('failure_code')}",
        f"failure_detail: {doc.get('failure_detail')}",
        f"failure_message: {doc.get('failure_message')}",
    ]
    for number, call in enumerate(calls, start=1):
        if "error" in call:
            parts.append(f"\n--- call {number}: error ---\n{call['error']}")
            continue
        parts.append(
            f"\n--- call {number}: {call['input_tokens']} in, {call['output_tokens']} out, "
            f"finish_reason {call['finish_reason']} ---\n{call['raw']}"
        )
    return mask("\n".join(parts), secret) + "\n"


def summary_table(rows: list[dict], guard: SpendGuard, stopped: str | None) -> str:
    head = f"{'recipe':18} {'status':7} {'in tok':>7} {'out tok':>7} {'sec':>6} {'USD':>8} {'blanks':>6} {'flags':>5} {'src dropped':>11}"  # noqa: E501
    lines = [head, "-" * len(head)]
    for r in rows:
        usd = "n/a" if r["cost_usd_estimated"] is None else f"{r['cost_usd_estimated']:.5f}"
        dropped = "?" if r["sources_dropped"] is None else str(r["sources_dropped"])
        lines.append(
            f"{r['key']:18} {r['status']:7} {r['input_tokens'] or 0:>7} {r['output_tokens'] or 0:>7} "
            f"{r['seconds']:>6} {usd:>8} {len(r['blanks']):>6} {len(r['flags']):>5} {dropped:>11}"
        )
    lines.append("-" * len(head))
    total = f"${guard.cost:.4f}" if guard.priced else "unknown (no price)"
    lines.append(f"{len(rows)} drafts, estimated spend {total}. {guard.note()}")
    if stopped:
        lines.append(f"STOPPED EARLY: {stopped}")
    return "\n".join(lines)
