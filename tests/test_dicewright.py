import io
import json
import os
import random
import sys
import tempfile
import unittest
from fractions import Fraction

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import dicewright as dw  # noqa: E402


def run(argv):
    out = io.StringIO()
    code = dw.main(argv, out=out)
    return code, out.getvalue()


class ParseTests(unittest.TestCase):
    def test_basic_expression(self):
        e = dw.parse("3d6")
        self.assertEqual((e.count, e.sides, e.mod, e.keep, e.op), (3, 6, 0, None, None))

    def test_case_and_spacing_are_accepted(self):
        e = dw.parse("  3D6 + 2 ")
        self.assertEqual((e.count, e.sides, e.mod), (3, 6, 2))

    def test_implicit_count_defaults_to_one(self):
        self.assertEqual(dw.parse("d20").count, 1)

    def test_keep_highest_and_lowest(self):
        self.assertEqual(dw.parse("4d6kh3").keep, "h")
        e = dw.parse("2d20kl1")
        self.assertEqual((e.keep, e.keep_n), ("l", 1))

    def test_success_test_and_negative_modifier(self):
        e = dw.parse("5d10>=7-1")
        self.assertEqual((e.op, e.target, e.mod), (">=", 7, -1))

    def test_unexpected_text_reports_column(self):
        with self.assertRaises(dw.ExprError) as ctx:
            dw.parse("3d6x")
        self.assertEqual(ctx.exception.column, 4)

    def test_missing_sides_is_an_error_not_a_silent_drop(self):
        with self.assertRaises(dw.ExprError):
            dw.parse("3d")

    def test_keep_count_larger_than_dice_is_rejected(self):
        with self.assertRaisesRegex(dw.ExprError, "keep count 5"):
            dw.parse("4d6kh5")

    def test_zero_dice_and_one_sided_dice_are_rejected(self):
        with self.assertRaises(dw.ExprError):
            dw.parse("0d6")
        with self.assertRaises(dw.ExprError):
            dw.parse("3d1")

    def test_invalid_combinations_are_rejected(self):
        with self.assertRaises(dw.ExprError):
            dw.parse("4d6kh2>=3")
        with self.assertRaises(dw.ExprError):
            dw.parse("d6!>=3")

    def test_too_many_outcomes_is_rejected(self):
        with self.assertRaisesRegex(dw.ExprError, "too many outcomes"):
            dw.parse("100d1000")


class OddsTests(unittest.TestCase):
    def test_single_die_is_uniform(self):
        dist = dw.distribution(dw.parse("d6"))
        self.assertEqual(set(dist.values()), {Fraction(1, 6)})
        stats = dw.stats(dist)
        self.assertEqual(stats["mean"], Fraction(7, 2))
        self.assertEqual(stats["variance"], Fraction(35, 12))

    def test_two_dice_sum_seven(self):
        dist = dw.distribution(dw.parse("2d6"))
        self.assertEqual(dist[7], Fraction(6, 36))

    def test_keep_highest_mean_of_four_d6_drop_lowest(self):
        mean = dw.stats(dw.distribution(dw.parse("4d6kh3")))["mean"]
        self.assertAlmostEqual(float(mean), 12.2446, places=3)

    def test_keep_lowest_probability_of_one(self):
        # P(min of two d20 = 1) = (20^2 - 19^2) / 400 = 39/400
        dist = dw.distribution(dw.parse("2d20kl1"))
        self.assertEqual(dist[1], Fraction(39, 400))

    def test_success_count_mean(self):
        # Each d10 succeeds on 7+ with probability 4/10, so mean = 5 * 0.4 = 2
        self.assertEqual(dw.stats(dw.distribution(dw.parse("5d10>=7")))["mean"], 2)

    def test_modifier_shifts_every_outcome(self):
        plain = dw.distribution(dw.parse("2d6"))
        shifted = dw.distribution(dw.parse("2d6+3"))
        self.assertEqual(shifted[10], plain[7])
        self.assertEqual(min(shifted), 5)

    def test_exploding_die_sums_to_one_and_explodes_one_sixth(self):
        dist = dw.distribution(dw.parse("d6!"))
        self.assertEqual(sum(dist.values()), 1)
        self.assertEqual(dw.probability(dist, ">", 6), Fraction(1, 6))

    def test_probability_query(self):
        dist = dw.distribution(dw.parse("d20"))
        self.assertEqual(dw.probability(dist, ">=", 15), Fraction(6, 20))

    def test_percentiles_of_d6(self):
        dist = dw.distribution(dw.parse("d6"))
        self.assertEqual(dw.percentile(dist, 10), 1)
        self.assertEqual(dw.percentile(dist, 50), 3)
        self.assertEqual(dw.percentile(dist, 90), 6)

    def test_compare_two_d6(self):
        result = dw.compare(dw.distribution(dw.parse("d6")), dw.distribution(dw.parse("d6")))
        self.assertEqual(result["greater"], Fraction(15, 36))
        self.assertEqual(result["equal"], Fraction(6, 36))
        self.assertEqual(result["less"], Fraction(15, 36))

    def test_histogram_has_one_row_per_outcome(self):
        lines = dw.histogram_lines(dw.distribution(dw.parse("2d6")))
        self.assertEqual(len(lines), 11)


