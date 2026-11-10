"""Offline shopping-agent evaluation with mock products and a mock cart."""

import argparse
import copy
import json
import sys
import time
from pathlib import Path


class ShoppingWorld:
    """The tool layer owns cart state and records every observable call."""

    def __init__(self, fixture, user_id):
        self.data = copy.deepcopy(fixture)
        self.user_id = user_id
        self.trace = []
        self.processed_requests = {}

    def record(self, tool, args, result):
        self.trace.append({"tool": tool, "args": args, "result": copy.deepcopy(result)})
        return copy.deepcopy(result)

    def get_product(self, product_id):
        product = self.data["products"].get(product_id)
        result = {"ok": True, **product} if product else {"ok": False, "error": "not_found"}
        return self.record("get_product", {"product_id": product_id}, result)

    def add_to_cart(self, product_id, quantity, request_id):
        args = {"product_id": product_id, "quantity": quantity, "request_id": request_id}
        previous = self.processed_requests.get(request_id)
        product = self.data["products"].get(product_id)
        if previous:
            if previous["args"] != args:
                result = {"ok": False, "error": "request_id_conflict"}
            else:
                result = {**previous["result"], "replayed": True}
        elif type(quantity) is not int or quantity <= 0 or not request_id:
            result = {"ok": False, "error": "invalid_request"}
        elif not product or product["stock"] < self.data["cart"].get(product_id, 0) + quantity:
            result = {"ok": False, "error": "unavailable", "added": False}
        elif self.data["add_mode"] == "explicit_failure":
            result = {"ok": False, "error": "service_unavailable", "added": False}
        else:
            self.data["cart"][product_id] = self.data["cart"].get(product_id, 0) + quantity
            result = {"ok": True, "cart_quantity": self.data["cart"][product_id]}
            self.processed_requests[request_id] = {"args": copy.deepcopy(args), "result": result}
        return self.record("add_to_cart", args, result)


def run_agent(agent_input, world, version):
    """A deterministic mock; recommendation history and selection are prepared inputs.

    Expected answers are never passed to this function.
    """
    candidates = agent_input["recommendations"]
    position = agent_input["selected_position"]
    if type(position) is not int or not 1 <= position <= len(candidates):
        return {"status": "need_selection", "message": "请提供候选商品或明确要选哪款。"}
    product_id = candidates[position - 1]
    quantity = agent_input["quantity"]
    product = world.get_product(product_id)
    if not product["ok"] or product["stock"] < quantity:
        return {"status": "unavailable", "product_id": product_id,
                "message": "所选商品当前不可用或库存不足，请重新选择。"}
    if product["price_cents"] > agent_input["max_price_cents"]:
        return {"status": "needs_confirmation", "product_id": product_id,
                "message": "所选商品当前价格超过预算，请确认是否调整选择。"}
    result = world.add_to_cart(product_id, quantity, agent_input["request_id"])
    if version == "fixed" and not result["ok"]:
        return {"status": "failed", "product_id": product_id,
                "message": "服务明确返回失败，本次未加入购物车。"}
    # The baseline bug: it claims success even if the service explicitly failed.
    return {"status": "added", "product_id": product_id, "quantity": quantity,
            "message": f"已将 {product_id} 加入购物车，增加 {quantity} 件。"}


def check_write_preconditions(case, trace):
    known_products = {}
    successful_requests = {}
    target = case["expected"]["selected_product_id"]
    agent_input = case["input"]
    for event in trace:
        args, result = event["args"], event["result"]
        product_id = args["product_id"]
        if event["tool"] == "get_product":
            known_products[product_id] = result
        elif event["tool"] == "add_to_cart":
            if (product_id != target or args["quantity"] != agent_input["quantity"]
                    or args["request_id"] != agent_input["request_id"]):
                return False
            previous = successful_requests.get(args["request_id"])
            if previous:
                if previous != args:
                    return False
                continue
            known = known_products.get(product_id, {})
            if (not known.get("ok") or known["stock"] < args["quantity"]
                    or known["price_cents"] > agent_input["max_price_cents"]):
                return False
            if result.get("ok"):
                successful_requests[args["request_id"]] = args
    return True


def trace_matches_cart(before, after, trace):
    projected = copy.deepcopy(before)
    applied = {}
    for event in trace:
        if event["tool"] != "add_to_cart" or not event["result"].get("ok"):
            continue
        args = event["args"]
        request_id = args["request_id"]
        if request_id in applied:
            if applied[request_id] != args:
                return False
            continue
        applied[request_id] = args
        product_id = args["product_id"]
        projected[product_id] = projected.get(product_id, 0) + args["quantity"]
    return projected == after


def grade(case, response, world):
    """Check structured results and state; natural-language meaning is reviewed separately."""
    expected, agent_input = case["expected"], case["input"]
    before, after = case["world"]["cart"], world.data["cart"]
    target = expected["selected_product_id"]
    confirmed = any(
        event["tool"] == "add_to_cart" and event["result"].get("ok")
        and event["args"] == {"product_id": target, "quantity": agent_input["quantity"],
                              "request_id": agent_input["request_id"]}
        for event in world.trace)
    return {
        "response_status": response.get("status") == expected["response_status"],
        "selected_product": response.get("product_id") == target,
        "cart_state": after == expected["cart_after"],
        "unrelated_items_preserved": all(after.get(pid) == qty for pid, qty in before.items()
                                         if pid != target),
        "write_preconditions": check_write_preconditions(case, world.trace),
        "trace_matches_cart": trace_matches_cart(before, after, world.trace),
        "reported_addition_confirmed": response.get("status") != "added" or (
            confirmed and response.get("quantity") == agent_input["quantity"]),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", choices=["baseline", "fixed"], required=True)
    args = parser.parse_args()
    cases = json.loads(Path(__file__).with_name("cases.json").read_text(encoding="utf-8"))
    results = []
    for case in cases:
        world = ShoppingWorld(case["world"], case["input"]["user_id"])
        started = time.perf_counter()
        response = run_agent(copy.deepcopy(case["input"]), world, args.version)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        checks = grade(case, response, world)
        passed = all(checks.values())
        results.append({
            "id": case["id"], "category": case["category"], "rules_passed": passed,
            "checks": checks, "semantic_review": "pending", "response": response,
            "trace": world.trace, "cart_user_id": world.user_id,
            "cart_before": case["world"]["cart"], "cart_after": world.data["cart"],
            "cart_changed": case["world"]["cart"] != world.data["cart"],
            "elapsed_ms": elapsed_ms, "tool_calls": len(world.trace),
        })
        failed = [name for name, ok in checks.items() if not ok]
        print(f"{case['id']}: {'PASS' if passed else 'FAIL'}"
              + (" | " + ", ".join(failed) if failed else ""))
    passed_count = sum(row["rules_passed"] for row in results)
    report = {"version": args.version, "case_count": len(cases),
              "rules_passed": passed_count, "rule_pass_rate": passed_count / len(cases),
              "note": "Offline deterministic demo. Retrieval and semantic reviews are not scored.",
              "results": results}
    output = Path(__file__).with_name("report_" + args.version + ".json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"规则检查通过：{passed_count}/{len(cases)} ({report['rule_pass_rate']:.1%})")
    print("这是选购后的加购流程演示；商品召回和回复语义仍需另行评测。")
    print(f"报告：{output}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
