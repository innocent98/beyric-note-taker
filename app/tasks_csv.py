"""The Tasks screen as a CSV file (`GET /api/tasks/export`).

The rows are the tasks the screen shows, read through the one visibility rule (`app/tasks.py`
`list_tasks`, which the Tasks list route uses too); this module only lays them out. UTF-8 with a
byte order mark, so Excel reads accented names correctly, and every cell that a spreadsheet could
read as a formula is made plain text (the CSV injection rule below).
"""

import csv
import io
from collections.abc import Iterable

COLUMNS = (
    "Title",
    "Status",
    "Assignee",
    "Due date",
    "Project",
    "Sprint",
    "Meeting title",
    "Meeting date",
)

# What the board calls the three statuses (`web/lib/tasks.ts`).
STATUS_LABELS = {"todo": "To do", "doing": "In progress", "done": "Done"}

# A cell that begins with one of these is a formula to Excel, Sheets and Numbers. The titles are
# written by a model from what people said in a call, and the names by the people themselves, so
# any of them can begin this way.
_FORMULA_STARTS = ("=", "+", "-", "@")
# Tab and carriage return are neutralised wherever the cell starts with them; the four above also
# after leading white space (a spreadsheet reads " =1+1" as a formula too).
_ALWAYS_STARTS = ("\t", "\r")


def safe_cell(value: object) -> str:
    """The cell as text, with a single quote in front when a spreadsheet would run it as a formula
    (the OWASP rule for CSV injection): it starts with `=`, `+`, `-` or `@` once leading white
    space is ignored, or with a tab or a carriage return. The quote stays visible in the cell when
    the file is opened in Excel or Sheets: that is the price of never running someone else's text
    as a formula."""
    text = "" if value is None else str(value)
    if text.startswith(_ALWAYS_STARTS) or text.lstrip().startswith(_FORMULA_STARTS):
        return f"'{text}"
    return text


def build_csv(rows: Iterable[Iterable[object]]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)  # \r\n line ends, a cell with a comma, quote or line break quoted
    writer.writerow(COLUMNS)
    for row in rows:
        writer.writerow([safe_cell(cell) for cell in row])
    return b"\xef\xbb\xbf" + out.getvalue().encode("utf-8")
