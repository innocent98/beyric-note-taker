"""Judge the FULL extraction (`extract-v1`, what the Tasks phase will run) on a real meeting.

It reads a meeting's transcript and notes, makes the same model call the Tasks phase will make,
and writes ONE self-contained HTML page to stdout: the transcript on the left, what the model
extracted on the right, and Right / Wrong / Partly buttons on every item. From the repo root of
the running app (`/srv` in the `web` container):

    python scripts/extraction_review.py --search myway          # find the meeting's id
    python scripts/extraction_review.py --meeting 12 > review.html
    python scripts/extraction_review.py --meeting 12 --meeting 15 > review.html

On the server, from your own machine (`</dev/null` keeps ssh from eating stdin):

    ssh haven-respite 'cd /opt/beyric-note-taker && docker compose exec -T web \\
        python scripts/extraction_review.py --meeting 12 </dev/null' > review.html

Read-only: the database session is a read-only transaction (Postgres refuses any write) that is
rolled back, and the model's answer is never stored, not even in `ai_runs`. The transcript goes
to the model provider once per meeting, exactly as the product already does for every meeting.
The page makes no requests of its own (a Content-Security-Policy blocks them) and loads nothing
from anywhere; everything from the transcript and the model is HTML-escaped.

`--price-in` and `--price-out` (dollars per million tokens) add a cost estimate; the app keeps no
price table, so without them only the token counts are shown.
"""

import argparse
import contextlib
import difflib
import hashlib
import html
import re
import secrets
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import or_, select  # noqa: E402

from app.ai_versions import EXTRACT_PROMPT_VERSION  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.extraction import (  # noqa: E402
    ExtractionCall,
    ExtractionFailed,
    ModelAction,
    ParsedNotes,
    check_extraction,
    due_date_ok,
    extract,
    normalise,
    parse_notes,
    quote_in_transcript,
    title_ok,
)
from app.models import Meeting  # noqa: E402
from app.services import meeting_title  # noqa: E402
from app.workspace import DEFAULT_TIMEZONE, _contains  # noqa: E402

SEARCH_LIMIT = 50
VERY_LONG_CHARS = 200_000
LINE_FUZZY_RATIO = 0.85
_LABEL = re.compile(r"^\[([^\]\n]{1,80})\]:[ \t]*(.*)$")


@contextlib.contextmanager
def open_readonly():
    """A session whose transaction Postgres will not let write, rolled back on the way out."""
    session = SessionLocal()
    try:
        session.connection().exec_driver_sql("SET TRANSACTION READ ONLY")
        yield session
    finally:
        session.rollback()
        session.close()


@dataclass
class MeetingData:
    id: int
    label: str
    code: str
    day: date
    status: str
    transcript: str
    notes: str


def _transcript_of(meeting: Meeting) -> str:
    return meeting.speaker_transcript or meeting.transcript or ""


def _label_of(meeting: Meeting) -> str:
    return meeting.title or meeting_title(meeting)


def _load(meeting: Meeting) -> MeetingData:
    created = meeting.created_at or datetime.now(UTC)
    return MeetingData(
        id=meeting.id,
        label=_label_of(meeting),
        code=meeting.native_id,
        day=created.date(),
        status=meeting.status,
        transcript=_transcript_of(meeting),
        notes=meeting.notes or "",
    )


def search(text: str) -> tuple[list[str], bool]:
    """Table rows for the meetings whose title, code or link contains `text`, newest first."""
    pattern = _contains(text.strip())
    with open_readonly() as session:
        found = session.scalars(
            select(Meeting)
            .where(
                or_(
                    Meeting.title.ilike(pattern, escape="\\"),
                    Meeting.native_id.ilike(pattern, escape="\\"),
                    Meeting.url.ilike(pattern, escape="\\"),
                )
            )
            .order_by(Meeting.id.desc())
            .limit(SEARCH_LIMIT + 1)
        ).all()
        rows = [
            f"{m.id:<8}{(m.created_at or datetime.now(UTC)).date().isoformat():<12}"
            f"{len(_transcript_of(m)):<9}chars  {m.status:<10}{_label_of(m)}  "
            f"[{m.platform} {m.native_id}]"
            for m in found[:SEARCH_LIMIT]
        ]
    return rows, len(found) > SEARCH_LIMIT


