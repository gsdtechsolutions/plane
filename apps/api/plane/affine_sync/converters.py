# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Markdown <-> Plane wiki description_html conversion (dependency-free).

Plane wiki pages round-trip HTML via ``description_html``. AFFiNE exchanges
markdown. This module provides the two pure functions the sync engine needs:

  html_to_markdown(description_html) -> str
  markdown_to_html(markdown)         -> str

Scope: block-level fidelity (headings, paragraphs, lists, code, quotes,
dividers, links, emphasis, inline code, tables). This is a deliberate trade —
the engine stores source-hashes so content only rewrites when it actually
changed, keeping round-trips lossless *by idempotence* even where formatting
is approximate. Exotic embeds (databases, edgeless frames, latex) degrade to
paragraph text on the Plane side and to fenced text on the AFFiNE side.
"""

import html as html_module
import re
from html.parser import HTMLParser

# --------------------------------------------------------------------------- #
# HTML -> Markdown
# --------------------------------------------------------------------------- #


class _HTMLToMarkdown(HTMLParser):
    """Streaming HTML->markdown converter over Python's stdlib parser.

    Maintains a block stack for nested lists; inline tags accumulate into the
    current block buffer and are unwrapped at block close.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list = []
        # each entry: {"kind": "p"|"li"|"pre"|"quote"|..., "buf": [str], "depth": int, "ordered": bool, "index": int}
        self.blocks: list = []
        self.inline: list = []  # markers for emphasis spans
        self.list_stack: list = []  # entries: {"ordered": bool, "index": int}
        self.pre_lang: str = ""
        self.anchor_href: str = ""

    # -- helpers ---------------------------------------------------------- #

    @property
    def current_block(self):
        return self.blocks[-1] if self.blocks else None

    def _push_block(self, kind, **extra):
        block = {"kind": kind, "buf": [], "depth": 0}
        block.update(extra)
        self.blocks.append(block)
        return block

    def _pop_block(self):
        block = self.blocks.pop()
        text = "".join(block["buf"])
        self._emit(block, text)

    def _emit(self, block, text):
        kind = block["kind"]
        text = text.strip("\n")
        if kind == "p":
            if text:
                self.out.append(text)
        elif kind == "h":
            level = block["level"]
            self.out.append("#" * level + " " + text)
        elif kind == "li":
            depth = block["depth"]
            indent = "  " * depth
            if block["ordered"]:
                self.out.append(f"{indent}{block['index']}. {text}")
            else:
                self.out.append(f"{indent}- {text}")
        elif kind == "pre":
            lang = block["lang"]
            fence = "```"
            while fence in text:
                fence += "`"
            self.out.append(fence + (lang or "") + "\n" + text + "\n" + fence)
        elif kind == "quote":
            for line in text.splitlines() or [""]:
                self.out.append("> " + line)
        elif kind == "divider":
            self.out.append("---")
        elif kind == "table":
            rows = block["rows"]
            if not rows:
                return
            width = max(len(r) for r in rows)
            rows = [r + [""] * (width - len(r)) for r in rows]
            header, body = rows[0], rows[1:]
            self.out.append("| " + " | ".join(header) + " |")
            self.out.append("|" + "|".join([" --- " for _ in header]) + "|")
            for row in body:
                self.out.append("| " + " | ".join(row) + " |")

    # -- tag handlers ------------------------------------------------------ #

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._push_block("h", level=int(tag[1]))
        elif tag == "p":
            # skip wrapper <p> inside <li>/<td>: only open a paragraph at top level
            if not self.blocks or self.current_block["kind"] in ("quote",):
                self._push_block("p")
            else:
                self.blocks.append({"kind": "inline-into", "buf": self.current_block["buf"], "depth": 0})
        elif tag in ("ul", "ol"):
            self.list_stack.append({"ordered": tag == "ol", "index": 0})
        elif tag == "li":
            depth = max(len(self.list_stack) - 1, 0)
            parent = self.list_stack[depth] if self.list_stack else {"ordered": False, "index": 0}
            parent["index"] += 1
            self._push_block("li", depth=depth, ordered=parent["ordered"], index=parent["index"])
        elif tag == "pre":
            self._push_block("pre", lang="")
        elif tag == "code" and self.current_block and self.current_block["kind"] == "pre":
            pass  # handled by block
        elif tag == "blockquote":
            self._push_block("quote")
        elif tag == "hr":
            self._push_block("divider")
        elif tag == "table":
            self._push_block("table", rows=[])
        elif tag == "tr" and self.current_block and self.current_block["kind"] == "table":
            self.current_block["current_row"] = []
        elif tag in ("td", "th") and self.current_block and self.current_block["kind"] == "table":
            self.current_block.setdefault("current_row", []).append({"buf": []})
        elif tag == "br":
            block = self.current_block
            if block:
                block["buf"].append("\n")
        elif tag == "img":
            src = attrs.get("src", "")
            alt = attrs.get("alt", "image")
            if src:
                if self.current_block:
                    self.current_block["buf"].append(f"![{alt}]({src})")
                else:
                    self.out.append(f"![{alt}]({src})")
        elif tag == "a":
            self.anchor_href = attrs.get("href", "")
            if self.current_block:
                self.current_block["buf"].append("[")
        elif tag in ("strong", "b"):
            if self.current_block:
                self.current_block["buf"].append("**")
        elif tag in ("em", "i"):
            if self.current_block:
                self.current_block["buf"].append("*")
        elif tag == "code":
            if self.current_block:
                self.current_block["buf"].append("`")
        elif tag in ("s", "strike", "del"):
            if self.current_block:
                self.current_block["buf"].append("~~")

    def handle_endtag(self, tag):
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            if self.current_block and self.current_block["kind"] == "h":
                self._pop_block()
        elif tag == "p":
            if self.current_block and self.current_block["kind"] == "p":
                self._pop_block()
            elif self.current_block and self.current_block["kind"] == "inline-into":
                self.blocks.pop()  # discard shim, buffer shared
        elif tag in ("ul", "ol"):
            if self.list_stack:
                self.list_stack.pop()
            # a list at top level ends the previous li block
            if self.current_block and self.current_block["kind"] == "li":
                self._pop_block()
        elif tag == "li":
            if self.current_block and self.current_block["kind"] == "li":
                self._pop_block()
        elif tag == "pre":
            if self.current_block and self.current_block["kind"] == "pre":
                self._pop_block()
        elif tag == "blockquote":
            if self.current_block and self.current_block["kind"] == "quote":
                self._pop_block()
        elif tag == "hr":
            if self.current_block and self.current_block["kind"] == "divider":
                self._pop_block()
        elif tag in ("td", "th"):
            block = self.current_block
            if block and block["kind"] == "table" and block.get("current_row"):
                cell = block["current_row"][-1]
                cell["text"] = "".join(cell["buf"])
        elif tag == "tr":
            block = self.current_block
            if block and block["kind"] == "table" and block.get("current_row") is not None:
                block["rows"].append([c.get("text", "") for c in block.pop("current_row")])
        elif tag == "table":
            if self.current_block and self.current_block["kind"] == "table":
                self._pop_block()
        elif tag == "a":
            href = self.anchor_href
            self.anchor_href = ""
            block = self.current_block
            if block and href:
                # take back the raw buffer since the "[" we emitted: rewrite by wrapping the span
                text_content = "".join(block["buf"])
                # find the last unclosed [ occurrence and close it
                idx = text_content.rfind("[")
                if idx != -1:
                    inner = text_content[idx + 1:]
                    block["buf"] = [text_content[:idx] + f"[{inner}]({href})"]
        elif tag in ("strong", "b"):
            if self.current_block:
                self.current_block["buf"].append("**")
        elif tag in ("em", "i"):
            if self.current_block:
                self.current_block["buf"].append("*")
        elif tag == "code":
            if self.current_block and self.current_block["kind"] != "pre":
                self.current_block["buf"].append("`")
        elif tag in ("s", "strike", "del"):
            if self.current_block:
                self.current_block["buf"].append("~~")

    def handle_data(self, data):
        if self.current_block:
            self.current_block["buf"].append(data)

    def result(self) -> str:
        while self.blocks:
            self._pop_block()
        return "\n\n".join(part for part in (p.strip("\n") for p in self.out) if part.strip()) + ("\n" if self.out else "")


