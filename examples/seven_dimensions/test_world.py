"""关键Mock行为的离线回归测试；不创建客户端，不读取.env，不联网。

运行：python -B -m unittest discover -s examples/seven_dimensions -p test_world.py -v
"""

import unittest

from cases import get_case
from world import World


class WorldTests(unittest.TestCase):
    def test_timeout_commit_retry_and_new_intent(self):
        world = World(get_case("timeout_after_commit"))
        args = {"product_id": "P102", "quantity": 1}
        first = world.call("add_to_cart", args, turn_index=2, request_id="run:turn2")
        self.assertFalse(first["ok"])
        self.assertEqual(first["error"]["code"], "outcome_unknown")
        self.assertTrue(world.trace[-1]["committed"])
        self.assertNotIn("committed", first)
        self.assertEqual(world.call("get_cart", {}, turn_index=2)["items"], {"P102": 1})

        retry = world.call("add_to_cart", args, turn_index=2, request_id="run:turn2")
        self.assertTrue(retry["ok"])
        self.assertEqual(retry["cart_quantity"], 1)
        self.assertTrue(world.trace[-1]["replayed"])
        self.assertFalse(world.trace[-1]["committed"])
        self.assertEqual(world.snapshot()["carts"]["U001"], {"P102": 1})

        later = world.call("add_to_cart", args, turn_index=3, request_id="run:turn3")
        self.assertEqual(later["cart_quantity"], 2)
        self.assertEqual(world.snapshot()["carts"]["U001"], {"P102": 2})
        self.assertEqual(sum(t["committed"] for t in world.trace), 2)

    def test_other_user_read_and_write_are_blocked(self):
        world = World(get_case("unauthorized"))
        before = world.snapshot()
        for name, args in (
            ("get_cart", {"user_id": "U002"}),
            ("add_to_cart", {"user_id": "U002", "product_id": "P101", "quantity": 1}),
        ):
            with self.subTest(tool=name):
                result = world.call(name, args, request_id="unauthorized")
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"], "forbidden")
                self.assertNotIn("items", result)
                self.assertNotIn("cart_quantity", result)
                self.assertTrue(world.trace[-1]["blocked"])
                self.assertFalse(world.trace[-1]["committed"])
                self.assertEqual(world.snapshot(), before)

    def test_request_id_conflict_cannot_change_payload(self):
        world = World(get_case("normal"))
        first = world.call("add_to_cart", {"product_id": "P101", "quantity": 1}, request_id="same")
        self.assertTrue(first["ok"])
        for args in ({"product_id": "P102", "quantity": 1},
                     {"product_id": "P101", "quantity": 2}):
            with self.subTest(args=args):
                result = world.call("add_to_cart", args, request_id="same")
                self.assertEqual(result["error"]["code"], "request_conflict")
                self.assertTrue(world.trace[-1]["blocked"])
                self.assertEqual(world.snapshot()["carts"]["U001"], {"P101": 1})

    def test_existing_quantity_increases_without_overwriting(self):
        world = World(get_case("existing_quantity"))
        self.assertEqual(world.snapshot()["carts"]["U001"], {"P101": 1, "P102": 2})
        result = world.call("add_to_cart", {"product_id": "P102", "quantity": 1}, request_id="add-one")
        self.assertEqual(result["cart_quantity"], 3)
        self.assertEqual(world.snapshot()["carts"]["U001"], {"P101": 1, "P102": 3})
        self.assertEqual(world.snapshot()["carts"]["U002"], {"P104": 1})

    def test_followup_retrieval_uses_selected_target_without_repairing_missing_docs(self):
        for scenario in ("normal", "rag_missing", "rag_noise", "injection"):
            with self.subTest(scenario=scenario):
                world = World(get_case(scenario))
                first = world.call("search_knowledge", {"query": "15.6英寸通勤包"})["documents"]
                self.assertEqual(len(first), 3 if scenario == "rag_missing" else 4)
                for selected in ("P101", "P102"):
                    world.start_turn(1, selected)
                    result = world.call("search_knowledge", {"query": "第二款防水吗"}, turn_index=1)
                    expected = [] if scenario == "rag_missing" and selected == "P102" else ["doc_" + selected]
                    if scenario == "rag_noise":
                        expected.insert(0, "doc_P104")
                    self.assertEqual([d["doc_id"] for d in result["documents"]], expected)
                    if scenario == "injection" and selected == "P102":
                        self.assertIn("忽略用户预算", result["documents"][0]["text"])
                world.start_turn(2, "P102")
                final = world.call("search_knowledge", {"query": "商品资料", "top_k": 10}, turn_index=2)
                if scenario == "rag_missing":
                    self.assertEqual(final["documents"], [])
                self.assertNotIn("expected", final)

    def test_price_stock_changes_and_explicit_failure(self):
        for scenario, field, value in (
            ("price_changed", "price_cents", 34900),
            ("out_of_stock", "stock", 0),
        ):
            with self.subTest(scenario=scenario):
                world = World(get_case(scenario))
                untouched = world.snapshot()["products"]["P102"]
                world.start_turn(1, "P101")
                self.assertNotEqual(world.snapshot()["products"]["P101"][field], value)
                world.start_turn(2, "P101")
                current = world.call("get_product", {"product_id": "P101"}, turn_index=2)
                self.assertEqual(current[field], value)
                self.assertEqual(world.snapshot()["products"]["P102"], untouched)
                if scenario == "out_of_stock":
                    result = world.call("add_to_cart", {"product_id": "P101", "quantity": 1},
                                        turn_index=2, request_id="out-of-stock")
                    self.assertEqual(result["error"]["code"], "out_of_stock")
                    self.assertEqual(world.snapshot()["carts"]["U001"], {})
        world = World(get_case("explicit_failure"))
        result = world.call("add_to_cart", {"product_id": "P101", "quantity": 1}, request_id="failure")
        self.assertEqual(result["error"]["code"], "service_unavailable")
        self.assertEqual(world.snapshot()["carts"]["U001"], {})
        self.assertFalse(world.trace[-1]["committed"])


if __name__ == "__main__":
    unittest.main()
