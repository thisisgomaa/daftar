"""Tests for markdown HTML conversion and fence-aware splitter."""

from __future__ import annotations

import unittest

from daftar.format import md_to_html, split_message


class TestFormat(unittest.TestCase):
    def test_bold_italic_code_link(self) -> None:
        html = md_to_html("Hello **bold** and *italic* and `code` and [x](https://ex.com)")
        self.assertIn("<b>bold</b>", html)
        self.assertIn("<i>italic</i>", html)
        self.assertIn("<code>code</code>", html)
        self.assertIn('<a href="https://ex.com">x</a>', html)

    def test_escape_other(self) -> None:
        html = md_to_html("a < b & c > d")
        self.assertIn("&lt;", html)
        self.assertIn("&amp;", html)
        self.assertIn("&gt;", html)

    def test_splitter_never_breaks_fences(self) -> None:
        fence_body = "\n".join(f"line_{i} = {i}" for i in range(200))
        text = "intro\n\n```python\n" + fence_body + "\n```\n\noutro " + ("x" * 100)
        chunks = split_message(text, limit=500)
        self.assertTrue(chunks)
        # Every chunk that contains an opening fence must contain a closing one
        for ch in chunks:
            if "```" in ch:
                self.assertEqual(ch.count("```") % 2, 0, msg=repr(ch[:80]))

    def test_short_message_unsplit(self) -> None:
        self.assertEqual(split_message("hi"), ["hi"])


if __name__ == "__main__":
    unittest.main()
