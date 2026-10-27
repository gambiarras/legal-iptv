import argparse
import sys
import unittest
from unittest.mock import patch

from legal_iptv.cli import coverage_ratio, parse_args, positive_int
from legal_iptv.config import AppConfig


class CLITest(unittest.TestCase):
    def test_positive_int_accepts_positive_values(self):
        self.assertEqual(positive_int("10"), 10)

    def test_positive_int_rejects_zero_and_negative_values(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            positive_int("0")

        with self.assertRaises(argparse.ArgumentTypeError):
            positive_int("-1")

    def test_coverage_ratio_is_bounded(self):
        self.assertEqual(coverage_ratio("0.5"), 0.5)
        with self.assertRaises(argparse.ArgumentTypeError):
            coverage_ratio("0")
        with self.assertRaises(argparse.ArgumentTypeError):
            coverage_ratio("1.1")

    def test_parse_args_sets_epg_cache_defaults(self):
        with patch.object(sys, "argv", ["legal-iptv"]):
            args = parse_args()

        self.assertEqual(args.epg_cache_file, "epg-cache.json")
        self.assertEqual(args.epg_cache_ttl, 43200)
        self.assertFalse(args.refresh_epg_cache)
        self.assertEqual(args.iptv_org_cache_file, "iptv-org-cache.json")
        self.assertEqual(args.iptv_org_cache_ttl, 43200)
        self.assertFalse(args.refresh_iptv_org_cache)
        self.assertEqual(args.profile, "full")
        self.assertEqual(args.player_profile, "portable")
        self.assertEqual(args.guide_output, "guide.xml")
        self.assertFalse(args.skip_guide)

    def test_app_config_reads_epg_cache_args(self):
        with patch.object(
            sys,
            "argv",
            [
                "legal-iptv",
                "--epg-cache-file",
                "custom-epg-cache.json",
                "--epg-cache-ttl",
                "120",
                "--refresh-epg-cache",
                "--iptv-org-cache-file",
                "custom-iptv-org-cache.json",
                "--iptv-org-cache-ttl",
                "240",
                "--refresh-iptv-org-cache",
            ],
        ):
            config = AppConfig.from_args(parse_args())

        self.assertEqual(config.epg_cache_file.name, "custom-epg-cache.json")
        self.assertEqual(config.epg_cache_ttl_seconds, 120)
        self.assertTrue(config.refresh_epg_cache)
        self.assertEqual(config.iptv_org_cache_file.name, "custom-iptv-org-cache.json")
        self.assertEqual(config.iptv_org_cache_ttl_seconds, 240)
        self.assertTrue(config.refresh_iptv_org_cache)

    def test_adaptive_validation_defaults_and_overrides(self):
        with patch.object(sys, "argv", ["legal-iptv"]):
            config = AppConfig.from_args(parse_args())
        self.assertEqual(config.validation_max_success_age, 86400)
        self.assertEqual(config.validation_retry_delay, 900)
        self.assertFalse(config.force_validation)
        with patch.object(
            sys,
            "argv",
            [
                "legal-iptv",
                "--validate-streams",
                "--force-validation",
                "--validation-max-success-age",
                "43200",
                "--validation-retry-delay",
                "600",
            ],
        ):
            config = AppConfig.from_args(parse_args())
        self.assertEqual(config.validation_max_success_age, 43200)
        self.assertEqual(config.validation_retry_delay, 600)
        self.assertTrue(config.force_validation)

    def test_force_requires_validation_and_intervals_must_be_positive(self):
        for flags in (
            ["--force-validation"],
            ["--validation-max-success-age", "0"],
            ["--validation-retry-delay", "-1"],
        ):
            with (
                self.subTest(flags=flags),
                patch.object(sys, "argv", ["legal-iptv", *flags]),
                patch("sys.stderr"),
                self.assertRaises(SystemExit) as error,
            ):
                parse_args()
            self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
