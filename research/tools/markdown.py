"""A saved webpage as Markdown: the one rendering every reader of HTML shares.

    python research/tools/markdown.py --selfcheck
    python research/tools/markdown.py data/raw/<file>      # print the rendering

The raw bytes under data/raw/ stay what the host sent, because their SHA-256 is the document's
identity in the manifest, the evidence rows and the brain items. What changes here is the processed
form: where a reader used to strip tags into one line of text, it now renders the page as Markdown,
so the block structure the page had (headings, paragraphs, lists, tables, quotes) reaches the reader
and the agent.

The rendering is deliberately a conservative subset, for the verbatim rule in reader.py: every
sentence on the page must appear in the rendering exactly as the page writes it, so an evidence span
the agent copies can be found again. Block markers therefore stand only at the start of a line
(`#`, `-`, `1.`, `>`, `|`), and nothing is inserted inside a sentence: an anchor keeps its text and
loses its address, emphasis keeps its words and loses its asterisks, an image contributes nothing.
Script, style, navigation, footer and SVG content are dropped, as the readers always dropped them;
`form` is not, because the .navy.mil content system wraps the whole article in one. A table whose
cells hold paragraphs or headings is page layout, not data, and renders as the paragraphs it holds;
a table of plain cells renders as a pipe table.

Stdlib only, no network, deterministic: the same bytes render to the same Markdown on every run,
which the cassette keys and the byte-identity checks rely on.
"""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

VERSION = 1
SKIP = {"script", "style", "nav", "footer", "svg", "noscript", "template", "head", "title"}
BLOCKS = {"p", "div", "section", "article", "main", "header", "aside", "address", "figure", "figcaption",
          "details", "summary", "form", "fieldset", "dl", "dt", "dd", "center"}
HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
LISTS = {"ul", "ol", "li", "blockquote", "pre", "hr"}
TABLE = {"table", "tr", "td", "th"}
VOID = {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed", "source", "track", "wbr", "param"}


class _Markdown(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip = 0
        self._pre = 0
        self._lists: list[list] = []            # [kind, count] per open list
        self._quote = 0
        self._cell: list[str] | None = None     # text of the table cell being read
        self._row: list[str] | None = None      # cells of the row being read
        self._tables: list[dict] = []           # per open table: rows, header, layout
        self._outer: list[tuple] = []           # (cell, row) of the table a nested table sits in

    # -- helpers -------------------------------------------------------------------------
    def _emit(self, text: str) -> None:
        (self._cell if self._cell is not None else self.out).append(text)

    def _break(self) -> None:
        """Start a new block: at most one blank line between blocks."""
        self.out.append("\n\n")

    def _prefix(self) -> str:
        return "> " * self._quote

    # -- tags ----------------------------------------------------------------------------
    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            self._skip += 1
            return
        if self._skip:
            return
        if self._cell is not None and tag not in TABLE:
            # Inside a cell nothing is marked; a block boundary is remembered, and decides at the
            # table's end whether this was a data table or the page's layout.
            if tag in HEADINGS or tag in BLOCKS or tag in LISTS:
                self._tables[-1]["layout"] = True
                self._cell.append("\n")
            elif tag == "br":
                self._cell.append("\n")
            return
        if tag in HEADINGS:
            self._break()
            self._emit(self._prefix() + "#" * HEADINGS[tag] + " ")
        elif tag in BLOCKS:
            self._break()
            if self._quote:
                self._emit(self._prefix())
        elif tag == "br":
            self._emit("\n" + self._prefix())
        elif tag == "hr":
            self._break()
            self._emit("---")
            self._break()
        elif tag in ("ul", "ol"):
            if not self._lists:
                self._break()  # a nested list continues its item's line; each item brings its own break
            self._lists.append([tag, 0])
        elif tag == "li":
            kind, count = self._lists[-1] if self._lists else ["ul", 0]
            if self._lists:
                self._lists[-1][1] = count + 1
            indent = "  " * (len(self._lists) - 1) if self._lists else ""
            self._emit("\n" + self._prefix() + indent + (f"{count + 1}. " if kind == "ol" else "- "))
        elif tag == "blockquote":
            self._break()
            self._quote += 1
            self._emit(self._prefix())
        elif tag == "pre":
            self._break()
            self._pre += 1
        elif tag == "table":
            if self._cell is not None:  # a table inside a cell: the outer one is layout
                self._tables[-1]["layout"] = True
                self._outer.append((self._cell, self._row))
                self._cell, self._row = None, None
            else:
                self._break()
            self._tables.append({"rows": [], "header": False, "layout": False})
        elif tag == "tr" and self._tables:
            self._close_cell()
            self._row = []
        elif tag in ("td", "th") and self._tables:
            self._close_cell()
            if self._row is None:
                self._row = []
            self._cell = []
            if tag == "th" and not self._tables[-1]["rows"]:
                self._tables[-1]["header"] = True

    def handle_endtag(self, tag):
        if tag in SKIP:
            if self._skip:
                self._skip -= 1
            return
        if self._skip:
            return
        if self._cell is not None and tag not in TABLE:
            if tag in HEADINGS or tag in BLOCKS or tag in LISTS:
                self._cell.append("\n")
            return
        if tag in HEADINGS or tag in BLOCKS:
            self._break()
        elif tag in ("ul", "ol"):
            if self._lists:
                self._lists.pop()
            if not self._lists:
                self._break()
        elif tag == "blockquote":
            if self._quote:
                self._quote -= 1
            self._break()
        elif tag == "pre":
            if self._pre:
                self._pre -= 1
            self._break()
        elif tag in ("td", "th"):
            self._close_cell()
        elif tag == "tr":
            self._close_row()
        elif tag == "table" and self._tables:
            self._close_row()
            table = self._tables.pop()
            rendered = _render_layout(table["rows"], self._prefix()) if table["layout"] else _render_table(table["rows"], self._prefix())
            if self._outer:
                self._cell, self._row = self._outer.pop()
                self._cell.append("\n" + rendered + "\n")
            else:
                self._emit(rendered)
                self._break()

    def _close_cell(self) -> None:
        if self._cell is not None and self._row is not None:
            self._row.append("".join(self._cell))
        self._cell = None

    def _close_row(self) -> None:
        self._close_cell()
        if self._row is not None and self._tables and any(c.strip() for c in self._row):
            self._tables[-1]["rows"].append(self._row)
        self._row = None

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if self._skip:
            return
        self._emit(data if self._pre else re.sub(r"\s+", " ", data))

    def close(self) -> None:
        super().close()
        while self._tables:  # a page cut off inside a table still renders what it had
            self.handle_endtag("table")

    def text(self) -> str:
        return _tidy("".join(self.out))


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _render_table(rows: list[list[str]], prefix: str) -> str:
    """A data table as pipe rows; the first row is the header row."""
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    lines = []
    for i, row in enumerate(rows):
        cells = [_squash(c) for c in row] + [""] * (width - len(row))
        lines.append(prefix + "| " + " | ".join(cells) + " |")
        if i == 0:
            lines.append(prefix + "|" + "|".join([" --- "] * width) + "|")
    return "\n".join(lines)


def _render_layout(rows: list[list[str]], prefix: str) -> str:
    """A layout table as the blocks its cells hold, in reading order."""
    blocks: list[str] = []
    table: list[str] = []  # the rows of a data table nested in a cell stay together

    def flush() -> None:
        if table:
            blocks.append("\n".join(table))
            table.clear()

    for row in rows:
        for cell in row:
            for line in cell.split("\n"):
                if line.startswith("|"):
                    table.append(line.rstrip())
                elif _squash(line):
                    flush()
                    blocks.append(prefix + _squash(line))
            flush()
    return "\n\n".join(blocks)


def _tidy(text: str) -> str:
    lines = [line.rstrip() for line in text.split("\n")]
    # A block marker with nothing after it is an empty element, not a line.
    lines = ["" if re.fullmatch(r"(?:>\s?)*(?:#{1,6}|-|\d+\.)?\s*", line) else line for line in lines]
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(line.lstrip(" ") if not line.startswith("  ") else line for line in lines))
    return out.strip()


def html_to_markdown(html: str) -> str:
    """The Markdown rendering of one HTML document (a str; decode bytes with 'replace' first)."""
    parser = _Markdown()
    parser.feed(html)
    parser.close()
    return parser.text()


def read_markdown(path: Path) -> str:
    return html_to_markdown(path.read_bytes().decode("utf-8", "replace"))


def selfcheck() -> int:
    page = ("<html><head><title>T</title><meta name=x content=y><script>var a='drop';</script></head><body>"
            "<nav>Home Press</nav><form><div class=\"article-view\"><h1>Naval Update</h1>"
            "<p>The <strong>Navy</strong> needs <a href=\"/x\">autonomous systems</a> by 2027.<br>Second line.</p>"
            "<ul><li>one</li><li>two<ul><li>nested</li></ul></li></ul><ol><li>first</li><li>second</li></ol>"
            "<blockquote><p>Quoted words.</p></blockquote>"
            "<table><tr><th>Line</th><th>FY2027</th></tr><tr><td>2614</td><td>$52.758M</td></tr></table>"
            "<pre>  keep   spacing</pre><hr><p>Tail &amp; end.</p></div></form><footer>Privacy</footer><svg><text>no</text></svg></body></html>")
    md = html_to_markdown(page)
    expected = ("# Naval Update\n\n"
                "The Navy needs autonomous systems by 2027.\nSecond line.\n\n"
                "- one\n- two\n  - nested\n\n"
                "1. first\n2. second\n\n"
                "> Quoted words.\n\n"
                "| Line | FY2027 |\n| --- | --- |\n| 2614 | $52.758M |\n\n"
                "  keep   spacing\n\n---\n\nTail & end.")
    assert md == expected, md
    for gone in ("drop", "Home Press", "Privacy", "<", "/x", "**", "T\n"):
        assert gone not in md, gone
    assert html_to_markdown(page) == md, "rendering must be deterministic"
    # The .navy.mil content system lays the article out in a table: its cells are paragraphs, not data.
    layout = ("<table><tr><td><h1>CHIPS Articles: Two New Offices</h1><p>By Public Affairs - April 2020</p>"
              "<p>The effort involves disestablishing PEO EIS.</p></td><td><a href=\"/e\">Email</a></td></tr>"
              "<tr><td><table><tr><td>Tag A</td><td>Tag B</td></tr></table></td></tr></table>")
    assert html_to_markdown(layout) == ("CHIPS Articles: Two New Offices\n\nBy Public Affairs - April 2020\n\n"
                                        "The effort involves disestablishing PEO EIS.\n\nEmail\n\n| Tag A | Tag B |\n| --- | --- |"), html_to_markdown(layout)
    assert html_to_markdown("<p>a</p><div></div><p>b</p>") == "a\n\nb"
    assert html_to_markdown("plain words &lt;tag&gt;") == "plain words <tag>"
    assert html_to_markdown("<div>x<br/>y</div>") == "x\ny"
    assert html_to_markdown("<table><tr><td>cut off") == "| cut off |\n| --- |"
    assert html_to_markdown("<p><a href='/a'>Vice Adm. Rob Gaucher</a>, Navy director of submarine programs</p>") == \
        "Vice Adm. Rob Gaucher, Navy director of submarine programs", "an anchor's text joins its sentence without a space"
    print("markdown selfcheck ok")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--selfcheck" in args:
        sys.exit(selfcheck())
    if args:
        print(read_markdown(Path(args[0])))
        sys.exit(0)
    print(__doc__)
    sys.exit(2)
