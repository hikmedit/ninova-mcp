from __future__ import annotations

import unittest

from ninova_mcp.obs import GRADE_POINTS
from ninova_mcp.prereq import (
    Atom,
    PrerequisiteParseError,
    base_code,
    evaluate,
    iter_atoms,
    normalize_code,
    parse,
)


class CodeNormalizationTests(unittest.TestCase):
    def test_normalize_inserts_a_single_space(self):
        self.assertEqual(normalize_code("YZV201E"), "YZV 201E")
        self.assertEqual(normalize_code("yzv  201e"), "YZV 201E")
        self.assertEqual(normalize_code("FIZ 101EL"), "FIZ 101EL")

    def test_base_code_collapses_the_english_variant(self):
        self.assertEqual(base_code("BBF 201E"), "BBF 201")
        self.assertEqual(base_code("BBF 201"), "BBF 201")
        self.assertEqual(base_code("YZV 4901E"), "YZV 4901")

    def test_base_code_maps_english_lab_onto_the_lab(self):
        self.assertEqual(base_code("FIZ 101EL"), "FIZ 101L")
        self.assertEqual(base_code("FIZ 101L"), "FIZ 101L")

    def test_trailing_a_is_a_section_marker_not_a_language_marker(self):
        self.assertEqual(base_code("ING 112A"), "ING 112A")
        self.assertEqual(base_code("ING 201A"), "ING 201A")


class ParserTests(unittest.TestCase):
    def test_single_atom(self):
        tree = parse("BLG 454E MIN. DD")
        self.assertIsInstance(tree, Atom)
        self.assertEqual((tree.code, tree.min_grade), ("BLG 454E", "DD"))

    def test_and_binds_tighter_than_or(self):
        tree = parse("BLG 101 MIN. DD Ve BLG 102 MIN. DD Veya BLG 103 MIN. DD")
        self.assertEqual(tree.operator, "or")
        self.assertEqual(tree.children[0].operator, "and")

    def test_parentheses_override_precedence(self):
        tree = parse("( BLG 101 MIN. DD Veya BLG 102 MIN. DD ) Ve BLG 103 MIN. DD")
        self.assertEqual(tree.operator, "and")
        self.assertEqual(tree.children[0].operator, "or")

    def test_nested_real_expression(self):
        expression = (
            "( ( BLG 252 MIN. DD Veya BLG 252E MIN. DD ) Ve "
            "( MAT 281 MIN. DD Veya MAT 281E MIN. DD ) ) Veya "
            "( YZV 212E MIN. DD Ve ( YZV 201 MIN. DD Veya YZV 201E MIN. DD ) )"
        )
        atoms = {atom.code for atom in iter_atoms(parse(expression))}
        self.assertEqual(
            atoms,
            {"BLG 252", "BLG 252E", "MAT 281", "MAT 281E", "YZV 212E", "YZV 201", "YZV 201E"},
        )

    def test_empty_expression_is_none(self):
        self.assertIsNone(parse(""))
        self.assertIsNone(parse("   "))

    def test_unbalanced_parenthesis_is_rejected(self):
        with self.assertRaises(PrerequisiteParseError):
            parse("( BLG 101 MIN. DD Ve BLG 102 MIN. DD")

    def test_garbage_is_rejected(self):
        with self.assertRaises(PrerequisiteParseError):
            parse("this is not an expression")


class EvaluationTests(unittest.TestCase):
    def test_no_prerequisite_is_satisfied(self):
        self.assertTrue(evaluate(None, {}, GRADE_POINTS)["satisfied"])

    def test_or_needs_only_one_branch(self):
        tree = parse("BLG 252 MIN. DD Veya MUH 212 MIN. DD")
        verdict = evaluate(tree, {"BLG 252": 1.0}, GRADE_POINTS)
        self.assertTrue(verdict["satisfied"])
        self.assertEqual(verdict["missing"], [])

    def test_and_needs_every_branch(self):
        tree = parse("BLG 252 MIN. DD Ve MAT 281 MIN. DD")
        verdict = evaluate(tree, {"BLG 252": 1.0}, GRADE_POINTS)
        self.assertFalse(verdict["satisfied"])
        self.assertEqual([m["code"] for m in verdict["missing"]], ["MAT 281"])

    def test_grade_below_the_minimum_does_not_satisfy(self):
        tree = parse("YZV 4901E MIN. BB")
        self.assertFalse(evaluate(tree, {"YZV 4901": 2.0}, GRADE_POINTS)["satisfied"])
        self.assertTrue(evaluate(tree, {"YZV 4901": 3.0}, GRADE_POINTS)["satisfied"])

    def test_english_variant_satisfies_the_turkish_atom(self):
        # The student's BBF 201E is stored collapsed as BBF 201.
        tree = parse("BBF 201E MIN. DD")
        self.assertTrue(evaluate(tree, {"BBF 201": 2.0}, GRADE_POINTS)["satisfied"])

    def test_unmet_or_reports_every_branch_as_missing(self):
        tree = parse("BLG 252 MIN. DD Veya MUH 212 MIN. DD")
        verdict = evaluate(tree, {}, GRADE_POINTS)
        self.assertFalse(verdict["satisfied"])
        self.assertEqual(len(verdict["missing"]), 2)

    def test_satisfied_and_reports_no_missing_branches(self):
        tree = parse("BLG 252 MIN. DD Ve MAT 281 MIN. DD")
        verdict = evaluate(tree, {"BLG 252": 1.0, "MAT 281": 2.5}, GRADE_POINTS)
        self.assertTrue(verdict["satisfied"])
        self.assertEqual(verdict["missing"], [])
        self.assertEqual(len(verdict["satisfied_by"]), 2)


if __name__ == "__main__":
    unittest.main()
