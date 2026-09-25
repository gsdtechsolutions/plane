# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Unit tests for plane.affine_sync.converters (pure functions, no DB).

Run: apps/api/.venv/bin/python -m unittest test_converters_affine -v  (from apps/api/)
"""

import unittest

from plane.affine_sync.converters import html_to_markdown, markdown_to_html


class MarkdownToHtmlTests(unittest.TestCase):
    def test_heading(self):
        self.assertEqual(markdown_to_html("# Title"), "<h1>Title</h1>")

    def test_heading_levels(self):
        self.assertIn("<h3>deep</h3>", markdown_to_html("### deep"))

    def test_paragraph(self):
        self.assertEqual(markdown_to_html("hello world"), "<p>hello world</p>")

    def test_multiline_paragraph_joins(self):
        self.assertEqual(markdown_to_html("one\ntwo"), "<p>one two</p>")

    def test_unordered_list(self):
        self.assertEqual(markdown_to_html("- a\n- b"), "<ul><li>a</li><li>b</li></ul>")

    def test_nested_list(self):
        html = markdown_to_html("- a\n  - b\n- c")
        self.assertEqual(html, "<ul><li>a<ul><li>b</li></ul></li><li>c</li></ul>")

    def test_ordered_list(self):
        self.assertEqual(markdown_to_html("1. one\n2. two"), "<ol><li>one</li><li>two</li></ol>")

    def test_code_fence(self):
        self.assertEqual(markdown_to_html("```py\nx = 1\n```"), "<pre><code>x = 1</code></pre>")

    def test_code_escapes_html(self):
        html = markdown_to_html("```\n<script>alert(1)</script>\n```")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_blockquote(self):
        self.assertEqual(markdown_to_html("> quoted"), "<blockquote>quoted</blockquote>")

    def test_divider(self):
        self.assertEqual(markdown_to_html("---"), "<hr />")

    def test_table(self):
        html = markdown_to_html("| a | b |\n| --- | --- |\n| 1 | 2 |")
        self.assertIn("<table>", html)
        self.assertIn("<th>a</th>", html)
        self.assertIn("<td>1</td>", html)

    def test_inline_link(self):
        self.assertIn('<a href="https://x.y">text</a>', markdown_to_html("[text](https://x.y)"))

    def test_inline_bold_em(self):
        html = markdown_to_html("**bold** and *em*")
        self.assertIn("<strong>bold</strong>", html)
        self.assertIn("<em>em</em>", html)

    def test_inline_code_protects_content(self):
        html = markdown_to_html("use `**not bold**` here")
        self.assertIn("<code>**not bold**</code>", html)
        self.assertNotIn("<strong>", html)

    def test_image(self):
        html = markdown_to_html("![alt](https://img.png)")
        self.assertIn('<img src="https://img.png" alt="alt"', html)

    def test_escapes_inline_html(self):
        html = markdown_to_html("hello <b>world</b>")
        self.assertIn("&lt;b&gt;", html)
        self.assertNotIn("<b>world</b>", html)

    def test_empty(self):
        self.assertEqual(markdown_to_html(""), "<p></p>")
        self.assertEqual(markdown_to_html(None), "<p></p>")

    def test_complex_document(self):
        md = (
            "# Runbook\n\n"
            "Intro text.\n\n"
            "## Steps\n\n"
            "- build\n"
            "- test\n"
            "- ship\n\n"
            "> note\n\n"
            "```sql\nSELECT 1;\n```\n"
        )
        html = markdown_to_html(md)
        self.assertIn("<h1>Runbook</h1>", html)
        self.assertIn("<h2>Steps</h2>", html)
        self.assertIn("<ul><li>build</li><li>test</li><li>ship</li></ul>", html)
        self.assertIn("<blockquote>note</blockquote>", html)
        self.assertIn("SELECT 1;", html)


class HtmlToMarkdownTests(unittest.TestCase):
    def test_heading(self):
        self.assertIn("# Title", html_to_markdown("<h1>Title</h1>"))

    def test_paragraph(self):
        self.assertIn("hello", html_to_markdown("<p>hello</p>"))

    def test_list(self):
        md = html_to_markdown("<ul><li>a</li><li>b</li></ul>")
        self.assertIn("- a", md)
        self.assertIn("- b", md)

    def test_code_block(self):
        md = html_to_markdown("<pre><code>x = 1</code></pre>")
        self.assertIn("```", md)
        self.assertIn("x = 1", md)

    def test_bold_em_link(self):
        md = html_to_markdown("<p><strong>b</strong> <em>e</em> <a href=\"https://x.y\">l</a></p>")
        self.assertIn("**b**", md)
        self.assertIn("*e*", md)
        self.assertIn("[l](https://x.y)", md)

    def test_empty(self):
        self.assertEqual(html_to_markdown(""), "")
        self.assertEqual(html_to_markdown("<p></p>"), "")

    def test_round_trip_doc(self):
        md = "# Title\n\nA paragraph.\n\n- one\n- two\n"
        html = markdown_to_html(md)
        md2 = html_to_markdown(html)
        self.assertIn("# Title", md2)
        self.assertIn("A paragraph.", md2)
        self.assertIn("- one", md2)
        self.assertIn("- two", md2)


if __name__ == "__main__":
    unittest.main()