class RollTests(unittest.TestCase):
    def test_seeded_rolls_are_reproducible(self):
        expr = dw.parse("4d6kh3")
        first = [dw.roll_once(expr, random.Random(7)).total for _ in range(1)]
        second = [dw.roll_once(expr, random.Random(7)).total for _ in range(1)]
        self.assertEqual(first, second)

    def test_keep_total_matches_kept_faces(self):
        expr = dw.parse("4d6kh3")
        rng = random.Random(3)
        for _ in range(200):
            roll = dw.roll_once(expr, rng)
            self.assertEqual(roll.total, sum(roll.kept))
            self.assertEqual(len(roll.kept), 3)
            self.assertEqual(len(roll.dropped), 1)
            self.assertLessEqual(max(roll.dropped), min(roll.kept))

    def test_simulation_converges_to_exact_mean(self):
        counts = dw.simulate(dw.parse("2d6"), 20_000, seed=1)
        mean = sum(v * c for v, c in counts.items()) / 20_000
        self.assertAlmostEqual(mean, 7.0, delta=0.1)


class LintTests(unittest.TestCase):
    def test_lint_reports_line_numbers_and_columns(self):
        results = dw.lint_lines([
            "# comment\n",
            "attack: 1d20+5\n",
            "\n",
            "damage: 2d6x\n",
        ])
        self.assertEqual([r["line"] for r in results], [2, 4])
        self.assertIsNone(results[0]["error"])
        self.assertEqual(results[1]["column"], 12)  # the 'x' sits at column 12 of the line

    def test_check_command_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = os.path.join(tmp, "good.dice")
            bad = os.path.join(tmp, "bad.dice")
            with open(good, "w", encoding="utf-8") as f:
                f.write("d20\n")
            with open(bad, "w", encoding="utf-8") as f:
                f.write("d20\n3d\n")
            code, text = run(["check", good])
            self.assertEqual(code, 0)
            code, text = run(["check", bad])
            self.assertEqual(code, 1)
            self.assertIn(f"{bad}:2:", text)


class CliTests(unittest.TestCase):
    def test_odds_json_is_valid_and_exact(self):
        code, text = run(["odds", "2d6", "--test", ">=10", "--format", "json"])
        self.assertEqual(code, 0)
        doc = json.loads(text)
        self.assertEqual(doc["mean"], 7.0)
        self.assertEqual(doc["probability"]["exact"], str(Fraction(6, 36)))

    def test_odds_histogram_text(self):
        code, text = run(["odds", "d4", "--histogram"])
        self.assertEqual(code, 0)
        self.assertIn("Mean", text)
        self.assertIn("#", text)

    def test_bad_expression_exits_2(self):
        code, _ = run(["odds", "3d6x"])
        self.assertEqual(code, 2)

    def test_roll_with_seed_is_repeatable(self):
        _, first = run(["roll", "4d6kh3", "--seed", "42", "--count", "3"])
        _, second = run(["roll", "4d6kh3", "--seed", "42", "--count", "3"])
        self.assertEqual(first, second)
        self.assertEqual(first.count("\n"), 3)

    def test_compare_command(self):
        code, text = run(["compare", "d6", "d6"])
        self.assertEqual(code, 0)
        self.assertIn("P(A > B)  0.4167", text)


if __name__ == "__main__":
    unittest.main()