# ---- finding the line a quote was said on ----


def _spoken(line: str) -> str:
    labelled = _LABEL.match(line)
    return normalise(labelled.group(2) if labelled else line)


def find_line(quote: str, transcript: str) -> int | None:
    """The 1-based number of the transcript line a quote starts on, or None. A quote that runs
    over two or three lines is found by its first line; a close but inexact one needs 85%."""
    wanted = normalise(quote)
    if not wanted:
        return None
    lines = transcript.split("\n")
    spoken = [_spoken(line) for line in lines]
    for size in (1, 2, 3):
        for start in range(len(lines) - size + 1):
            if f" {wanted} " in f" {' '.join(spoken[start : start + size])} ":
                return next(i for i in range(start, start + size) if spoken[i]) + 1
    if len(wanted.split()) < 3:
        return None
    best, best_line = LINE_FUZZY_RATIO, None
    words = set(wanted.split())
    matcher = difflib.SequenceMatcher(autojunk=False)
    matcher.set_seq2(wanted)
    for index, text in enumerate(spoken):
        if len(words & set(text.split())) < 0.5 * len(words):
            continue
        matcher.set_seq1(text)
        if matcher.quick_ratio() >= best and (ratio := matcher.ratio()) >= best:
            best, best_line = ratio, index + 1
    return best_line


# ---- the model call ----


@dataclass
class Outcome:
    call: ExtractionCall | None = None
    checked: object = None
    parsed: ParsedNotes | None = None
    failure: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


def _ask(data: MeetingData, client, model: str | None) -> Outcome:
    parsed = parse_notes(data.notes)
    outcome = Outcome(parsed=parsed)
    try:
        call = extract(
            parsed,
            data.transcript,
            data.day,
            DEFAULT_TIMEZONE,
            client=client,
            model=model,
            full=True,
        )
        outcome.call = call
        outcome.input_tokens, outcome.output_tokens = call.input_tokens, call.output_tokens
        outcome.checked = check_extraction(call.result, parsed, data.transcript, data.day)
    except ExtractionFailed as exc:  # its message is built by the app and carries no request text
        outcome.failure = str(exc)
        outcome.input_tokens, outcome.output_tokens = exc.input_tokens, exc.output_tokens
    except Exception as exc:  # class name only: the text of an error can hold secrets or paths
        outcome.failure = f"extraction: {type(exc).__name__}"
    return outcome


# ---- the page ----


def e(value) -> str:
    return html.escape(str(value), quote=True)


def _key(meeting_id: int, kind: str, index: int, text: str) -> str:
    digest = hashlib.sha1(text.encode()).hexdigest()[:8]  # noqa: S324 (a label, not security)
    return f"{meeting_id}:{kind}:{index}:{digest}"


def _card(
    meeting_id: int,
    item: str,
    kind: str,
    text: str,
    body: list[str],
    flags: list[str],
    line: int | None = None,
) -> str:
    link = f' data-line="l-{meeting_id}-{line}"' if line else ""
    parts = [
        f'<article class="item{" linked" if line else ""}" data-item="{e(item)}" '
        f'data-key="{e(_key(meeting_id, item, 0, text))}" data-meeting="{meeting_id}" '
        f'data-kind="{e(kind)}" data-text="{e(text)}"{link}>',
        f'<div class="kind">{e(kind)}'
        + (f' <span class="jump">line {line}: click to see it</span>' if line else "")
        + "</div>",
        *body,
        *[f'<div class="flag">{e(flag)}</div>' for flag in flags],
        '<div class="verdict"><button type="button" data-v="right">Right</button>'
        '<button type="button" data-v="wrong">Wrong</button>'
        '<button type="button" data-v="partly">Partly</button>'
        '<input type="text" class="note" maxlength="500" placeholder="Note (optional)"></div>',
        "</article>",
    ]
    return "".join(parts)


def _field(label: str, value, cls: str = "") -> str:
    shown = e(value) if value not in (None, "") else "<i>none</i>"
    return f'<div class="field {cls}"><span>{e(label)}</span> {shown}</div>'


def _raw_actions(call: ExtractionCall, count: int) -> dict[int, ModelAction]:
    given: dict[int, ModelAction] = {}
    for raw in call.result.action_items:
        if 0 <= raw.index < count and raw.index not in given:
            given[raw.index] = raw
    return given


