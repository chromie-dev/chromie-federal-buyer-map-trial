"""The Markdown rendering of a saved webpage: the processed form every HTML reader shares.

The raw bytes are the document and keep their hash; the rendering is what the readers and the agent see.
It must carry every sentence of the page verbatim (the evidence-span rule in reader.py depends on it),
keep the page's block structure, drop the site chrome, and render the same bytes the same way every time."""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "tools"))
from markdown import SKIP, html_to_markdown  # noqa: E402
from reader import flatten, flatten_lines  # noqa: E402

PAGE = """<!DOCTYPE html><html><head><title>CNO Remarks | U.S. Navy</title>
<meta property="og:title" content="CNO Remarks"><script>window.x = 'not text';</script><style>p{}</style></head>
<body><nav><a href="/">Home</a> <a href="/press">Press</a></nav>
<form id="Form" method="post"><div class="article-view">
<h4 class="press-release__dateline">Panama City, Panama</h4>
<h1>CNO Remarks at the Inter-American Naval Conference</h1>
<p>Good morning. We need <strong>autonomous systems</strong> on&#8209;watch now, and we need <a href="/x">industry</a> to build them.</p>
<p>The Navy requested $58.739 million for line 2614 in fiscal year 2026.<br>That request is 11 percent below last year.</p>
<h2>Three priorities</h2>
<ul><li>Readiness of the fleet we have</li><li>Capacity in the yards<ul><li>Public and private</li></ul></li></ul>
<blockquote><p>We will not accept delay as a plan.</p></blockquote>
<table><tr><th>Line</th><th>FY2026</th><th>FY2027</th></tr><tr><td>2614 ATDLS</td><td>$58.739M</td><td>$52.758M</td></tr></table>
<div class="bottom-blue"><h2>Speech by</h2><p> Adm. Daryl Caudle <br> </p><h2>Presented on</h2><p>23 June 2026</p></div>
</div></form><footer>Privacy Policy</footer><svg><text>icon</text></svg></body></html>"""


class _Visible(HTMLParser):
    """Every text node outside the tags the rendering drops, as the page writes it."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.nodes: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.nodes.append(re.sub(r"\s+", " ", data).strip())


def test_every_visible_text_node_is_in_the_rendering_verbatim() -> None:
    seen = _Visible()
    seen.feed(PAGE)
    md = html_to_markdown(PAGE)
    assert seen.nodes, "the fixture has text"
    for node in seen.nodes:
        assert node in md, node


def test_a_sentence_survives_flatten_the_way_the_verbatim_rule_reads_it() -> None:
    md = flatten_lines(html_to_markdown(PAGE))
    flat = flatten(md)
    for span in ("We need autonomous systems on-watch now, and we need industry to build them.",
                 "The Navy requested $58.739 million for line 2614 in fiscal year 2026.",
                 "We will not accept delay as a plan."):
        assert span in flat, span
    assert flatten(flatten_lines(md)) == flatten(md)


def test_block_structure_is_kept_and_markers_stand_only_at_line_starts() -> None:
    md = html_to_markdown(PAGE)
    assert "# CNO Remarks at the Inter-American Naval Conference\n" in md
    assert "\n## Three priorities\n" in md
    assert "\n- Readiness of the fleet we have\n- Capacity in the yards\n  - Public and private\n" in md
    assert "\n> We will not accept delay as a plan.\n" in md
    assert "| Line | FY2026 | FY2027 |\n| --- | --- | --- |\n| 2614 ATDLS | $58.739M | $52.758M |" in md
    assert "on-watch now, and we need industry to build them.\n\nThe Navy requested" in md.replace("‑", "-")
    for line in md.split("\n"):
        body = re.sub(r"^(?:>\s?)*(?:#{1,6}\s|-\s|\d+\.\s|\|)?", "", line)
        assert "**" not in body and "](" not in body and "<" not in body, line


def test_chrome_is_dropped_and_the_form_wrapper_is_not() -> None:
    md = html_to_markdown(PAGE)
    for gone in ("Home", "Press", "Privacy Policy", "icon", "not text", "p{}", "CNO Remarks | U.S. Navy"):
        assert gone not in md, gone
    assert "Good morning." in md, "the .navy.mil content system wraps the article in a form; the form's content is the article"


def test_a_layout_table_renders_as_its_paragraphs_and_a_data_table_as_rows() -> None:
    layout = ("<table><tr><td><h1>CHIPS Articles: Navy Establishes Two New IT Delivery Offices</h1>"
              "<p>By PEO Digital and PEO MLB Public Affairs - April-June 2020</p>"
              "<p>The effort involves disestablishing the Program Executive Office Enterprise Information Systems (PEO EIS).</p></td>"
              "<td><a href=\"/email\">Email</a></td></tr></table>")
    md = html_to_markdown(layout)
    assert md.split("\n\n") == ["CHIPS Articles: Navy Establishes Two New IT Delivery Offices",
                                "By PEO Digital and PEO MLB Public Affairs - April-June 2020",
                                "The effort involves disestablishing the Program Executive Office Enterprise Information Systems (PEO EIS).",
                                "Email"], md
    assert "|" not in md and "#" not in md, "a layout table is paragraphs; no marker stands inside a line"
    data = "<table><tr><th>Line</th><th>FY2026</th></tr><tr><td>2614 ATDLS</td><td>$58.739M</td></tr></table>"
    assert html_to_markdown(data) == "| Line | FY2026 |\n| --- | --- |\n| 2614 ATDLS | $58.739M |"


def test_an_anchor_joins_its_sentence_without_an_inserted_space() -> None:
    """The old tag stripping put a space where every tag had been, so a linked name read `Gaucher , Navy director`."""
    md = html_to_markdown("<p>Other keynotes include: <a href='/g'>Vice Adm. Rob Gaucher</a>, Navy director of submarine programs</p>")
    assert md == "Other keynotes include: Vice Adm. Rob Gaucher, Navy director of submarine programs"


def test_rendering_is_deterministic_and_entities_are_text() -> None:
    assert html_to_markdown(PAGE) == html_to_markdown(PAGE)
    assert html_to_markdown("<p>Tail &amp; end &lt;here&gt;</p>") == "Tail & end <here>"
    assert html_to_markdown("<p>a</p><div></div><div><span></span></div><p>b</p>") == "a\n\nb"
    assert html_to_markdown("") == ""