def html_to_markdown(description_html: str) -> str:
    """Convert Plane wiki description_html to AFFiNE markdown."""
    html = description_html or ""
    if not html.strip():
        return ""
    parser = _HTMLToMarkdown()
    try:
        parser.feed(html)
        parser.close()
        return parser.result()
    except Exception:
        # last resort: strip tags so sync still progresses
        return re.sub(r"<[^>]+>", " ", html).strip()


# --------------------------------------------------------------------------- #
# Markdown -> HTML (Plane description_html)
# --------------------------------------------------------------------------- #

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_HR_RE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")
_UL_RE = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_OL_RE = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")
_IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_CODE_FENCE_RE = re.compile(r"^```(\w*)\s*$")


def _inline_to_html(text: str) -> str:
    """Inline markdown (code/emphasis/links/images) -> inline HTML, escaped."""
    placeholders: list = []

    def stash(html_fragment: str) -> str:
        placeholders.append(html_fragment)
        return f"\x00{len(placeholders) - 1}\x00"

    def repl_code(match):
        return stash("<code>" + html_module.escape(match.group(2)) + "</code>")

    # inline code first (protect its content from emphasis parsing)
    text = re.sub(r"(`+)([^`]+)\1", repl_code, text)

    def repl_image(match):
        alt, src = match.group(1), match.group(2)
        return stash(f'<img src="{html_module.escape(src, quote=True)}" alt="{html_module.escape(alt, quote=True)}" />')

    def repl_link(match):
        label, href = match.group(1), match.group(2)
        return stash(f'<a href="{html_module.escape(href, quote=True)}">{html_module.escape(label)}</a>')

    text = _IMG_RE.sub(repl_image, text)
    text = _LINK_RE.sub(repl_link, text)

    text = html_module.escape(text)

    # unmatched-marker pairs applied greedily per pair; approximate is fine
    text = text.replace("**", "\x01").replace("__", "\x01")
    parts = text.split("\x01")
    for i in range(1, len(parts), 2):
        parts[i] = "<strong>" + parts[i] + "</strong>"
    text = "".join(parts)

    text = text.replace("*", "\x01").replace("_", "\x01")
    parts = text.split("\x01")
    for i in range(1, len(parts), 2):
        parts[i] = "<em>" + parts[i] + "</em>"
    text = "".join(parts)

    text = text.replace("~~", "\x01")
    parts = text.split("\x01")
    for i in range(1, len(parts), 2):
        parts[i] = "<s>" + parts[i] + "</s>"
    text = "".join(parts)

    # restore stashed HTML
    def unstash(match):
        return placeholders[int(match.group(1))]

    return re.sub(r"\x00(\d+)\x00", unstash, text)


