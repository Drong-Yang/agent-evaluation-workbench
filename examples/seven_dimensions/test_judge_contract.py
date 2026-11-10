"""Judge contracts are trusted grading rules, never extra instructions to the Agent.

These tests execute the real Mock world and evaluator against scripted API replies.
They neither read local credentials nor make network requests.
"""

from copy import deepcopy
import json
import unittest
from unittest import mock

import client as client_module
from cases import CASES, get_case
from evaluate import evaluate
import run as runner
from test_runner import ScriptedClient, final_message, tool_message
from world import World


def judge_message(status="pass"):
    return {"role": "assistant", "content": json.dumps({
        key: {"status": status, "reason": "按本次验收约定核对证据。"}
        for key in ("evidence", "truthfulness", "instruction_following")
    }, ensure_ascii=False)}


def price_change_script(*, add_anyway=False, judge_reply=None):
    recommendations = [
        {"product_id": "P101", "reason": "满足条件", "evidence_ids": ["doc_P101"]},
        {"product_id": "P102", "reason": "满足条件", "evidence_ids": ["doc_P102"]},
    ]
    messages = [
        tool_message(("search_knowledge", {"query": "通勤双肩包", "top_k": 4})),
        tool_message(("get_product", {"product_id": "P101"}),
                     ("get_product", {"product_id": "P102"})),
        final_message("按下面的列表推荐两款。", "recommendation", recommendations),
        final_message("第二款是防泼水，不能保证暴雨中电脑不进水。", selected="P102"),
        tool_message(("get_product", {"product_id": "P102"})),
    ]
    if add_anyway:
        messages.extend([
            tool_message(("add_to_cart", {"product_id": "P102", "quantity": 1})),
            final_message("已将第二款增加一个。", "added", selected="P102"),
        ])
    else:
        # No alternative product and no request to increase the budget: neither
        # was required to handle this price change correctly.
        messages.append(final_message(
            "第二款当前涨价到349元，超过300元预算，尚未加购。",
            "not_added", selected="P102"))
    messages.append(judge_reply if judge_reply is not None else judge_message())
    return messages


class JudgeContractTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(client_module, "load_local_env", side_effect=AssertionError(
            "Tests must not read the local .env")).start()
        mock.patch.object(client_module.urllib.request.OpenerDirector, "open",
                          side_effect=AssertionError("Tests must not use the network")).start()
        mock.patch.object(runner, "pricing_from_env", return_value=None).start()

    def run_price_change(self, *, add_anyway=False, judge_reply=None):
        client = ScriptedClient(price_change_script(
            add_anyway=add_anyway, judge_reply=judge_reply))
        record = runner.run_live(get_case("price_changed"), client, max_steps=8,
                                 use_judge=True, repeat_index=0)
        self.assertIsNone(record["error"])
        self.assertIsNone(record["judge_error"])
        dimensions = {item["key"]: item for item in evaluate(record)}
        return record, dimensions, client

    def test_correct_price_change_stop_passes_result_and_process(self):
        record, dimensions, _ = self.run_price_change()
        self.assertEqual("pass", dimensions["result"]["status"])
        self.assertEqual("pass", dimensions["process"]["status"])
        self.assertEqual(record["initial_state"]["carts"], record["final_state"]["carts"])
        self.assertEqual("not_added", record["turns"][-1]["parsed"]["action_status"])
        self.assertFalse(any(entry["tool"] == "add_to_cart" for entry in record["trace"]))
        latest_query = record["trace"][-1]
        self.assertEqual(2, latest_query["turn_index"])
        self.assertEqual(34900, latest_query["result"]["price_cents"])

    def test_favorable_judge_cannot_override_actual_over_budget_add(self):
        record, dimensions, _ = self.run_price_change(add_anyway=True)
        self.assertEqual("pass", record["judge"]["instruction_following"]["status"])
        self.assertEqual({"P102": 1}, record["final_state"]["carts"]["U001"])
        self.assertEqual("fail", dimensions["result"]["status"])
        self.assertEqual("fail", dimensions["process"]["status"])
        self.assertTrue(any(check["status"] == "fail" and "加购资格" in check["name"]
                            for check in dimensions["process"]["checks"]))

    def test_contract_reaches_only_the_judge_as_separate_trusted_message(self):
        record, _, client = self.run_price_change()
        calls = client.seen
        judge_calls = [call for call in calls if call["options"].get("purpose") == "judge"]
        agent_calls = [call for call in calls if call["options"].get("purpose", "agent") == "agent"]
        self.assertEqual(1, len(judge_calls))
        judge_messages = judge_calls[0]["messages"]
        self.assertEqual(["system", "system", "user"],
                         [message["role"] for message in judge_messages])
        self.assertEqual(runner.JUDGE_SYSTEM, judge_messages[0]["content"])
        contract = runner.build_judge_contract(record)
        self.assertEqual(contract, record["judge_contract"])
        contract_prompt = judge_messages[1]["content"]
        self.assertIn("requirements", contract_prompt)
        self.assertIn("optional_behaviors", contract_prompt)
        self.assertIn(str(record["expected"]["budget_cents"]), contract_prompt)
        self.assertIn(record["scenario"], contract_prompt)
        self.assertIn(contract["scenario_rule"], contract_prompt)
        alternatives = [rule for rule in contract["optional_behaviors"] if "替代商品" in rule]
        self.assertTrue(alternatives)
        self.assertTrue(any("可选" in rule and "不能" in rule for rule in alternatives))
        self.assertTrue(any("预算" in rule or "新条件" in rule
                            for rule in contract["optional_behaviors"]))
        # The judge's evidence payload remains an untrusted user message, not a
        # place where an answer can replace the independently compiled contract.
        evidence = json.loads(judge_messages[2]["content"])
        self.assertIn("trace", evidence)
        self.assertNotIn("expected", evidence)
        self.assertNotIn("judge_contract", evidence)
        for call in agent_calls:
            self.assertEqual(1, sum(message["role"] == "system" for message in call["messages"]))
            serialized = json.dumps(call["messages"], ensure_ascii=False)
            for private_field in ("judge_contract", "optional_behaviors", "scenario_rule", "should_add"):
                self.assertNotIn(private_field, serialized)

    def test_every_case_contract_uses_record_requirements_without_mutating_them(self):
        mappings = {
            "required_recommendations": "required_recommendations",
            "budget_cents": "budget_cents",
            "min_laptop_inches": "min_laptop_inches",
            "current_user_id": "current_user_id",
            "expected_cart_delta": "cart_delta",
            "should_add": "should_add",
        }
        for case_data in CASES:
            with self.subTest(case=case_data["id"]):
                case = get_case(case_data["id"])
                record = runner.new_record(case, World(case), "live", "unit-test-model", 0)
                before = deepcopy(record)
                contract = runner.build_judge_contract(record)
                self.assertEqual(before, record)
                self.assertEqual(record["scenario"], contract["scenario"])
                for contract_field, expected_field in mappings.items():
                    self.assertEqual(record["expected"][expected_field],
                                     contract["requirements"][contract_field])
                self.assertTrue(contract["selection_rule"])
                self.assertTrue(contract["business_rules"])
                self.assertTrue(contract["scenario_rule"])
                self.assertTrue(contract["optional_behaviors"])
                self.assertTrue(contract["scope"])
                self.assertTrue(all(isinstance(rule, str) for rule in contract["business_rules"]))
                self.assertTrue(all(isinstance(rule, str) for rule in contract["optional_behaviors"]))
        # Do not silently hardcode the original 300-yuan example into the
        # compiler when the same scenario's business parameters change.
        record["expected"]["budget_cents"] = 28765
        record["expected"]["min_laptop_inches"] = 14.2
        contract = runner.build_judge_contract(record)
        self.assertEqual(28765, contract["requirements"]["budget_cents"])
        self.assertEqual(14.2, contract["requirements"]["min_laptop_inches"])

    def test_missing_semantic_judgments_stay_unknown_for_a_correct_stop(self):
        record, dimensions, _ = self.run_price_change(judge_reply={
            "role": "assistant", "content": "{}"})
        self.assertEqual("unknown", record["judge"]["instruction_following"]["status"])
        self.assertEqual("unknown", dimensions["process"]["status"])
        self.assertEqual("unknown", dimensions["result"]["status"])
        self.assertFalse(any(check["status"] == "fail" for check in dimensions["process"]["checks"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
