from __future__ import annotations

import unittest

from src.resume.extract import latex_to_plain_text


class LatexToPlainTextTests(unittest.TestCase):
    def test_strips_comments_and_keeps_visible_text(self) -> None:
        source = r"""
\documentclass{article}
\begin{document}
% secret comment
\textbf{Foo} bar
\end{document}
"""
        text = latex_to_plain_text(source)
        self.assertIn("Foo", text)
        self.assertIn("bar", text)
        self.assertNotIn("secret comment", text)
        self.assertNotIn("textbf", text)


if __name__ == "__main__":
    unittest.main()
