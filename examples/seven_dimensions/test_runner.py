"""Offline boundary tests: python examples/seven_dimensions/test_runner.py.

Only dummy credentials, mocked responses, and scripted model messages are used.
The real .env is never loaded and the network is disabled throughout each test.
"""

from copy import deepcopy
import io
import json
from html.parser import HTMLParser
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import urllib.error

import client as client_module
import report as report_module
import run as runner
from cases import get_case


DUMMY_KEY = "unit-test-placeholder-not-a-real-key"


def final_message(answer, status="answer", recommendations=None, selected=None):
    return {"role": "assistant", "content": json.dumps({
        "answer": answer, "action_status": status,
        "recommendations": recommendations or [],
        "selected_product_id": selected, "citations": [],
    }, ensure_ascii=False)}


def tool_message(*calls):
    """Each call is (name, arguments), where a string can be malformed JSON."""
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": f"unit-call-{index}", "type": "function", "function": {
            "name": name,
            "arguments": args if isinstance(args, str) else json.dumps(args),
        }} for index, (name, args) in enumerate(calls)
    ]}


class ScriptedClient:
    """A fake API boundary; the real World still executes each tool."""

    model = "unit-test-model"

    def __init__(self, messages):
        self._messages = iter(messages)
        self.seen = []
        # An earlier task's usage must not be copied into a new record.
        self.history = [{"purpose": "old-task", "usage": None}]

    def complete(self, messages, **kwargs):
        self.seen.append({"messages": deepcopy(messages), "options": deepcopy(kwargs)})
        self.history.append({"purpose": kwargs.get("purpose", "agent"),
                             "usage": None, "elapsed_ms": 1,
                             "finish_reason": None, "error": None})
        return {"message": deepcopy(next(self._messages)), "usage": None}