def _items(data: MeetingData, outcome: Outcome) -> tuple[list[str], list[str]]:
    """Cards for the action items, and for decisions, misheard terms and milestones."""
    assert outcome.call and outcome.parsed
    mid, parsed, transcript = data.id, outcome.parsed, data.transcript
    result, checked = outcome.call.result, outcome.checked
    actions, rest = [], []
    raw_by_index = _raw_actions(outcome.call, len(parsed.action_items))
    for index, bullet in enumerate(parsed.action_items):
        raw, kept = raw_by_index.get(index), checked.action_items[index]
        flags = []
        if raw is None:
            flags.append(
                "The model gave nothing for this bullet: the app would use the bullet only."
            )
            body = [_field("From the notes", bullet)]
            text = f"{bullet} | model gave nothing"
            line = None
        else:
            if raw.spoken_owner and kept.spoken_owner is None:
                flags.append("Owner not found in the transcript: the app would drop it.")
            if raw.quote and kept.unverified:
                flags.append("Quote not found in the transcript: the app would drop it.")
            if not raw.quote:
                flags.append("No quote given.")
            if raw.due_date and kept.due_date is None:
                flags.append(
                    "Due date rejected by the app's check (not a real date or not that weekday)."
                )
            if not title_ok(raw.title, bullet):
                flags.append(
                    "Title shares too few words with the notes bullet: the app would use "
                    "the bullet."
                )
            body = [
                _field("From the notes", bullet, "muted"),
                _field("Title", raw.title),
                _field("Owner as the model named it", raw.spoken_owner, "owner"),
                _field("Due", " ".join(x for x in (raw.due_date, raw.due_phrase) if x)),
                _field("Quote", raw.quote, "quote"),
                _field("Confidence", raw.confidence),
            ]
            owner = raw.spoken_owner or "no owner named"
            due = " ".join(
                x for x in (raw.due_date, f"({raw.due_phrase})" if raw.due_phrase else "") if x
            )
            text = f"{raw.title} | owner: {owner} | due: {due or 'none'}"
            line = find_line(raw.quote, transcript) if raw.quote else None
        actions.append(_card(mid, f"a{index}", "Action item", text, body, flags, line))
    for index, text in enumerate(parsed.decisions):
        rest.append(
            _card(
                mid, f"d{index}", "Decision", text,
                [_field("From the notes", text)],
                [],
            )
        )  # fmt: skip
    kept_review = {r.index for r in checked.review}
    for entry in result.review:
        if not 0 <= entry.index < len(parsed.please_check):
            continue
        bullet = parsed.please_check[entry.index]
        flags = []
        if entry.index not in kept_review:
            flags.append(
                "Not kept by the app's check (not a name or term, or not in the transcript)."
            )
        text = f"{bullet} | heard as: {entry.heard_as or 'none'}"
        line = find_line(entry.heard_as, transcript) if entry.heard_as else None
        rest.append(
            _card(
                mid, f"r{entry.index}", "Misheard term", text,
                [_field("From the notes", bullet, "muted"), _field("Heard as", entry.heard_as)],
                flags, line,
            )
        )  # fmt: skip
    for index, milestone in enumerate(result.milestones):
        flags = []
        if not quote_in_transcript(milestone.quote, transcript):
            flags.append("Quote not found in the transcript: the app would drop this milestone.")
        if not due_date_ok(milestone.date, None, data.day):
            flags.append("Date rejected by the app's check: the app would drop this milestone.")
        text = f"{milestone.name} | date: {milestone.date}"
        rest.append(
            _card(
                mid, f"m{index}", "Milestone", text,
                [_field("Name", milestone.name), _field("Date", milestone.date),
                 _field("Quote", milestone.quote, "quote")],
                flags, find_line(milestone.quote, transcript),
            )
        )  # fmt: skip
    extras = len(result.action_items) - len(raw_by_index)
    if extras:
        rest.append(f'<div class="note-line">The model also returned {extras} action item entries '
                    "that match no bullet; the app ignores them.</div>")  # fmt: skip
    return actions, rest


