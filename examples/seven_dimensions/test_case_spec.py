"""Test the public, pre-run case explanation without a model or credentials."""

from copy import deepcopy
import json
import unittest
from unittest import mock

from cases import CASES, get_case
from case_spec import build_case_spec
from world import BASE_DOCUMENTS, World


DIMENSION_KEYS = {
    "result", "retrieval", "evidence", "process", "reliability", "efficiency", "security",
}


class CaseSpecificationTests(unittest.TestCase):
    def setUp(self):
        # Looking at a specification is entirely local and should work even
        # when the private configuration and the model API are unavailable.
        self.addCleanup(mock.patch.stopall)
        mock.patch("client.load_local_env", side_effect=AssertionError(
            "Specifications must not read private configuration")).start()
        mock.patch("client.ArkClient", side_effect=AssertionError(
            "Specifications must not construct a model client")).start()
        mock.patch("urllib.request.urlopen", side_effect=AssertionError(
            "Specifications must not access the network")).start()

    def test_all_cases_have_readable_rounds_and_seven_dimensions(self):
        for case in CASES:
            with self.subTest(case_id=case["id"]):
                spec = build_case_spec(case)
                self.assertTrue(spec["purpose"])
                self.assertTrue(spec["scenario_note"])
                self.assertTrue(spec["setup"])
                self.assertTrue(spec["expected_outcome"])
                self.assertTrue(spec["expected_process"])
                self.assertTrue(spec["aggregation_rule"])
                self.assertEqual(case["turns"], [row["user"] for row in spec["turns"]])
                self.assertTrue(all(row["expected"] for row in spec["turns"]))
                self.assertEqual(DIMENSION_KEYS, {row["key"] for row in spec["dimensions"]})
                self.assertEqual(7, len(spec["dimensions"]))
                for dimension in spec["dimensions"]:
                    self.assertTrue(dimension["name"])
                    self.assertTrue(dimension["scope"])
                    for status in ("pass", "fail", "unknown", "na"):
                        self.assertIsInstance(dimension[status], list)
                        self.assertTrue(all(isinstance(text, str) and text.strip()
                                            for text in dimension[status]))
                # A specification is public JSON data, with no live object or
                # model client needed to serialize it.
                json.dumps(spec, ensure_ascii=False)

    def test_retrieval_gold_labels_match_case_and_document_sources(self):
        for case in CASES:
            with self.subTest(case_id=case["id"]):
                retrieval = build_case_spec(case)["retrieval"]
                applicable = case["expected"]["required_recommendations"] > 0
                self.assertEqual(applicable, retrieval["applicable"])
                docs = retrieval["related_docs"]
                expected_ids = case["expected"]["related_doc_ids"] if applicable else []
                self.assertEqual(expected_ids, [doc["doc_id"] for doc in docs])
                for doc in docs:
                    self.assertEqual(BASE_DOCUMENTS[doc["doc_id"]]["product_id"],
                                     doc["product_id"])
                    self.assertTrue(doc["title"])
                self.assertTrue(retrieval["pass_rule"])
                self.assertTrue(retrieval["note"])

    def test_specs_do_not_mutate_fixtures_or_share_mutable_results(self):
        original_cases = deepcopy(CASES)
        case = get_case("normal")
        original_case = deepcopy(case)
        original_state = World(case).snapshot()
        first = build_case_spec(case)
        first["dimensions"].clear()
        first["turns"].clear()
        again = build_case_spec(case)
        self.assertEqual(7, len(again["dimensions"]))
        self.assertEqual(3, len(again["turns"]))
        self.assertEqual(original_case, case)
        self.assertEqual(original_cases, CASES)
        self.assertEqual(original_state, World(case).snapshot())

    def test_displayed_initial_products_and_carts_match_the_reset_world(self):
        for case in CASES:
            with self.subTest(case_id=case["id"]):
                initial = World(case).snapshot()
                spec = build_case_spec(case)
                self.assertEqual(set(initial["products"]),
                                 {row["product_id"] for row in spec["products"]})
                for row in spec["products"]:
                    product = initial["products"][row["product_id"]]
                    for field in ("name", "price_cents", "stock", "max_laptop_inches"):
                        self.assertEqual(product[field], row[field])
                displayed_carts = {
                    row["user_id"]: {item["product_id"]: item["quantity"]
                                     for item in row["items"]}
                    for row in spec["initial_carts"]
                }
                self.assertEqual(initial["carts"], displayed_carts)

    def test_faults_do_not_change_gold_labels_or_pretend_to_be_initial_state(self):
        normal = build_case_spec(get_case("normal"))
        missing = build_case_spec(get_case("rag_missing"))
        self.assertEqual(normal["retrieval"]["related_docs"],
                         missing["retrieval"]["related_docs"])
        self.assertIn("doc_P102", [doc["doc_id"] for doc in missing["retrieval"]["related_docs"]])
        for scenario in ("price_changed", "out_of_stock"):
            with self.subTest(scenario=scenario):
                spec = build_case_spec(get_case(scenario))
                self.assertEqual(normal["products"], spec["products"])
                self.assertNotEqual(normal["scenario_note"], spec["scenario_note"])
        attack = get_case("injection")
        attack_spec = build_case_spec(attack)
        for doc in attack_spec["retrieval"]["related_docs"]:
            self.assertEqual(World(attack).documents[doc["doc_id"]]["text"], doc["text"])

    def test_normal_scope_does_not_claim_single_run_stability_or_attack_coverage(self):
        spec = build_case_spec(get_case("normal"))
        dimensions = {row["key"]: row for row in spec["dimensions"]}
        self.assertTrue(dimensions["efficiency"]["unknown"])
        self.assertTrue(dimensions["reliability"]["unknown"])
        self.assertTrue(dimensions["reliability"]["na"])
        security_na = " ".join(dimensions["security"]["na"])
        self.assertIn("权限", security_na)
        self.assertIn("注入", security_na)
        unauthorized = build_case_spec(get_case("unauthorized"))
        unauthorized_dimensions = {row["key"]: row for row in unauthorized["dimensions"]}
        for key in ("retrieval", "evidence"):
            self.assertEqual([], unauthorized_dimensions[key]["pass"])
            self.assertTrue(unauthorized_dimensions[key]["na"])

    def test_numeric_acceptance_criteria_follow_case_configuration(self):
        case = get_case("normal")
        case["expected"].update(budget_cents=49900, min_laptop_inches=17.3,
                                required_recommendations=3)
        spec = build_case_spec(case)
        explanation = json.dumps({"setup": spec["setup"],
                                  "expected_outcome": spec["expected_outcome"],
                                  "dimensions": spec["dimensions"]}, ensure_ascii=False)
        self.assertIn("499", explanation)
        self.assertIn("17.3", explanation)
        self.assertTrue("3款" in explanation or "3 款" in explanation)


if __name__ == "__main__":
    unittest.main(verbosity=2)
