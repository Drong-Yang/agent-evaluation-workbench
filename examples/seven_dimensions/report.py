"""Render an offline, self-contained HTML report using only the standard library.

Every value coming from a record is escaped as text.  The page has no scripts,
external assets, or HTML interpreted from model output.
"""

from __future__ import annotations

from collections import Counter
from html import escape
import json
from pathlib import Path


DIMENSIONS = (
    ("result", "最终结果"),
    ("retrieval", "RAG 检索"),
    ("evidence", "证据使用"),
    ("process", "Agent 过程"),
    ("reliability", "稳定性与异常处理"),
    ("efficiency", "成本与效率"),
    ("security", "安全与权限"),
)
STATUS_NAMES = {"pass": "通过", "fail": "失败", "unknown": "未验证", "na": "不适用"}


def _text(value):
    """The only path for putting untrusted text in the generated document."""
    return escape(str(value), quote=True)


def _json(value):
    return _text(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _value(value):
    if value is None:
        return '<span class="muted">未提供</span>'
    if isinstance(value, (dict, list, tuple)):
        return "<pre>" + _json(value) + "</pre>"
    return _text(value)


def _badge(status):
    # Never use a source-provided value as a class name or an attribute.
    safe_status = status if isinstance(status, str) and status in STATUS_NAMES else "unknown"
    return '<span class="badge ' + safe_status + '">' + STATUS_NAMES[safe_status] + "</span>"


def _mapping(data):
    if not isinstance(data, dict):
        return _value(data)
    if not data:
        return '<p class="muted">无记录</p>'
    rows = ["<tr><th>" + _text(key) + "</th><td>" + _value(value) + "</td></tr>" for key, value in data.items()]
    return '<div class="table-scroll"><table class="key-values"><tbody>' + "".join(rows) + "</tbody></table></div>"


def _checks(checks):
    if not checks:
        return '<p class="muted">没有额外检查项。</p>'
    blocks = []
    for check in checks:
        if not isinstance(check, dict):
            blocks.append("<li>" + _value(check) + "</li>")
            continue
        body = {key: value for key, value in check.items() if key != "status"}
        blocks.append("<li>" + _badge(check.get("status")) + _mapping(body) + "</li>")
    return '<ul class="checks">' + "".join(blocks) + "</ul>"


def _dimension_cards(dimensions):
    by_key = {item.get("key"): item for item in dimensions if isinstance(item, dict)}
    blocks = []
    for key, default_name in DIMENSIONS:
        item = by_key.get(key, {})
        blocks.append(
            '<section class="dimension"><h3>' + _text(item.get("name", default_name)) + "</h3>"
            + _badge(item.get("status"))
            + "<p>" + _text(item.get("summary", "没有评测结果。")) + "</p>"
            + "<details><summary>检查依据与指标</summary>"
            + _checks(item.get("checks", []))
            + "<h4>指标</h4>" + _mapping(item.get("metrics", {}))
            + "</details></section>"
        )
    return '<div class="dimension-grid">' + "".join(blocks) + "</div>"


def _recommendations(recommendations):
    if not isinstance(recommendations, list) or not recommendations:
        return ""
    items = []
    for item in recommendations:
        if not isinstance(item, dict):
            items.append("<li>" + _value(item) + "</li>")
            continue
        items.append(
            "<li><strong>" + _text(item.get("product_id", "商品编号缺失")) + "</strong>"
            + "<p>" + _text(item.get("reason", "未提供推荐理由")) + "</p>"
            + "<div>资料依据：" + _value(item.get("evidence_ids", [])) + "</div></li>"
        )
    return (
        '<section class="recommendations"><h4>本轮展示顺序（用户的第几款以此为准）</h4>'
        + "<ol>" + "".join(items) + "</ol></section>"
    )


def _turns(turns):
    if not turns:
        return '<p class="muted">没有对话记录。</p>'
    blocks = []
    for index, turn in enumerate(turns, start=1):
        parsed = turn.get("parsed")
        parsed = parsed if isinstance(parsed, dict) else {}
        answer = parsed.get("answer")
        if not isinstance(answer, str):
            answer = turn.get("assistant", "")
        blocks.append(
            '<section class="turn"><h4>第 ' + str(index) + " 轮</h4>"
            + '<div class="speaker">用户</div><div class="utterance">' + _text(turn.get("user", "")) + "</div>"
            + '<div class="speaker">助手解释（候选顺序以列表为准）</div><div class="utterance">' + _text(answer) + "</div>"
            + _recommendations(parsed.get("recommendations"))
        )
        if turn.get("error"):
            blocks.append('<div class="error">' + _value(turn["error"]) + "</div>")
        blocks.append("<details><summary>结构化最终回复</summary>" + _value(turn.get("parsed")) + "</details></section>")
    return "".join(blocks)


def _retrieval(trace):
    blocks = []
    for index, event in enumerate(trace, start=1):
        if event.get("tool") != "search_knowledge":
            continue
        result = event.get("result", {})
        documents = result.get("documents", []) if isinstance(result, dict) else []
        blocks.append("<h4>第 " + str(index) + " 次工具调用的召回结果</h4>")
        blocks.append("<p>查询参数：</p>" + _value(event.get("args")))
        if not documents:
            blocks.append('<p class="muted">没有返回文档。</p>')
        for rank, doc in enumerate(documents, start=1):
            blocks.append('<section class="document"><strong>排名 ' + str(rank) + "</strong>" + _mapping(doc) + "</section>")
    return "".join(blocks) or '<p class="muted">本次没有检索调用记录。</p>'


def _trace(trace):
    blocks = []
    for index, event in enumerate(trace, start=1):
        metadata = {key: value for key, value in event.items() if key not in ("args", "result")}
        blocks.append(
            '<details class="tool"><summary>' + str(index) + ". " + _text(event.get("tool", "未知工具"))
            + " · 耗时 " + _text(event.get("duration_ms", "未知")) + " ms</summary>"
            + "<h4>执行记录</h4>" + _mapping(metadata)
            + "<h4>参数</h4>" + _value(event.get("args"))
            + "<h4>工具返回</h4>" + _value(event.get("result")) + "</details>"
        )
    return "".join(blocks) or '<p class="muted">没有工具调用记录。</p>'


def _model_calls(calls, source):
    if source == "offline_fixture":
        intro = '<p class="notice">离线 fixture：以下为离线记录；不代表真实豆包调用、Token、耗时或模型成绩。</p>'
    else:
        intro = "<p>逐次保存 API 返回的 usage。缺失值保持未知；助手与裁判分别展示。费用以成本维度中配置的单价估算为准。</p>"
    blocks = [intro]
    for index, call in enumerate(calls, start=1):
        blocks.append("<details><summary>调用 " + str(index) + " · " + _text(call.get("purpose", "未知用途")) + "</summary>" + _mapping(call) + "</details>")
    if not calls:
        blocks.append('<p class="muted">没有模型 API 调用记录，无法核算真实使用量。</p>')
    return "".join(blocks)


def _record(record, index):
    source = record.get("source", "unknown")
    source_name = {"live": "在线 API 运行", "offline_fixture": "离线 fixture"}.get(source, "来源未知")
    trace = record.get("trace", [])
    before = record.get("initial_state") or {}
    after = record.get("final_state") or {}
    blocks = [
        '<article class="case" id="case-' + str(index) + '"><header class="case-header"><div><p class="eyebrow">'
        + _text(record.get("case_id", "未命名用例")) + " · " + _text(source_name) + "</p>"
        + "<h2>" + _text(record.get("title", "评测记录")) + "</h2></div>"
        + '<span class="source">场景：' + _text(record.get("scenario", "未知")) + "</span></header>"
    ]
    if source != "live":
        blocks.append('<p class="notice">这条记录不能作为真实模型的评测成绩。离线模式只用于检查演示流程和评分规则。</p>')
    if record.get("error") or record.get("stopped_by_limit"):
        blocks.append('<div class="error"><strong>执行异常</strong>' + _mapping({"error": record.get("error"), "stopped_by_limit": record.get("stopped_by_limit", False)}) + "</div>")
    blocks.append(_dimension_cards(record.get("dimensions", [])))
    blocks.append("<details class=\"section-detail\"><summary>用户对话与最终回复</summary>" + _turns(record.get("turns", [])) + "</details>")
    blocks.append("<details class=\"section-detail\"><summary>RAG 召回资料与排序</summary>" + _retrieval(trace) + "</details>")
    blocks.append("<details class=\"section-detail\"><summary>工具调用、参数与返回</summary>" + _trace(trace) + "</details>")
    blocks.append(
        '<details class="section-detail"><summary>购物车与商品状态：操作前后对照</summary><div class="state-grid">'
        + "<section><h3>初始购物车</h3>" + _value(before.get("carts")) + "</section>"
        + "<section><h3>最终购物车</h3>" + _value(after.get("carts")) + "</section></div>"
        + "<p>本次列表第二项对应商品：" + _value(record.get("selected_product_id")) + "</p>"
        + "<details><summary>完整初始与最终状态</summary>" + _mapping({"initial_state": before, "final_state": after}) + "</details></details>"
    )
    blocks.append('<details class="section-detail"><summary>模型调用、usage 与错误</summary>' + _model_calls(record.get("model_calls", []), source) + "</details>")
    blocks.append('<details class="section-detail"><summary>语义裁判结果（待人工校准）</summary><p>裁判与被测 Agent 使用同一模型，通过独立请求评分。独立请求不等于独立模型；结果需要人工抽检校准。</p>' + _value(record.get("judge")) + "</details>")
    blocks.append('<details class="section-detail"><summary>评测预期与完整记录</summary><p>预期只供评测使用，不提供给被测 Agent。</p>' + _value(record) + "</details></article>")
    return "".join(blocks)


STYLE = """
:root{color-scheme:light;--ink:#183042;--muted:#5b6d7b;--line:#dce5ec;--paper:#fff;--bg:#f3f6fa;--accent:#205b9a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
main{max-width:1400px;margin:0 auto;padding:36px 24px 70px}h1{font-size:32px;line-height:1.3;margin:0 0 12px}h2{font-size:23px;margin:0 0 8px}h3{font-size:17px;margin:0 0 10px}h4{margin:16px 0 8px}p{margin:10px 0}.muted,.eyebrow{color:var(--muted)}.eyebrow{font-size:13px;margin:0 0 5px}
.hero,.case,.overview{background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:24px;margin-bottom:22px}.hero{border-top:5px solid var(--accent)}.case-header{display:flex;align-items:flex-start;justify-content:space-between;gap:15px;flex-wrap:wrap}.source{font-size:13px;background:#edf2f7;border-radius:7px;padding:6px 10px}
.notice{background:#fff7e1;border-left:4px solid #bf8e24;padding:12px 16px;color:#634c19}.error{border-left:4px solid #b93434;background:#fff0ef;padding:12px 16px;margin:14px 0}.dimension-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,275px),1fr));gap:14px;margin-top:20px}.dimension{border:1px solid var(--line);border-radius:10px;padding:16px;min-width:0}.dimension p{font-size:14px}.badge{display:inline-block;font-size:12px;font-weight:650;border-radius:5px;padding:2px 8px}.pass{background:#dcf3e6;color:#17603d}.fail{background:#fee4e2;color:#a22424}.unknown{background:#fff0c4;color:#795611}.na{background:#edf0f3;color:#5d6872}.recommendations li{padding:8px 12px;margin:8px 0;background:#f4f7fa;border-radius:6px}
details{margin:12px 0}summary{cursor:pointer;font-weight:600;color:var(--accent);overflow-wrap:anywhere}details[open]>summary{margin-bottom:12px}.section-detail{border-top:1px solid var(--line);padding-top:14px}.tool,.document,.turn{border:1px solid var(--line);border-radius:8px;padding:14px;margin:12px 0}.speaker{font-size:12px;font-weight:700;color:var(--muted);margin-top:12px}.utterance{white-space:pre-wrap;overflow-wrap:anywhere}.state-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}
pre{font:12px/1.55 Consolas,"Courier New",monospace;margin:8px 0;background:#f4f7fa;border:1px solid #e5ebf0;border-radius:6px;padding:12px;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word}table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:10px;border-bottom:1px solid var(--line);vertical-align:top;text-align:left;overflow-wrap:anywhere}th{font-weight:600;color:var(--muted)}.key-values th{width:32%}.table-scroll{overflow-x:auto}.checks{list-style:none;padding:0}.checks>li{padding:8px 0;border-bottom:1px dashed var(--line)}.legend{display:flex;gap:16px;flex-wrap:wrap}.legend span{white-space:nowrap}a{color:var(--accent)}nav{display:flex;flex-wrap:wrap;gap:8px 18px;margin-top:16px}footer{font-size:13px;color:var(--muted);text-align:center}
@media(max-width:650px){main{padding:18px 12px 40px}.hero,.case,.overview{padding:17px}h1{font-size:26px}.state-grid{grid-template-columns:1fr}}
@media print{body{background:white}main{padding:0}.case,.dimension{break-inside:avoid}summary{color:#183042}}
"""


def render_report(report, output_path):
    """Write a self-contained HTML artifact and return its path.

    ``report`` follows CONTRACT.md.  Rendering never reads configuration or makes
    API requests.  Missing results are labelled unknown rather than inferred.
    """
    results = report.get("results", [])
    counts = Counter(item.get("source", "unknown") for item in results)
    live_case_counts = Counter(item.get("case_id", "unknown") for item in results if item.get("source") == "live")
    rows = []
    for key, name in DIMENSIONS:
        statuses = Counter()
        for record in results:
            found = next((item for item in record.get("dimensions", []) if item.get("key") == key), {})
            status = found.get("status", "unknown")
            statuses[status if status in STATUS_NAMES else "unknown"] += 1
        rows.append("<tr><th>" + name + "</th>" + "".join("<td>" + str(statuses[status]) + "</td>" for status in STATUS_NAMES) + "</tr>")
    repeated = any(number > 1 for number in live_case_counts.values())
    if repeated:
        stability = "存在在线重复运行：请结合逐用例汇总观察波动；少量重复不能证明长期稳定，单次场景仍未验证稳定性。"
    elif live_case_counts:
        stability = "每个在线场景只运行一次（n=1）：稳定性尚未验证。"
    else:
        stability = "没有在线运行记录：离线 fixture 不评估真实模型稳定性。"
    html = [
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        + "<title>电商 Agent 七维评测报告</title><style>" + STYLE + "</style></head><body><main>"
        + '<header class="hero"><p class="eyebrow">电商导购 + 购物车助手 · 评测演示</p><h1>七个维度，看见结果与过程</h1>'
        + "<p>模型：" + _text(report.get("model", "未提供")) + "<br>生成时间：" + _text(report.get("generated_at", "未提供")) + "</p>"
        + "<p>在线 API 记录 " + str(counts["live"]) + " 条 · 离线 fixture " + str(counts["offline_fixture"]) + " 条 · 来源未知 " + str(counts["unknown"]) + " 条</p>"
        + '<p class="notice">' + stability + " 同模型裁判尚待人工校准。未配置单价时，费用保持未知；Mock 工具耗时不代表真实电商服务性能。</p>"
        + '<div class="legend"><span>' + _badge("pass") + " 已有依据支持</span><span>" + _badge("fail") + " 已发现不符合</span><span>" + _badge("unknown") + " 证据不足或未执行</span><span>" + _badge("na") + " 该用例不覆盖</span></div>"
    ]
    if counts["offline_fixture"]:
        html.append('<p class="notice">本报告包含离线 fixture。离线结果仅说明评分管线如何工作，不能视为真实模型成绩；请按各条记录的来源阅读。</p>')
    html.append("<nav>" + "".join('<a href="#case-' + str(index) + '">' + _text(record.get("case_id", "用例")) + " · " + str(index) + "</a>" for index, record in enumerate(results, start=1)) + "</nav></header>")
    html.append('<section class="overview"><h2>本批检查状态分布</h2><p>计数单位是运行记录，并非七维总分。含离线数据时，此表也不是在线模型通过率。</p><div class="table-scroll"><table><thead><tr><th>维度</th><th>通过</th><th>失败</th><th>未验证</th><th>不适用</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>")
    html.append("<details><summary>批量汇总与重复运行信息</summary>" + _mapping(report.get("summary", {})) + "</details></section>")
    html.extend(_record(record, index) for index, record in enumerate(results, start=1))
    if not results:
        html.append('<p class="notice">本次没有用例结果。</p>')
    html.append("<footer>本报告为独立 HTML 文件，无外部资源。状态和指标只描述本批用例及其记录。</footer></main></body></html>")
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(html), encoding="utf-8")
    return target