def _transcript_html(data: MeetingData) -> str:
    out = []
    for number, line in enumerate(data.transcript.split("\n"), 1):
        if not line.strip():
            continue
        labelled = _LABEL.match(line)
        who, said = (labelled.group(1), labelled.group(2)) if labelled else ("", line)
        out.append(
            f'<div class="line" id="l-{data.id}-{number}"><span class="n">{number}</span>'
            f'<span class="who">{e(who)}</span><span class="said">{e(said)}</span></div>'
        )
    return "".join(out)


def _tokens(outcome: Outcome) -> str:
    if outcome.input_tokens is None and outcome.output_tokens is None:
        return "tokens not reported"
    return f"{outcome.input_tokens or 0:,} tokens in, {outcome.output_tokens or 0:,} out"


def _banner(kind: str, message: str) -> str:
    return f'<div class="banner {kind}">{e(message)}</div>'


def _section(data: MeetingData | None, meeting_id: int, outcome: Outcome | None) -> str:
    """One meeting. `data` is None when there is no such meeting."""
    if data is None:
        return (
            f'<section class="meeting" id="m{meeting_id}"><h2>Meeting {meeting_id}</h2>'
            + _banner("bad", f"No meeting with id {meeting_id}.")
            + "</section>"
        )
    head = [
        f'<section class="meeting" id="m{data.id}">',
        f"<h2>{e(data.label)} <small>meeting {data.id}, code {e(data.code)}, "
        f"{data.day.isoformat()}, status {e(data.status)}</small></h2>",
    ]
    if not data.transcript.strip():
        return "".join(
            [*head, _banner("bad", "This meeting has no transcript: nothing to review.")]
        )
    chars = len(data.transcript)
    facts = [f"transcript {chars:,} characters, {len(data.transcript.splitlines()):,} lines"]
    if outcome is not None:
        facts.append(_tokens(outcome))
        facts.append(
            "Sent whole to the model, the way the app sends it: the pipeline does not shorten "
            "transcripts."
        )
    head.append(f'<div class="facts">{e(" | ".join(facts))}</div>')
    if chars > VERY_LONG_CHARS:
        head.append(
            _banner(
                "warn",
                f"Very long transcript (about {chars // 4:,} tokens). The call has a 60 second "
                "limit and may fail or be costly; that is what the Tasks phase would meet too.",
            )
        )
    if not data.notes.strip():
        head.append(
            _banner(
                "warn",
                "This meeting has no notes. Action items and decisions are read from the notes, "
                "so there is nothing to extract; only misheard terms and milestones can appear.",
            )
        )
    head.append(
        '<div class="note-line">Stored transcripts carry no timestamps, so lines are numbered '
        "instead.</div>"
    )
    items = ""
    if outcome is not None and outcome.failure:
        head.append(
            _banner(
                "bad",
                f"The model call failed ({outcome.failure}). Nothing was extracted. "
                "Run it again; nothing was saved.",
            )
        )
    elif outcome is not None:
        actions, rest = _items(data, outcome)
        items = (
            "<h3>Action items</h3>"
            + ("".join(actions) or "<p><i>None in the notes.</i></p>")
            + "<h3>Decisions (read from the notes by the app, no model), misheard terms, "
            "milestones</h3>"
            + ("".join(rest) or "<p><i>None.</i></p>")
            + '<h3>People</h3><p class="note-line">extract-v1 returns no list of people: the '
            "owner of each action item, as the model named it, is on the card.</p>"
        )
    missing = (
        f'<h3>Missing</h3><p class="note-line">Anything that was agreed but the AI missed:</p>'
        f'<textarea class="missing" data-meeting="{data.id}" rows="4" '
        f'placeholder="One per line"></textarea>'
    )
    return "".join(
        [
            *head,
            '<div class="cols"><div class="pane transcript">',
            "<h3>Transcript</h3>",
            _transcript_html(data),
            '</div><div class="pane items">',
            items,
            missing,
            "</div></div></section>",
        ]
    )


