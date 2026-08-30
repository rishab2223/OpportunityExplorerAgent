from __future__ import annotations

import unittest

from src.agent.filenames import sanitize_filename_part, unique_tex_name


class SanitizeFilenameTests(unittest.TestCase):
    def test_replaces_illegal_windows_chars(self) -> None:
        self.assertEqual(
            sanitize_filename_part(r'Acme/Corp: *?"<>|'),
            "Acme_Corp",
        )

    def test_collapses_spaces(self) -> None:
        self.assertEqual(
            sanitize_filename_part("Nice  Backyard"),
            "Nice_Backyard",
        )

    def test_empty_becomes_unknown(self) -> None:
        self.assertEqual(sanitize_filename_part(""), "unknown")
        self.assertEqual(sanitize_filename_part("   "), "unknown")
        self.assertEqual(sanitize_filename_part("..."), "unknown")


class UniqueTexNameTests(unittest.TestCase):
    def test_first_name_is_company_title(self) -> None:
        used: set[str] = set()
        name = unique_tex_name("Acme", "Software Engineer", "job-1", used)
        self.assertEqual(name, "Acme_Software_Engineer.tex")
        self.assertIn(name, used)

    def test_collision_appends_job_id(self) -> None:
        used: set[str] = set()
        first = unique_tex_name("Acme", "Engineer", "abc/123", used)
        second = unique_tex_name("Acme", "Engineer", "abc/123", used)
        self.assertEqual(first, "Acme_Engineer.tex")
        self.assertEqual(second, "Acme_Engineer_abc_123.tex")

    def test_second_collision_appends_counter(self) -> None:
        used: set[str] = set()
        unique_tex_name("Acme", "Engineer", "id1", used)
        unique_tex_name("Acme", "Engineer", "id1", used)
        third = unique_tex_name("Acme", "Engineer", "id1", used)
        self.assertEqual(third, "Acme_Engineer_id1_2.tex")


if __name__ == "__main__":
    unittest.main()
