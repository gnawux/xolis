import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "tools" / "xolis_aws_cost.py"
SPEC = importlib.util.spec_from_file_location("xolis_aws_cost", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FakeAws:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def run_json(self, arguments):
        self.calls.append(arguments)
        return next(self.responses)


class AuthenticationTests(unittest.TestCase):
    def test_auto_login_retries_after_authentication_failure(self) -> None:
        aws = mock.Mock()
        aws.run_json.side_effect = [
            MODULE.AwsCommandError("expired"),
            {"Account": "123456789012"},
        ]

        identity = MODULE.authenticate(aws, "auto")

        self.assertEqual(identity["Account"], "123456789012")
        aws.login.assert_called_once_with()
        self.assertEqual(aws.run_json.call_count, 2)

    def test_never_mode_does_not_start_login(self) -> None:
        aws = mock.Mock()
        aws.run_json.side_effect = MODULE.AwsCommandError("expired")

        with self.assertRaisesRegex(MODULE.AwsCommandError, "expired"):
            MODULE.authenticate(aws, "never")

        aws.login.assert_not_called()


class SnapshotTests(unittest.TestCase):
    def test_snapshot_block_pages_are_summed(self) -> None:
        aws = FakeAws(
            [
                {
                    "BlockSize": 524288,
                    "Blocks": [{"BlockIndex": 0}, {"BlockIndex": 1}],
                    "NextToken": "next-page",
                },
                {"BlockSize": 524288, "Blocks": [{"BlockIndex": 2}]},
            ]
        )

        size = MODULE.snapshot_allocated_bytes(aws, "snap-0123456789")

        self.assertEqual(size, 3 * 524288)
        self.assertNotIn("--next-token", aws.calls[0])
        self.assertEqual(aws.calls[1][-2:], ["--next-token", "next-page"])

    def test_snapshot_block_size_must_be_consistent(self) -> None:
        aws = FakeAws(
            [
                {"BlockSize": 524288, "Blocks": [], "NextToken": "next-page"},
                {"BlockSize": 1048576, "Blocks": []},
            ]
        )

        with self.assertRaisesRegex(MODULE.AwsCommandError, "changed block size"):
            MODULE.snapshot_allocated_bytes(aws, "snap-0123456789")


class PricingTests(unittest.TestCase):
    def test_price_per_unit_selects_lowest_zero_range_price(self) -> None:
        response = {
            "PriceList": [
                json.dumps(
                    {
                        "terms": {
                            "OnDemand": {
                                "term": {
                                    "priceDimensions": {
                                        "tier": {
                                            "unit": "GB-Mo",
                                            "beginRange": "0",
                                            "pricePerUnit": {"USD": "0.05"},
                                        },
                                        "later-tier": {
                                            "unit": "GB-Mo",
                                            "beginRange": "50",
                                            "pricePerUnit": {"USD": "0.01"},
                                        },
                                    }
                                }
                            }
                        }
                    }
                ),
                {
                    "terms": {
                        "OnDemand": {
                            "term": {
                                "priceDimensions": {
                                    "price": {
                                        "unit": "GB-Mo",
                                        "beginRange": "0",
                                        "pricePerUnit": {"USD": "0.025"},
                                    }
                                }
                            }
                        }
                    }
                },
            ]
        }

        self.assertEqual(MODULE.price_per_unit(response, "GB-Mo"), 0.025)

    def test_price_per_unit_can_exclude_snapshot_archive_price(self) -> None:
        def product(usage_type, price):
            return {
                "product": {"attributes": {"usagetype": usage_type}},
                "terms": {
                    "OnDemand": {
                        "term": {
                            "priceDimensions": {
                                "price": {
                                    "unit": "GB-Mo",
                                    "beginRange": "0",
                                    "pricePerUnit": {"USD": price},
                                }
                            }
                        }
                    }
                },
            }

        response = {
            "PriceList": [
                product("APN1-EBS:SnapshotArchiveStorage", "0.0125"),
                product("APN1-EBS:SnapshotUsage", "0.05"),
            ]
        }

        self.assertEqual(
            MODULE.price_per_unit(
                response, "GB-Mo", usage_type_suffix="EBS:SnapshotUsage"
            ),
            0.05,
        )

    def test_monthly_and_daily_cost_uses_gib_and_annual_average(self) -> None:
        monthly, daily = MODULE.monthly_and_daily_cost(
            10 * MODULE.BYTES_PER_GIB, 0.05
        )

        self.assertEqual(monthly, 0.5)
        self.assertAlmostEqual(daily, 6 / 365)


class StorageAggregationTests(unittest.TestCase):
    def test_ecr_aggregates_only_prefixed_repositories(self) -> None:
        aws = FakeAws(
            [
                {
                    "repositories": [
                        {"repositoryName": "xolis/api"},
                        {"repositoryName": "unrelated/app"},
                    ]
                },
                {
                    "imageDetails": [
                        {"imageSizeInBytes": 100},
                        {"imageSizeInBytes": 200},
                    ]
                },
            ]
        )

        result = MODULE.collect_ecr(aws, "xolis")

        self.assertEqual(result["repository_count"], 1)
        self.assertEqual(result["image_count"], 2)
        self.assertEqual(result["logical_bytes"], 300)


if __name__ == "__main__":
    unittest.main()