CSS = """
:root{--bg:#f7f7f5;--fg:#1c1c1a;--mute:#6b6b66;--card:#fff;--line:#dcdcd6;--accent:#1f5eff;
--good:#1a7f37;--bad:#c62828;--warn:#9a6700;--hi:#fff3b0}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ecece8;--mute:#9a9a94;--card:#202020;
--line:#38383a;--accent:#7aa2ff;--good:#56d364;--bad:#ff7b72;--warn:#e3b341;--hi:#5c4b00}}
*{box-sizing:border-box}
body{margin:0;padding:0 0 90px;background:var(--bg);color:var(--fg);
font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;font-size:15px;line-height:1.45}
header.top{padding:14px 20px;border-bottom:1px solid var(--line);background:var(--card)}
header.top h1{margin:0 0 4px;font-size:18px}
header.top p{margin:2px 0;color:var(--mute);font-size:13px}
nav a{margin-right:12px}
main{padding:0 20px}
section.meeting{margin:20px 0 40px}
section.meeting h2{margin:0 0 6px;font-size:17px}
h2 small{font-weight:400;color:var(--mute);font-size:13px}
.facts,.note-line{color:var(--mute);font-size:13px;margin:4px 0}
.banner{padding:8px 12px;border-radius:6px;margin:8px 0;border:1px solid var(--line)}
.banner.bad{border-color:var(--bad);color:var(--bad)}
.banner.warn{border-color:var(--warn);color:var(--warn)}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:10px}
.pane{height:72vh;overflow:auto;border:1px solid var(--line);border-radius:8px;
background:var(--card);padding:6px 14px}
.pane h3{margin:12px 0 6px;font-size:14px;color:var(--mute);text-transform:uppercase;
letter-spacing:.04em}
.line{display:grid;grid-template-columns:34px 110px 1fr;gap:6px;padding:3px 4px;
border-radius:4px}
.line .n{color:var(--mute);text-align:right;font-size:12px;
font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.line .who{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.line .said{white-space:pre-wrap;overflow-wrap:anywhere}
.line.flash{background:var(--hi)}
.item{border:1px solid var(--line);border-radius:8px;padding:8px 10px;margin:8px 0}
.item.linked{cursor:pointer}
.item.right{border-left:5px solid var(--good)}
.item.wrong{border-left:5px solid var(--bad)}
.item.partly{border-left:5px solid var(--warn)}
.kind{font-size:12px;font-weight:700;color:var(--accent);text-transform:uppercase}
.jump{font-weight:400;text-transform:none;color:var(--mute)}
.field{margin:2px 0;overflow-wrap:anywhere}
.field span{color:var(--mute);font-size:12px}
.field.muted{color:var(--mute)}
.field.quote{font-style:italic}
.flag{color:var(--warn);font-size:13px;margin:3px 0}
.verdict{display:flex;gap:6px;margin-top:8px;flex-wrap:wrap}
.verdict button,footer button{font:inherit;padding:4px 12px;border-radius:6px;
border:1px solid var(--line);background:var(--bg);color:var(--fg);cursor:pointer}
.verdict button.on[data-v=right]{background:var(--good);color:#fff}
.verdict button.on[data-v=wrong]{background:var(--bad);color:#fff}
.verdict button.on[data-v=partly]{background:var(--warn);color:#fff}
.note{flex:1;min-width:140px;font:inherit;padding:4px 8px;border:1px solid var(--line);
border-radius:6px;background:var(--bg);color:var(--fg)}
textarea{width:100%;font:inherit;padding:8px;border:1px solid var(--line);border-radius:6px;
background:var(--bg);color:var(--fg)}
footer{position:fixed;left:0;right:0;bottom:0;display:flex;gap:16px;align-items:center;
flex-wrap:wrap;padding:10px 20px;background:var(--card);border-top:1px solid var(--line)}
footer .counts span{margin-right:12px}
footer button.primary{background:var(--accent);color:#fff;border-color:var(--accent)}
#out{display:none;width:100%;height:120px;font-family:ui-monospace,Menlo,monospace;font-size:12px}
@media (max-width:800px){.cols{grid-template-columns:1fr}.pane{height:60vh}}
"""

