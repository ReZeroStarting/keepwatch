"""Smoke test: the package imports, exposes a version, and the parser builds."""

import unittest

import keepwatch
from keepwatch.cli import build_parser


class TestPackage(unittest.TestCase):
    def test_version_is_a_string(self):
        self.assertIsInstance(keepwatch.__version__, str)
        self.assertTrue(keepwatch.__version__)

    def test_cli_parser_builds(self):
        self.assertEqual(build_parser().prog, "keepwatch")


if __name__ == "__main__":
    unittest.main()
