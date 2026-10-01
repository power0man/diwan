"""CSS/HTML contracts for #224; actual geometry is measured by the Chromium audit.

These checks do not pretend to render CSS. They prevent the known overlay and
full-width-control declarations from returning, while the browser receipt
records hit-testing, dimensions, keyboard activation and axe results.
"""
from html.parser import HTMLParser
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def rules(selector):
    css = (ROOT / "webui/static/style.css").read_text()
    result = []
    # Inspect declarations at every breakpoint; a mobile override cannot undo
    # a flow-safe base rule unnoticed. This is not a browser cascade engine.
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if selector in [part.strip() for part in selectors.split(",")]:
            result.append(dict(item.strip().split(":", 1) for item in body.split(";") if ":" in item))
    assert result, selector
    return result


class Controls(HTMLParser):
    def __init__(self):
        super().__init__()
        self.label = None
        self.thinking_label = None
        self.textarea = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "label":
            self.label = attrs
        if attrs.get("id") == "agent-thinking":
            self.thinking_label = self.label
        if attrs.get("id") == "message":
            self.textarea = attrs

    def handle_endtag(self, tag):
        if tag == "label":
            self.label = None


def controls():
    parser = Controls()
    parser.feed((ROOT / "webui/static/index.html").read_text())
    return parser


def test_composer_stays_in_document_flow_at_every_breakpoint():
    positions = [rule["position"] for rule in rules("#composer") if "position" in rule]
    assert positions and all(value in {"static", "relative"} for value in positions)


def test_draft_growth_is_bounded_and_initial_field_is_compact():
    attrs = controls().textarea
    assert attrs and 1 <= int(attrs["rows"]) <= 2
    cap = rules("#message")[-1]["max-height"]
    match = re.fullmatch(r"(\d+(?:\.\d+)?)dvh", cap)
    assert match and 15 <= float(match[1]) <= 30


def test_thinking_checkbox_keeps_native_width_next_to_its_own_label():
    label = controls().thinking_label
    assert label and "thinking-option" in label.get("class", "").split()
    box = rules('.thinking-option input[type="checkbox"]')[-1]
    assert box["width"] == "auto" and box["flex"] == "0 0 auto"
    assert rules(".thinking-option")[-1]["align-items"] == "flex-start"