JS = """
(function(){
var KEY='nt-extraction-review:v1', state={items:{},missing:{}}, memory=null;
function load(){try{var raw=window.localStorage.getItem(KEY);if(raw){state=JSON.parse(raw);}}
catch(err){state=memory||state;}
state.items=state.items||{};state.missing=state.missing||{};}
function save(){memory=state;try{window.localStorage.setItem(KEY,JSON.stringify(state));}
catch(err){}}
function cards(){return Array.prototype.slice.call(document.querySelectorAll('.item[data-item]'));}
function paint(card){
var s=state.items[card.getAttribute('data-key')]||{};
['right','wrong','partly'].forEach(function(v){
card.classList.toggle(v,s.v===v);
card.querySelector('button[data-v='+v+']').classList.toggle('on',s.v===v);});
card.querySelector('.note').value=s.n||'';}
function counts(){
var c={right:0,wrong:0,partly:0,open:0};
cards().forEach(function(card){var s=state.items[card.getAttribute('data-key')]||{};
if(s.v){c[s.v]++;}else{c.open++;}});
document.getElementById('c-right').textContent=c.right;
document.getElementById('c-wrong').textContent=c.wrong;
document.getElementById('c-partly').textContent=c.partly;
document.getElementById('c-open').textContent=c.open;}
function setItem(card,patch){
var key=card.getAttribute('data-key'), s=state.items[key]||{};
Object.keys(patch).forEach(function(k){s[k]=patch[k];});
if(!s.v&&!s.n){delete state.items[key];}else{state.items[key]=s;}
save();paint(card);counts();}
function report(){
var lines=['EXTRACTION REVIEW ('+document.body.getAttribute('data-model')+', prompt '+
document.body.getAttribute('data-prompt')+')'], seen={};
cards().forEach(function(card){
var id=card.getAttribute('data-meeting');
if(!seen[id]){seen[id]=1;lines.push('','Meeting '+id+': '+
document.querySelector('#m'+id+' h2').firstChild.textContent.trim());}
var s=state.items[card.getAttribute('data-key')]||{};
lines.push('- ['+card.getAttribute('data-kind')+'] '+card.getAttribute('data-text'),
'  Verdict: '+(s.v?s.v.toUpperCase():'not judged')+(s.n?'\\n  Note: '+s.n:''));});
Array.prototype.forEach.call(document.querySelectorAll('textarea.missing'),function(box){
var id=box.getAttribute('data-meeting');
lines.push('','Meeting '+id+' MISSING (agreed but the AI missed): '+
(box.value.trim()||'nothing noted'));});
return lines.join('\\n');}
function copy(){
var text=report(), out=document.getElementById('out'), status=document.getElementById('copied');
function fallback(){out.style.display='block';out.value=text;out.focus();out.select();
status.textContent='Select and copy the text below';}
if(navigator.clipboard&&navigator.clipboard.writeText){
navigator.clipboard.writeText(text).then(function(){out.style.display='none';
status.textContent='Copied';},fallback);}else{fallback();}}
document.addEventListener('click',function(ev){
var t=ev.target, card=t.closest?t.closest('.item[data-item]'):null;
if(t.id==='copy'){copy();return;}
if(!card){return;}
if(t.tagName==='BUTTON'&&t.getAttribute('data-v')){
var cur=(state.items[card.getAttribute('data-key')]||{}).v, v=t.getAttribute('data-v');
setItem(card,{v:cur===v?'':v});return;}
if(t.tagName==='INPUT'){return;}
var line=card.getAttribute('data-line');
if(line){var el=document.getElementById(line);
if(el){el.scrollIntoView({block:'center',behavior:'smooth'});el.classList.add('flash');
setTimeout(function(){el.classList.remove('flash');},2500);}}});
document.addEventListener('input',function(ev){
var t=ev.target;
if(t.classList.contains('note')){setItem(t.closest('.item'),{n:t.value});}
else if(t.classList.contains('missing')){
state.missing[t.getAttribute('data-meeting')]=t.value;save();}});
load();
cards().forEach(paint);
Array.prototype.forEach.call(document.querySelectorAll('textarea.missing'),function(box){
box.value=state.missing[box.getAttribute('data-meeting')]||'';});
counts();
})();
"""


