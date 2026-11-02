"""Cheap guards that keep the UI on one design system.

The plan is in docs/superpowers/specs/2026-11-02-frontend-approach.md.

They read files only, so they run without a database. A failure means a screen has drifted from
the tokens and components, which is exactly the parity problem they exist to prevent.
"""

import re
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
CSS = sorted((APP / "static").glob("*.css"))
TEMPLATES = sorted((APP / "templates").glob("*.html"))

HEX_COLOUR = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{8}\b")
COLOUR_FUNCTION = re.compile(r"\b(?:rgb|rgba|hsl|hsla)\(")


def test_the_design_files_exist():
    names = {p.name for p in CSS}
    assert {"tokens.css", "components.css", "app.css"} <= names
    assert TEMPLATES


def test_no_colour_literals_outside_tokens():
    offenders = []
    for path in [*CSS, *TEMPLATES]:
        if path.name == "tokens.css":
            continue
        text = path.read_text()
        if HEX_COLOUR.search(text) or COLOUR_FUNCTION.search(text):
            offenders.append(path.name)
    assert not offenders, (
        f"colour literals found outside tokens.css: {offenders}; add a token instead"
    )


def test_no_inline_styles_or_handlers_in_templates():
    # The CSP forbids both, and inline styling is how screens drift from the components.
    offenders = []
    for path in TEMPLATES:
        text = re.sub(r"\{#.*?#\}", "", path.read_text(), flags=re.S)
        if re.search(r"\sstyle\s*=", text) or re.search(r"\son[a-z]+\s*=", text):
            offenders.append(path.name)
    assert not offenders, f"inline style= or on*= attributes in: {offenders}"


def test_every_macro_is_in_the_style_guide():
    macros = re.findall(
        r"\{%-?\s*macro\s+(\w+)\(", (APP / "templates" / "components.html").read_text()
    )
    guide = (APP / "templates" / "styleguide.html").read_text()
    assert macros
    missing = [m for m in macros if f"ui.{m}(" not in guide]
    assert not missing, f"macros missing from styleguide.html: {missing}"


def test_every_template_page_uses_the_shared_head():
    # head.html brings tokens.css, components.css and the fonts; a page that skips it is off-brand.
    for name in ("base.html", "public_base.html"):
        assert '{% include "head.html" %}' in (APP / "templates" / name).read_text()