def markdown_to_html(markdown: str) -> str:
    """Convert AFFiNE markdown export to Plane description_html."""
    if not markdown:
        return "<p></p>"
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    html_parts: list = []
    i = 0
    n = len(lines)

    def list_item(item_text, ordered, index):
        return f"<li>{_inline_to_html(item_text)}</li>"

    while i < n:
        line = lines[i]

        if not line.strip():
            i += 1
            continue

        fence = _CODE_FENCE_RE.match(line)
        if fence:
            lang = fence.group(1)
            code_lines = []
            i += 1
            while i < n and not _CODE_FENCE_RE.match(lines[i]):
                code_lines.append(lines[i])
                i += 1
            i += 1  # closing fence (or EOF)
            code_html = html_module.escape("\n".join(code_lines))
            html_parts.append(f"<pre><code>{code_html}</code></pre>")
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            html_parts.append(f"<h{level}>{_inline_to_html(heading.group(2).strip())}</h{level}>")
            i += 1
            continue

        if _HR_RE.match(line):
            html_parts.append("<hr />")
            i += 1
            continue

        quote_match = _QUOTE_RE.match(line)
        if quote_match:
            quote_lines = []
            while i < n and (m := _QUOTE_RE.match(lines[i])):
                quote_lines.append(m.group(1))
                i += 1
            html_parts.append("<blockquote>" + _inline_to_html(" ".join(quote_lines)) + "</blockquote>")
            continue

        ul = _UL_RE.match(line)
        ol = _OL_RE.match(line)
        if ul or ol:
            is_ordered = bool(ol)
            base_indent = len((ol or ul).group(1))
            items: list = []
            while i < n:
                m = _UL_RE.match(lines[i]) or _OL_RE.match(lines[i])
                if not m or (len(m.group(1)) < base_indent):
                    break
                ordered_now = bool(_OL_RE.match(lines[i]))
                if ordered_now != is_ordered and len(m.group(1)) == base_indent:
                    break
                indent = len(m.group(1))
                depth = (indent - base_indent) // 2
                items.append((depth, m.group(3)))
                i += 1
            tag = "ol" if is_ordered else "ul"
            html_parts.append(_render_nested_list(tag, items))

        if i < n and (ul or ol):
            continue

        # tables: | a | b | / | --- | --- | / rows
        if line.lstrip().startswith("|") and line.rstrip().endswith("|"):
            rows = []
            while i < n and lines[i].lstrip().startswith("|") and lines[i].rstrip().endswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            if rows:
                head = rows[0]
                thead = "<tr>" + "".join(f"<th>{_inline_to_html(c)}</th>" for c in head) + "</tr>"
                body_rows = "".join(
                    "<tr>" + "".join(f"<td>{_inline_to_html(c)}</td>" for c in row) + "</tr>" for row in rows[1:]
                )
                html_parts.append(f"<table><thead>{thead}</thead><tbody>{body_rows}</tbody></table>")
            continue

        # paragraph: join until blank line or another block start
        para = [line.strip()]
        i += 1
        while i < n and lines[i].strip() and not (
            _HEADING_RE.match(lines[i])
            or _HR_RE.match(lines[i])
            or _UL_RE.match(lines[i])
            or _OL_RE.match(lines[i])
            or _QUOTE_RE.match(lines[i])
            or _CODE_FENCE_RE.match(lines[i])
            or (lines[i].lstrip().startswith("|") and lines[i].rstrip().endswith("|"))
        ):
            para.append(lines[i].strip())
            i += 1
        html_parts.append("<p>" + _inline_to_html(" ".join(para)) + "</p>")

    return "".join(html_parts) or "<p></p>"


def _render_nested_list(tag: str, items) -> str:
    """Render [(depth, text)] into nested <ul>/<ol> html."""
    result: list = []
    stack: list = [0]  # depths of open lists

    def open_list(depth):
        result.append(f"<{tag}>")
        stack.append(depth)

    def close_to_depth(depth):
        while len(stack) > 1 and stack[-1] > depth:
            result.append(f"</{tag}>")
            stack.pop()

    for depth, text in items:
        depth = max(depth, 0)
        if depth > stack[-1]:
            # open child list under the previous item
            if result and result[-1].startswith("<li>"):
                prev = result.pop()
                result.append(prev[:-len("</li>")] if prev.endswith("</li>") else prev)
            open_list(depth)
        else:
            close_to_depth(depth)
        result.append(f"<li>{_inline_to_html(text)}</li>")
    close_to_depth(0)
    result.append(f"</{tag}>")
    return "".join(result)