class OfflineBoundaryTests(unittest.TestCase):
    def setUp(self):
        # Fail fast if a future test accidentally stops mocking network/config.
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(client_module, "load_local_env", side_effect=AssertionError(
            "Tests must not read the local .env")).start()
        mock.patch.object(client_module.urllib.request.OpenerDirector, "open",
                          side_effect=AssertionError("Tests must not use the network")).start()
        mock.patch.object(runner, "pricing_from_env", return_value=None).start()

    def api_client(self, response=None, error=None):
        opener = mock.Mock()
        if error is not None:
            opener.open.side_effect = error
        else:
            opener.open.return_value = io.BytesIO(response)
        with mock.patch.object(client_module, "load_local_env"), \
                mock.patch.dict(client_module.os.environ,
                                {"ARK_API_KEY": DUMMY_KEY, "ARK_MODEL": "unit-test-model"}, clear=True), \
                mock.patch.object(client_module.urllib.request, "build_opener", return_value=opener):
            return client_module.ArkClient()

    def test_http_error_redacts_dummy_key_from_exception_and_history(self):
        body = ("provider error echo: " + DUMMY_KEY).encode("utf-8")
        error = urllib.error.HTTPError(client_module.API_URL, 401, "Unauthorized", {}, io.BytesIO(body))
        client = self.api_client(error=error)
        with self.assertRaises(client_module.ModelError) as caught:
            client.complete([{"role": "user", "content": "hello"}])
        self.assertNotIn(DUMMY_KEY, str(caught.exception))
        self.assertNotIn(DUMMY_KEY, json.dumps(client.history))
        self.assertIn("[REDACTED]", client.history[0]["error"])
        self.assertIsNone(client.history[0]["usage"])

    def test_missing_usage_is_unknown_in_result_and_history(self):
        response = json.dumps({"choices": [{"message": {"role": "assistant", "content": "hello"},
                                           "finish_reason": "stop"}]}).encode("utf-8")
        client = self.api_client(response=response)
        result = client.complete([{"role": "user", "content": "hello"}], purpose="judge")
        self.assertIsNone(result["usage"])
        self.assertIsNone(client.history[0]["usage"])
        self.assertEqual("judge", client.history[0]["purpose"])
        self.assertEqual("stop", result["finish_reason"])

    def test_bad_json_and_unexpected_response_shapes_become_recorded_model_errors(self):
        for response in (b"not JSON", b"{}", b"[]", b'{"choices":null}', b'{"choices":[]}'):
            with self.subTest(response=response):
                client = self.api_client(response=response)
                with self.assertRaises(client_module.ModelError):
                    client.complete([{"role": "user", "content": "hello"}])
                self.assertEqual(1, len(client.history))
                self.assertTrue(client.history[0]["error"])
                self.assertIsNone(client.history[0]["usage"])

    def test_three_turns_follow_displayed_second_item_and_save_only_current_usage(self):
        # Deliberately reverse the normal candidate order.  Also supply an
        # inconsistent selected_product_id: the displayed list is authoritative.
        recommendations = [
            {"product_id": "P102", "reason": "符合条件", "evidence_ids": ["doc_P102"]},
            {"product_id": "P101", "reason": "符合条件", "evidence_ids": ["doc_P101"]},
        ]
        client = ScriptedClient([
            tool_message(("search_knowledge", {"query": "通勤双肩包", "top_k": 4})),
            tool_message(("get_product", {"product_id": "P101"}),
                         ("get_product", {"product_id": "P102"})),
            final_message("第一款云帆，第二款轻行。", "recommendation", recommendations, "P102"),
            final_message("第二款只有防泼水，不能保证暴雨中电脑不进水。", selected="P102"),
            tool_message(("get_product", {"product_id": "P101"})),
            tool_message(("add_to_cart", {"product_id": "P101", "quantity": 1})),
            final_message("第二款已增加一个。", "added", selected="P101"),
        ])
        record = runner.run_live(get_case("normal"), client, max_steps=8,
                                 use_judge=False, repeat_index=4)
        self.assertIsNone(record["error"])
        self.assertEqual(3, len(record["turns"]))
        self.assertEqual("P101", record["selected_product_id"])
        self.assertEqual({"P101": 1}, record["final_state"]["carts"]["U001"])
        self.assertEqual(record["initial_state"]["carts"]["U002"],
                         record["final_state"]["carts"]["U002"])
        self.assertEqual(7, len(record["model_calls"]))
        self.assertNotIn("old-task", [call["purpose"] for call in record["model_calls"]])
        serialized_messages = json.dumps(client.seen, ensure_ascii=False)
        self.assertNotIn("related_doc_ids", serialized_messages)
        self.assertNotIn("should_add", serialized_messages)
        self.assertTrue(all(call["options"]["json_output"] for call in client.seen))

    def test_repeated_tool_requests_stop_at_step_limit_without_running_judge(self):
        client = ScriptedClient([
            tool_message(("get_cart", {})),
            tool_message(("get_cart", {})),
        ])
        record = runner.run_live(get_case("normal"), client, max_steps=2,
                                 use_judge=True, repeat_index=0)
        self.assertTrue(record["stopped_by_limit"])
        self.assertTrue(record["error"])
        self.assertEqual(1, len(record["turns"]))
        self.assertEqual(2, len(record["model_calls"]))
        self.assertEqual(2, len(record["trace"]))
        self.assertIsNone(record["judge"])
        self.assertEqual(record["initial_state"]["carts"], record["final_state"]["carts"])

    def test_malformed_tool_arguments_return_safe_error_to_model(self):
        case = get_case("normal")
        case["turns"] = ["查询我的购物车。"]
        client = ScriptedClient([
            tool_message(("get_cart", '{"user_id":')),
            final_message("刚才的工具参数不完整，尚未取得购物车。"),
        ])
        record = runner.run_live(case, client, max_steps=3, use_judge=False, repeat_index=0)
        self.assertIsNone(record["error"])
        self.assertTrue(record["trace"][0]["blocked"])
        self.assertEqual("invalid_arguments", record["trace"][0]["result"]["error"]["code"])
        tool_response = next(message for message in client.seen[1]["messages"] if message["role"] == "tool")
        returned = json.loads(tool_response["content"])
        self.assertFalse(returned["ok"])
        self.assertNotIn("committed", returned)
        self.assertNotIn("blocked", returned)
        self.assertNotIn("request_id", returned)

    def test_live_progress_exposes_tool_effect_before_the_final_answer(self):
        case = get_case("normal")
        case["turns"] = ["查询我的购物车。"]
        client = ScriptedClient([
            tool_message(("get_cart", {})),
            final_message("购物车暂时为空。"),
        ])
        events = []
        record = runner.run_live(case, client, max_steps=3, use_judge=False,
                                 repeat_index=0, on_event=lambda kind, data: events.append((kind, data)))
        kinds = [kind for kind, _ in events]
        self.assertLess(kinds.index("user"), kinds.index("tool"))
        self.assertLess(kinds.index("tool"), kinds.index("assistant"))
        tool_event = next(data for kind, data in events if kind == "tool")
        self.assertEqual("get_cart", tool_event["entry"]["tool"])
        self.assertEqual({}, tool_event["state"]["carts"]["U001"])
        # Previously emitted snapshots must not acquire later turns or effects.
        self.assertEqual([], events[0][1]["record"]["turns"])
        self.assertEqual(record, events[-1][1]["record"])

    def test_report_renders_ordered_candidates_and_escapes_untrusted_text(self):
        payload = '\"><script>alert(1)</script><img src=x onerror=alert(1)>'
        parsed = {"answer": "下面按列表展示。" + payload, "recommendations": [
            {"product_id": "P102" + payload, "reason": payload, "evidence_ids": [payload]},
            {"product_id": "P101" + payload, "reason": payload, "evidence_ids": [payload]},
        ]}
        record = {"case_id": "unit", "source": "offline_fixture", "title": payload,
                  "turns": [{"user": payload, "assistant": json.dumps(parsed), "parsed": parsed}],
                  "trace": [{"tool": "search_knowledge", "args": {"query": payload},
                             "result": {"documents": [{"text": payload}]}}],
                  "dimensions": [], "initial_state": {"carts": {}}, "final_state": {"carts": {}}}
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as handle:
            target = Path(handle.name)
        self.addCleanup(target.unlink, missing_ok=True)
        report_module.render_report({"model": payload, "results": [record]}, target)
        rendered = target.read_text(encoding="utf-8")
        self.assertIn("本轮展示顺序（用户的第几款以此为准）", rendered)
        ordered = rendered.split("<ol>", 1)[1].split("</ol>", 1)[0]
        self.assertLess(ordered.index("P102"), ordered.index("P101"))
        self.assertIn("&lt;script&gt;", ordered)

        class ExecutableMarkupAudit(HTMLParser):
            dangerous = []

            def handle_starttag(self, tag, attrs):
                if tag in {"script", "img"} or any(key.lower().startswith("on") for key, _ in attrs):
                    self.dangerous.append((tag, attrs))

        audit = ExecutableMarkupAudit()
        audit.feed(rendered)
        self.assertEqual([], audit.dangerous)


if __name__ == "__main__":
    unittest.main(verbosity=2)
