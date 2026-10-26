"""`--json` must work after the sub-command, but never be stolen from a wrapped command."""

import unittest

from keepwatch.cli import normalise


class TestArgvNormalise(unittest.TestCase):
    def test_global_position_is_untouched(self):
        argv = ["--root", "r", "--json", "check", "--who", "a"]
        self.assertEqual(normalise(argv), argv)

    def test_after_the_subcommand_moves_to_the_front(self):
        got = normalise(["--root", "r", "check", "--who", "a", "--json"])
        self.assertEqual(got[0], "--json")
        self.assertIn("check", got)
        self.assertNotIn("--json", got[1:])

    def test_never_touches_the_wrapped_command(self):
        argv = ["run", "--who", "a", "--", "tool", "--json"]
        self.assertEqual(normalise(argv), argv)

    def test_moves_only_before_the_separator(self):
        argv = ["check", "--json", "--who", "a", "--", "cmd", "--json"]
        got = normalise(argv)
        self.assertEqual(got[0], "--json")
        self.assertEqual(got[-1], "--json", "the wrapped command keeps its own flag")
        self.assertEqual(got.count("--json"), 2)


if __name__ == "__main__":
    unittest.main()
