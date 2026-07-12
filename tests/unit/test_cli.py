import argparse
import unittest

from legal_iptv.cli import positive_int


class CLITest(unittest.TestCase):
    def test_positive_int_accepts_positive_values(self):
        self.assertEqual(positive_int("10"), 10)

    def test_positive_int_rejects_zero_and_negative_values(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            positive_int("0")

        with self.assertRaises(argparse.ArgumentTypeError):
            positive_int("-1")


if __name__ == "__main__":
    unittest.main()
