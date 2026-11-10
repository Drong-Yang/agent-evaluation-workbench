"""Repeat statistics must not turn one run or offline fixtures into model evidence."""

import unittest

from run import add_repeat_metrics


def row(source="live", result="pass"):
    return {"source": source, "case_id": "normal", "dimensions": [
        {"key": "result", "status": result},
        {"key": "reliability", "status": "unknown", "checks": [
            {"name": "重复运行稳定性", "status": "unknown", "reason": "single run"}], "metrics": {}},
    ]}


class RepeatTests(unittest.TestCase):
    def test_single_and_offline_runs_never_prove_model_stability(self):
        rows = [row(), row("offline_fixture"), row("offline_fixture")]
        add_repeat_metrics(rows)
        self.assertTrue(all(r["dimensions"][1]["status"] == "unknown" for r in rows))
        self.assertIsNone(rows[1]["dimensions"][1]["metrics"]["repeated_case"]["scenario_pass_rate"])

    def test_repeated_live_passes_report_only_the_observed_batch(self):
        rows = [row(), row()]
        add_repeat_metrics(rows)
        self.assertEqual(rows[0]["dimensions"][1]["status"], "pass")
        metrics = rows[0]["dimensions"][1]["metrics"]["repeated_case"]
        self.assertEqual(metrics["runs"], 2)
        self.assertEqual(metrics["scenario_pass_rate"], 1)
        self.assertIn("不等于生产", metrics["scope"])

    def test_failures_and_unknown_results_are_not_hidden_by_passes(self):
        failed = [row(), row(result="fail")]
        add_repeat_metrics(failed)
        self.assertEqual(failed[0]["dimensions"][1]["status"], "fail")
        pending = [row(), row(result="unknown")]
        add_repeat_metrics(pending)
        self.assertEqual(pending[0]["dimensions"][1]["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