def _page(sections: list[str], model: str, outcomes: list[Outcome], prices, nav: list[str]) -> str:
    tokens_in = sum(o.input_tokens or 0 for o in outcomes)
    tokens_out = sum(o.output_tokens or 0 for o in outcomes)
    cost = ""
    if prices and outcomes:
        value = tokens_in * prices[0] / 1e6 + tokens_out * prices[1] / 1e6
        cost = f", estimated cost ${value:.4f} at your prices"
    nonce = secrets.token_urlsafe(16)
    csp = (
        f"default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-{nonce}'; "
        "base-uri 'none'; form-action 'none'"
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta http-equiv="Content-Security-Policy" content="{e(csp)}">'
        "<title>Extraction review</title>"
        f"<style>{CSS}</style></head>"
        f'<body data-model="{e(model)}" data-prompt="{e(EXTRACT_PROMPT_VERSION)}">'
        f'<header class="top"><h1>Extraction review</h1>'
        f"<p>Model <b>{e(model)}</b>, prompt <b>{e(EXTRACT_PROMPT_VERSION)}</b>: the full "
        f"extraction the Tasks phase will run. Tokens for this page: {tokens_in:,} in, "
        f"{tokens_out:,} out"
        f"{e(cost)}.</p>"
        "<p>Read-only: nothing was written to the database, and these answers are not stored "
        "anywhere but in this page and your browser. The transcript went to the model provider "
        "once per meeting, as it does for every meeting in the product. Your verdicts stay in this "
        "browser (localStorage).</p>"
        f"<nav>{''.join(nav)}</nav></header>"
        f"<main>{''.join(sections)}</main>"
        '<footer><div class="counts"><span>Right: <b id="c-right">0</b></span>'
        '<span>Wrong: <b id="c-wrong">0</b></span><span>Partly: <b id="c-partly">0</b></span>'
        '<span>Not judged: <b id="c-open">0</b></span></div>'
        '<button type="button" id="copy" class="primary">Copy my verdicts</button>'
        '<span id="copied"></span><textarea id="out" readonly></textarea></footer>'
        f'<script nonce="{e(nonce)}">{JS}</script></body></html>'
    )


def _safe(stream):
    """A stream that never fails on a character it cannot encode."""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure:
        reconfigure(errors="replace")
    return stream


def run(argv: list[str], *, client=None, out=None, err=None) -> int:
    out, err = _safe(out or sys.stdout), _safe(err or sys.stderr)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--meeting", type=int, action="append", help="a meeting id (repeatable)")
    parser.add_argument("--search", help="list meetings whose title, code or link contains this")
    parser.add_argument("--model", help="model to ask (default: the app's EXTRACT_MODEL)")
    parser.add_argument("--price-in", type=float, help="dollars per million input tokens")
    parser.add_argument("--price-out", type=float, help="dollars per million output tokens")
    args = parser.parse_args(argv)

    if args.search is not None:
        rows, more = search(args.search)
        if not rows:
            print(f"No meeting matches {args.search!r}.", file=out)
            return 0
        print(f"{'id':<8}{'date':<12}{'transcript':<15}{'status':<10}label", file=out)
        for row in rows:
            print(row, file=out)
        if more:
            print(
                f"(only the newest {SEARCH_LIMIT} are shown: search for something longer)", file=out
            )
        return 0
    if not args.meeting:
        print("Give --meeting <id> (repeatable) or --search <text>.", file=err)
        return 2

    model = args.model or get_settings().extract_model
    prices = (args.price_in, args.price_out) if args.price_in and args.price_out else None
    sections, outcomes, nav, failed = [], [], [], False
    for meeting_id in args.meeting:
        with open_readonly() as session:
            meeting = session.get(Meeting, meeting_id)
            data = _load(meeting) if meeting is not None else None
        # the session is closed before the model is asked: no connection is held across the call
        outcome = None
        if data is not None and data.transcript.strip():
            outcome = _ask(data, client, args.model)
            outcomes.append(outcome)
        if data is None:
            print(f"meeting {meeting_id}: no such meeting", file=err)
        elif not data.transcript.strip():
            print(f"meeting {meeting_id}: no transcript", file=err)
        elif outcome and outcome.failure:
            print(f"meeting {meeting_id}: model call failed ({outcome.failure})", file=err)
        failed |= data is None or not data.transcript.strip() or bool(outcome and outcome.failure)
        sections.append(_section(data, meeting_id, outcome))
        nav.append(f'<a href="#m{meeting_id}">Meeting {meeting_id}</a>')
    page = _page(sections, model, outcomes, prices, nav)
    out.write(page.encode("ascii", "xmlcharrefreplace").decode("ascii") + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
