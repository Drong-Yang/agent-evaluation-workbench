"use strict";

const $ = (id) => document.getElementById(id);
const DIMENSIONS = [
  ["result", "最终结果", "答案与实际任务是否完成", "◎"],
  ["retrieval", "RAG 检索", "该找的找全，无关的少一点", "⌕"],
  ["evidence", "证据使用", "说出的结论有没有资料支持", "▤"],
  ["process", "Agent 过程", "工具、参数与执行顺序", "↳"],
  ["reliability", "稳定性与异常处理", "重复运行与失败恢复", "↻"],
  ["efficiency", "成本与效率", "耗时、Token 与调用次数", "◷"],
  ["security", "安全与权限", "越权与恶意内容是否被拦住", "◇"],
];
const STATUS = {pass: "通过", fail: "未通过", unknown: "未知", na: "不适用", pending: "待评测"};
const ACTION = {recommendation: "已推荐", answer: "已回答", added: "声称已加购", not_added: "声称未加购", pending: "结果待确认", need_clarification: "需要澄清"};
const state = {cases: [], config: null, selected: null, run: null, busy: false, tab: "conversation", repeatIndex: 0, dimension: null, lastSignature: "", pollTimer: null, clockTimer: null, spec: null, specCaseId: null, specTab: "testcase", specLoading: false, specError: "", specRequest: 0};

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}
function append(parent, ...children) {
  children.filter(Boolean).forEach((child) => parent.append(child));
  return parent;
}
function setError(message) {
  $("error-banner").textContent = message || "";
  $("error-banner").hidden = !message;
}
function json(value) {
  try { return JSON.stringify(value, null, 2); } catch (_) { return "数据无法展示"; }
}
function copy(value) {
  return value == null ? value : JSON.parse(JSON.stringify(value));
}
function statusPill(value) {
  const known = Object.hasOwn(STATUS, value) ? value : "unknown";
  return node("span", "status-pill " + known, STATUS[known]);
}
function formatNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("zh-CN") : "—";
}
function seconds(ms) {
  return typeof ms === "number" && Number.isFinite(ms) ? (ms / 1000).toFixed(1) + " s" : "—";
}
async function api(path, options = {}) {
  const response = await fetch(path, {cache: "no-store", ...options});
  let data;
  try { data = await response.json(); } catch (_) { throw new Error("服务返回了无法识别的响应。"); }
  if (!response.ok) throw new Error(data.error || "请求失败（" + response.status + "）");
  return data;
}
function selectedCase() {
  return state.cases.find((item) => item.id === state.selected);
}
function selectedMode() {
  return document.querySelector('input[name="mode"]:checked').value;
}
function textItems(value) {
  return Array.isArray(value) ? value : value == null || value === "" ? [] : [value];
}
function specList(values, className = "spec-list") {
  const list = node("ul", className);
  textItems(values).forEach((value) => list.append(node("li", "", value)));
  return list;
}
function specBlock(title, ...children) {
  return append(node("section", "spec-block"), node("h3", "", title), ...children);
}
function specText(value) { return node("p", "spec-text", value || "未提供说明"); }
async function loadCaseSpec(id, resetTab = true) {
  if (!id) return;
  const request = ++state.specRequest;
  state.specCaseId = id;
  state.spec = null;
  state.specLoading = true;
  state.specError = "";
  if (resetTab) state.specTab = "testcase";
  renderCaseSpec();
  try {
    const spec = await api("/api/cases/" + encodeURIComponent(id) + "/spec");
    if (request !== state.specRequest || state.selected !== id) return;
    state.spec = spec;
  } catch (error) {
    if (request !== state.specRequest || state.selected !== id) return;
    state.specError = error.message;
  } finally {
    if (request === state.specRequest && state.selected === id) {
      state.specLoading = false;
      renderCaseSpec();
    }
  }
}
function renderSpecTestcase(container, spec) {
  append(container, specBlock("这个用例要验证什么", specText(spec.purpose)), node("p", "spec-callout", spec.scenario_note || "这里展示预设的测试条件；运行结果请看下方实际记录。"));
  if (spec.setup?.length) {
    const setup = node("dl", "spec-setup");
    spec.setup.forEach((item) => {
      append(setup, append(node("div"), node("dt", "", item.label), node("dd", "", item.value)));
    });
    container.append(specBlock("前置条件与场景设置", setup));
  }
  const turns = node("ol", "spec-turns");
  (spec.turns || []).forEach((turn, index) => {
    const card = node("li", "spec-turn");
    append(card, node("span", "spec-turn-number", "第 " + (index + 1) + " 轮"), append(node("div", "spec-turn-body"), node("p", "spec-input", turn.user), node("span", "spec-field-label", "本轮预期"), specList(turn.expected)));
    turns.append(card);
  });
  container.append(specBlock("用户输入与逐轮预期", turns));
  const outcomes = node("div", "spec-columns");
  append(outcomes, specBlock("整条 Case 的预期结果", specList(spec.expected_outcome)), specBlock("应该满足的过程约束", specList(spec.expected_process)));
  container.append(outcomes);
  container.append(node("p", "spec-footnote", "一条 Case 包含上面全部轮次，保留上下文连续执行；每次检索、工具调用和最终状态分别检查，再汇总为七维结果。"));
}
function renderSpecStandards(container, spec) {
  append(container, node("p", "spec-callout", spec.aggregation_rule || "这里只展示预设标准，运行后才会得到通过、未通过、未知或不适用的实际结果。"));
  (spec.dimensions || []).forEach((dimension, index) => {
    const detail = node("details", "spec-dimension");
    detail.id = "spec-dimension-" + dimension.key;
    detail.open = index === 0;
    const summary = append(node("summary"), node("span", "spec-rule-number", String(index + 1).padStart(2, "0")), node("strong", "", dimension.name), node("span", "spec-dimension-scope", dimension.scope));
    detail.append(summary);
    const grid = node("div", "spec-rules-grid");
    [["pass", "什么情况下通过"], ["fail", "什么情况下未通过"], ["unknown", "什么情况下未知"], ["na", "什么情况下不适用"]].forEach(([key, title]) => {
      append(grid, append(node("div", "spec-rule"), node("h4", "", title), specList(textItems(dimension[key]).length ? dimension[key] : ["本用例未单独设置此类条件。"] )));
    });
    detail.append(grid);
    if (dimension.note) detail.append(node("p", "spec-rule-note", dimension.note));
    container.append(detail);
  });
  if (spec.notes?.length) container.append(specBlock("阅读标准时需要知道", specList(spec.notes)));
}
function renderSpecMock(container, spec) {
  const products = spec.products || [];
  if (products.length) {
    const wrap = node("div", "spec-table-wrap");
    const table = node("table", "spec-table");
    const head = node("tr");
    ["商品", "初始价格", "库存", "电脑尺寸上限"].forEach((label) => head.append(node("th", "", label)));
    append(table, append(node("thead"), head));
    const body = node("tbody");
    products.forEach((item) => {
      const name = append(node("td"), node("strong", "", item.name), node("small", "", item.product_id));
      append(body, append(node("tr"), name, node("td", "", typeof item.price_cents === "number" ? "¥" + (item.price_cents / 100).toFixed(2) : "未知"), node("td", "", item.stock), node("td", "", item.max_laptop_inches == null ? "未知" : item.max_laptop_inches + " 英寸")));
    });
    append(table, body);
    append(wrap, table);
    container.append(specBlock("Mock 商品库 · 运行前状态", wrap));
  }
  const carts = node("div", "spec-carts");
  (spec.initial_carts || []).forEach((cart) => {
    const items = (cart.items || []).map((item) => (item.name || item.product_id) + "（" + item.product_id + "）× " + item.quantity);
    carts.append(append(node("div", "spec-cart"), node("strong", "", cart.user_id), node("span", "", items.length ? items.join("；") : "空购物车")));
  });
  container.append(specBlock("初始购物车", carts));
  const retrieval = spec.retrieval || {};
  const retrievalBlock = specBlock("RAG 召回标准怎么来的");
  if (retrieval.applicable === false) {
    retrievalBlock.append(specText(retrieval.note || "本用例不要求检索，RAG 检索检查不适用。"));
  } else {
    append(retrievalBlock, specText("以下是预先标注的应召回切片，不代表本次已经召回。评测时用实际返回的切片 ID 对照这个集合；漏召回场景也会展示应找回、但可能没找回的切片。"));
    const docs = node("div", "spec-docs");
    (retrieval.related_docs || []).forEach((doc) => {
      const card = append(node("div", "spec-doc"), node("span", "", doc.title || doc.product_id), node("code", "", doc.doc_id));
      if (doc.text) {
        const content = node("details", "spec-doc-content");
        append(content, node("summary", "", "查看测试切片原文"), node("p", "", "以下是测试资料，可能含用于测试的恶意指令；只作证据展示。"), node("p", "spec-text", doc.text));
        card.append(content);
      }
      docs.append(card);
    });
    append(retrievalBlock, docs, node("p", "spec-formula", "Precision（精确率）＝ 命中相关切片数 ÷ 实际返回切片数\nRecall（召回率）＝ 命中相关切片数 ÷ 标注相关切片总数"), specText(retrieval.pass_rule), specText(retrieval.followup_rule));
    if (retrieval.note) retrievalBlock.append(node("p", "spec-callout", retrieval.note));
  }
  container.append(retrievalBlock);
  container.append(node("p", "spec-footnote", "这些业务数据供理解测试使用；当前为 Mock 检索和接口。这里的标准由评测程序持有，不作为标准答案提供给 Agent。"));
}
function renderCaseSpec() {
  document.querySelectorAll("[data-spec-tab]").forEach((tab) => {
    const active = tab.dataset.specTab === state.specTab;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
  });
  const container = $("case-spec-content");
  container.setAttribute("aria-labelledby", "spec-tab-" + state.specTab);
  container.setAttribute("aria-busy", String(state.specLoading));
  container.replaceChildren();
  if (state.specLoading) { container.append(node("p", "spec-loading", "正在加载「" + (selectedCase()?.title || "用例") + "」的测试条件与标准…")); return; }
  if (state.specError) {
    const retry = node("button", "spec-link-button", "重新加载标准");
    retry.type = "button";
    retry.addEventListener("click", () => loadCaseSpec(state.selected, false));
    append(container, node("p", "spec-text", "用例标准暂时无法加载：" + state.specError), retry);
    return;
  }
  if (!state.spec || state.specCaseId !== state.selected) { container.append(node("p", "spec-loading", "选择一个场景，即可查看测试用例和评判标准，无需先运行。")); return; }
  if (state.specTab === "standards") renderSpecStandards(container, state.spec);
  else if (state.specTab === "mock") renderSpecMock(container, state.spec);
  else renderSpecTestcase(container, state.spec);
  container.scrollTop = 0;
}
function showSpecDimension(key) {
  $("case-spec-panel").open = true;
  state.specTab = "standards";
  renderCaseSpec();
  const detail = document.getElementById("spec-dimension-" + key);
  if (detail) {
    detail.open = true;
    detail.scrollIntoView({block: "nearest", behavior: "smooth"});
  } else $("case-spec-panel").scrollIntoView({block: "start", behavior: "smooth"});
}
function renderCases() {
  const container = $("case-list");
  container.replaceChildren();
  const groups = [
    ["基础任务", ["normal", "existing_quantity"]],
    ["检索质量", ["rag_missing", "rag_noise"]],
    ["业务异常", ["price_changed", "out_of_stock", "explicit_failure", "timeout_after_commit"]],
    ["安全边界", ["injection", "unauthorized"]],
  ];
  let count = 0;
  const known = new Set(groups.flatMap((group) => group[1]));
  if (state.cases.some((item) => !known.has(item.id))) groups.push(["其他场景", state.cases.filter((item) => !known.has(item.id)).map((item) => item.id)]);
  groups.forEach(([name, ids]) => {
    const cases = ids.map((id) => state.cases.find((item) => item.id === id)).filter(Boolean);
    if (!cases.length) return;
    container.append(node("p", "case-group-title", name));
    cases.forEach((item) => {
      count += 1;
      const button = node("button", "case-button" + (item.id === state.selected ? " active" : ""));
      button.type = "button";
      button.disabled = state.busy;
      button.setAttribute("aria-current", item.id === state.selected ? "true" : "false");
      button.title = item.description;
      const text = node("span", "case-button-text", item.title);
      text.append(node("small", "", item.id));
      append(button, node("span", "case-number", String(count).padStart(2, "0")), text, node("span", "case-arrow", "›"));
      button.addEventListener("click", () => selectCase(item.id));
      container.append(button);
    });
  });
  $("case-count").textContent = String(state.cases.length);
}
function selectCase(id) {
  if (state.busy || id === state.selected) return;
  state.selected = id;
  state.run = null;
  state.repeatIndex = 0;
  state.dimension = null;
  state.lastSignature = "";
  sessionStorage.removeItem("evaluationRunId");
  setError("");
  renderCases();
  render();
  loadCaseSpec(id);
}
function syncControls() {
  const mode = selectedMode();
  const keyMissing = mode === "live" && state.config && !state.config.key_configured;
  $("run-button").disabled = state.busy || !state.selected || !state.config || keyMissing;
  $("run-label").textContent = state.busy ? "运行中…" : "运行此 Case";
  document.querySelectorAll('input[name="mode"], #repeat-count').forEach((element) => { element.disabled = state.busy; });
  $("judge-enabled").disabled = state.busy || mode === "offline";
  const note = $("mode-note");
  note.classList.toggle("offline", mode === "offline");
  note.textContent = mode === "offline" ? "离线模式运行预设脚本，用于演示评测器；结果不能作为真实模型表现。" : keyMissing ? "服务端尚未配置 API Key。可先切换离线演示。" : "仅点击运行后才调用模型；每次运行会产生 API 用量。";
}

// Rebuild observed state from the event stream. Completed records are authoritative.
function recordsFromRun(run) {
  const records = new Map();
  if (!run) return records;
  (run.events || []).forEach((event) => {
    const index = Number.isInteger(event.repeat_index) ? event.repeat_index : 0;
    const data = event.data || {};
    if (data.record && typeof data.record === "object") {
      records.set(index, copy(data.record));
      return;
    }
    if (!records.has(index)) records.set(index, {turns: [], trace: [], model_calls: [], initial_state: null, final_state: null});
    const record = records.get(index);
    const turnIndex = Number.isInteger(data.turn_index) ? data.turn_index : 0;
    if (event.type === "user") {
      record.turns[turnIndex] = {...(record.turns[turnIndex] || {}), user: data.user};
    } else if (event.type === "assistant" && data.turn) {
      record.turns[turnIndex] = copy(data.turn);
    } else if (event.type === "tool" && data.entry) {
      record.trace.push(copy(data.entry));
      if (data.state) record.final_state = copy(data.state);
    } else if (event.type === "judge_end") {
      record.judge = copy(data.judge);
      record.judge_error = data.judge_error;
    }
  });
  (run.results || []).forEach((record, i) => records.set(Number.isInteger(record.repeat_index) ? record.repeat_index : i, record));
  return records;
}
function currentRecord() {
  return recordsFromRun(state.run).get(state.repeatIndex) || null;
}
function product(record, id) {
  return record?.final_state?.products?.[id] || record?.initial_state?.products?.[id] || {};
}
function productName(record, id) {
  return product(record, id).name || id || "未知商品";
}
function renderEmpty(container, title, description, symbol, withTurns = false) {
  const empty = node("div", "empty-state");
  append(empty, node("div", "empty-art", symbol), node("h3", "", title), node("p", "", description));
  if (withTurns) {
    const previews = node("div", "preview-turns");
    (selectedCase()?.turns || []).forEach((turn, index) => {
      append(previews, append(node("div", "preview-turn"), node("span", "", String(index + 1).padStart(2, "0")), node("div", "", turn)));
    });
    empty.append(previews);
  }
  container.append(empty);
}
function waiting(text) {
  const dots = node("span", "loading-dots");
  append(dots, node("i"), node("i"), node("i"));
  return append(node("div", "waiting-message"), dots, node("span", "", text));
}
function addMessage(parent, role, text) {
  const message = node("div", "message " + (role === "user" ? "user-message" : "assistant-message"));
  const body = node("div", "message-body");
  append(body, node("div", "speaker", role === "user" ? "用户" : "导购 Agent"));
  if (text) body.append(node("p", "message-text", text));
  append(message, node("span", "avatar " + role, role === "user" ? "U" : "AI"), body);
  parent.append(message);
  return body;
}
function renderConversation(container, record) {
  if (!record || !(record.turns || []).length) {
    renderEmpty(container, state.busy ? "Agent 即将开始" : "从一次购物对话开始", state.busy ? "每一步执行都会出现在这里，可以随时切换工具与证据。" : "点击「运行此 Case」，观察 Agent 如何检索资料、回答问题并完成加购。", "↳", !state.busy);
    return;
  }
  if (state.run?.mode === "offline") container.append(node("div", "offline-notice", "当前展示的是预设脚本，不是真实模型的回答；用于观察评测如何发现问题。"));
  (record.turns || []).forEach((turn, index) => {
    if (!turn) return;
    const section = node("section", "conversation-turn");
    section.append(node("p", "turn-label", "TURN " + String(index + 1).padStart(2, "0")));
    addMessage(section, "user", turn.user || "");
    const parsed = turn.parsed;
    const body = addMessage(section, "assistant", parsed?.answer || (turn.assistant && !parsed ? turn.assistant : ""));
    if (Array.isArray(parsed?.recommendations) && parsed.recommendations.length) {
      const list = node("ol", "recommendation-list");
      parsed.recommendations.forEach((item, order) => {
        if (!item || typeof item !== "object" || Array.isArray(item)) {
          list.append(node("li", "inline-error", "第 " + (order + 1) + " 个候选项格式无效。"));
          return;
        }
        const card = node("li", "recommendation-card");
        const info = node("div", "message-body");
        // The rendered array is the user's actual order; never infer a fixed second item.
        const facts = record.initial_state?.products?.[item.product_id] || product(record, item.product_id);
        const heading = append(node("div", "product-heading"), node("strong", "", facts.name || item.product_id), typeof facts.price_cents === "number" ? node("span", "product-price", "¥" + (facts.price_cents / 100).toFixed(0)) : null);
        const citations = node("div", "citations");
        (Array.isArray(item.evidence_ids) ? item.evidence_ids : []).forEach((id) => citations.append(node("span", "citation", id)));
        append(info, heading, node("p", "product-reason", item.reason || ""), citations);
        append(card, node("span", "recommendation-order", order + 1), info);
        list.append(card);
      });
      append(body, list, node("p", "display-order-note", "展示顺序来自模型返回的候选数组；「第二款」以此为准。"));
    }
    if (!parsed && !turn.assistant && !turn.error) body.append(state.busy ? waiting("正在思考或调用工具…") : node("p", "muted", "没有收到有效回答。"));
    if (parsed?.action_status) body.append(node("span", "action-tag", ACTION[parsed.action_status] || parsed.action_status));
    if (Array.isArray(parsed?.citations) && parsed.citations.length) {
      const citations = node("div", "citations");
      citations.style.marginTop = "9px";
      parsed.citations.forEach((id) => citations.append(node("span", "citation", id)));
      body.append(citations);
    }
    if (turn.error) body.append(node("div", "inline-error", turn.error));
    container.append(section);
  });
  if (record.judge_error) container.append(node("div", "inline-error", "LLM 裁判没有完成：" + record.judge_error));
}
function renderTools(container, record) {
  const trace = record?.trace || [];
  if (!trace.length) {
    renderEmpty(container, "工具调用会显示在这里", "这里记录模型选择的工具、真实参数、返回值和执行顺序。没有调用时不会补造记录。", "⌘");
    return;
  }
  trace.forEach((entry, index) => {
    const details = node("details", "tool-card");
    details.dataset.key = "tool-" + state.repeatIndex + "-" + index;
    const success = entry.result?.ok !== false;
    const summary = append(node("summary"), node("span", "tool-number", String(index + 1).padStart(2, "0")), node("span", "tool-name", entry.tool || "未知工具"), node("span", "tool-result-tag" + (success ? "" : " failed"), entry.blocked ? "已拦截" : entry.replayed ? "幂等重放" : success ? "已返回" : "异常返回"));
    const notes = ["第 " + ((entry.turn_index || 0) + 1) + " 轮", seconds(entry.duration_ms)];
    if (entry.committed) notes.push("已实际写入");
    append(details, summary, node("div", "tool-meta", notes.join(" · ")), node("p", "code-label", "调用参数"), node("pre", "json-view", json(entry.args)), node("p", "code-label", "工具返回"), node("pre", "json-view", json(entry.result)));
    container.append(details);
  });
}
function renderEvidence(container, record) {
  const searches = (record?.trace || []).filter((entry) => entry.tool === "search_knowledge");
  if (!searches.length) {
    renderEmpty(container, "先看 Agent 实际拿到了什么", "每次检索返回的原文都会出现在这里，再对照答案判断有没有遗漏、噪声或无依据承诺。", "▤");
    return;
  }
  searches.forEach((entry, index) => {
    container.append(node("p", "evidence-query", "检索 " + (index + 1) + " · 第 " + ((entry.turn_index || 0) + 1) + " 轮 · " + (entry.args?.query || "")));
    const docs = entry.result?.documents;
    if (!Array.isArray(docs) || !docs.length) container.append(node("p", "muted", "本次检索没有返回文档。"));
    (Array.isArray(docs) ? docs : []).forEach((doc, order) => {
      if (!doc || typeof doc !== "object") {
        container.append(node("pre", "json-view", json(doc)));
        return;
      }
      const card = node("article", "evidence-card");
      const header = append(node("div", "evidence-heading"), node("span", "", "#" + (order + 1)), node("span", "", doc.doc_id), node("small", "", doc.product_id));
      append(card, header, node("p", "evidence-text", doc.text || json(doc)));
      container.append(card);
    });
  });
}
function renderDimensions(record) {
  const container = $("dimensions");
  container.replaceChildren();
  const dimensions = Array.isArray(record?.dimensions) ? record.dimensions : [];
  const passed = dimensions.filter((item) => item.status === "pass").length;
  $("dimension-summary").textContent = dimensions.length ? passed + " / 7 通过" : state.busy ? "执行后评测" : "等待评测";
  DIMENSIONS.forEach(([key, name, description, icon]) => {
    const dimension = dimensions.find((item) => item.key === key);
    const button = node("button", "dimension-button" + (state.dimension === key ? " selected" : ""));
    button.type = "button";
    button.setAttribute("aria-expanded", state.dimension === key ? "true" : "false");
    button.setAttribute("aria-controls", "dimension-detail");
    const text = append(node("div"), node("div", "dimension-name", name), node("div", "dimension-description", description));
    append(button, node("span", "dimension-icon", icon), text, statusPill(dimension ? dimension.status : "pending"));
    button.addEventListener("click", () => { state.dimension = state.dimension === key ? null : key; renderDimensions(currentRecord()); });
    container.append(button);
  });
  const detail = $("dimension-detail");
  detail.replaceChildren();
  detail.hidden = !state.dimension;
  if (!state.dimension) return;
  const active = dimensions.find((item) => item.key === state.dimension);
  detail.append(node("p", "detail-title", DIMENSIONS.find((item) => item[0] === state.dimension)[1] + " · 判定依据"));
  const standards = node("button", "spec-link-button", "查看此维度的预设评判标准 ↑");
  standards.type = "button";
  standards.addEventListener("click", () => showSpecDimension(state.dimension));
  detail.append(standards);
  if (!active) { detail.append(node("p", "check-reason", "任务执行结束后显示检查项与实际测量值。")); return; }
  (active.checks || []).forEach((check) => {
    const row = node("div", "check-row");
    append(row, append(node("div", "check-title"), node("span", "", check.name), statusPill(check.status)), node("p", "check-reason", check.reason));
    detail.append(row);
  });
  if (["evidence", "result", "process"].includes(state.dimension) && record.judge) detail.append(node("p", "check-reason", "语义裁判使用同一模型的独立请求，评分仍需人工校准。"));
  if (active.metrics && Object.keys(active.metrics).length) {
    const metrics = node("details", "metric-details");
    append(metrics, node("summary", "", "查看测量原始值"), node("pre", "json-view", json(active.metrics)));
    detail.append(metrics);
  }
}
function renderCart(record) {
  const container = $("cart-content");
  container.replaceChildren();
  if (!record?.initial_state) { container.append(node("p", "muted", "运行后对照真实状态，核实是否加购。")); return; }
  const before = record.initial_state.carts?.U001 || {};
  const after = record.final_state?.carts?.U001;
  if (!after) { container.append(node("p", "muted", "购物车状态将在工具执行后更新。")); return; }
  const ids = [...new Set([...Object.keys(before), ...Object.keys(after)])];
  if (!ids.length) container.append(node("p", "muted", "运行前后均为空购物车。"));
  ids.forEach((id) => {
    const old = before[id] || 0;
    const current = after[id] || 0;
    const delta = current - old;
    const name = node("div", "cart-name", productName(record, id));
    name.append(node("small", "", id));
    const values = append(node("div", "cart-count"), node("span", "", old), node("span", "cart-arrow", "→"), node("span", "cart-after", current), node("span", "cart-delta" + (delta === 0 ? " same" : delta < 0 ? " negative" : ""), delta > 0 ? "+" + delta : delta === 0 ? "—" : delta));
    container.append(append(node("div", "cart-row"), name, values));
  });
  container.append(node("p", "cart-footnote", "按运行前后的 Mock 数据库状态核对，数字代表商品数量；最终答复中的「已加购」还需与这里一致。"));
}
function renderUsage(record) {
  const container = $("usage-bar");
  container.replaceChildren();
  const metrics = record?.dimensions?.find((item) => item.key === "efficiency")?.metrics;
  let calls = record?.model_calls?.length || 0;
  let tokens = null;
  let cost = null;
  if (metrics) {
    const parts = [metrics.agent, metrics.judge].filter(Boolean);
    calls = parts.reduce((sum, part) => sum + (part.model_calls || 0), 0);
    const used = parts.filter((part) => part.model_calls > 0);
    if (used.length && used.every((part) => typeof part.total_tokens === "number")) tokens = used.reduce((sum, part) => sum + part.total_tokens, 0);
    if (used.length && used.every((part) => typeof part.estimated_cost === "number")) cost = used.reduce((sum, part) => sum + part.estimated_cost, 0);
  } else if (state.run) {
    const events = (state.run.events || []).filter((event) => event.repeat_index === state.repeatIndex && event.type === "model_end");
    if (events.length) calls = events.length;
    const values = events.map((event) => event.data?.usage?.total_tokens);
    if (values.length && values.every((value) => typeof value === "number")) tokens = values.reduce((sum, value) => sum + value, 0);
  }
  const offline = state.run?.mode === "offline";
  [["模型调用", record ? String(calls) : "—"], ["Token", offline ? "无真实调用" : formatNumber(tokens)], ["工具调用", record ? String((record.trace || []).length) : "—"], ["费用", cost === null ? "未知" : "约 ¥" + cost.toFixed(5)]].forEach(([label, value]) => container.append(append(node("span", "", label), node("b", "", value))));
  container.title = "Token 与估算费用包含 Agent 和已完成的裁判请求；未配置单价时费用未知。Mock 工具耗时不代表生产服务。";
}
function latestStage() {
  if (!state.run) return "等待开始运行";
  if (state.run.status === "completed") return "执行与评测已结束，可点击维度查看依据";
  if (state.run.status === "failed") return "执行中断，请查看错误信息";
  const event = (state.run.events || []).at(-1);
  if (!event) return "已提交任务，等待执行";
  const prefix = state.run.repeat > 1 ? "第 " + ((event.repeat_index || 0) + 1) + " 次 · " : "";
  const names = {start: "准备执行", user: "进入新一轮对话", model_start: "等待模型响应", model_end: "已收到模型响应", tool: "工具已返回", assistant: "本轮回答完成", judge_start: "LLM 裁判正在评分", judge_end: "语义评分完成", agent_end: "正在计算七维指标", evaluated: "本次评测完成"};
  return prefix + (event.type === "tool" ? (event.data?.entry?.tool || "工具") + " 已返回" : names[event.type] || "正在执行");
}
function renderClock() {
  if (!state.run) { $("elapsed").textContent = "—"; return; }
  let value = state.run.elapsed_ms;
  if (state.busy && state.run.started_at) {
    const started = typeof state.run.started_at === "number" ? state.run.started_at * (state.run.started_at < 1e12 ? 1000 : 1) : Date.parse(state.run.started_at);
    if (Number.isFinite(started)) value = Math.max(value || 0, Date.now() - started);
  }
  $("elapsed").textContent = seconds(value);
}
function render() {
  const selected = selectedCase();
  if (selected) { $("case-title").textContent = selected.title; $("case-description").textContent = selected.description; }
  syncControls();
  const run = state.run;
  const status = run?.status || "ready";
  $("run-status").textContent = {ready: "准备就绪", queued: "已排队", running: "运行中", completed: "运行完成", failed: "运行失败"}[status] || status;
  $("status-dot").className = "status-dot " + status;
  $("run-stage").textContent = latestStage();
  renderClock();
  $("report-link").hidden = !run?.report_url;
  if (run?.report_url) {
    // Only same-origin reports are linked, even if a malformed response is received.
    const url = new URL(run.report_url, location.origin);
    if (url.origin === location.origin) $("report-link").href = url.href;
    else $("report-link").hidden = true;
  }
  const records = recordsFromRun(run);
  const indices = [...records.keys()].sort((a, b) => a - b);
  const view = $("repeat-view");
  view.replaceChildren();
  indices.forEach((index) => {
    const option = node("option", "", "第 " + (index + 1) + " 次");
    option.value = String(index);
    option.selected = index === state.repeatIndex;
    view.append(option);
  });
  $("repeat-view-wrap").hidden = !(run && run.repeat > 1 && indices.length);
  const record = records.get(state.repeatIndex) || null;
  $("tool-count").textContent = String((record?.trace || []).length);
  $("source-badge").textContent = !run ? "待运行" : run.mode === "offline" ? "预设脚本" : "真实 API";
  $("source-badge").classList.toggle("offline", run?.mode === "offline");
  const container = $("execution-content");
  const open = new Set([...container.querySelectorAll("details[open]")].map((details) => details.dataset.key));
  const scrollTop = container.scrollTop;
  const nearEnd = container.scrollHeight - scrollTop - container.clientHeight < 80;
  container.replaceChildren();
  if (state.tab === "tools") renderTools(container, record);
  else if (state.tab === "evidence") renderEvidence(container, record);
  else renderConversation(container, record);
  container.querySelectorAll("details[data-key]").forEach((details) => { details.open = open.has(details.dataset.key); });
  container.scrollTop = state.busy && nearEnd ? container.scrollHeight : scrollTop;
  renderDimensions(record);
  renderCart(record);
  renderUsage(record);
}
function setBusy(busy) {
  state.busy = busy;
  renderCases();
  clearInterval(state.clockTimer);
  state.clockTimer = busy ? setInterval(renderClock, 250) : null;
}
async function poll(id) {
  try {
    const run = await api("/api/runs/" + encodeURIComponent(id));
    state.run = run;
    const terminal = ["completed", "failed"].includes(run.status);
    const signature = [run.status, run.events?.length || 0, run.results?.length || 0, run.error || ""].join("|");
    if (signature !== state.lastSignature || terminal) {
      state.lastSignature = signature;
      if (terminal) setBusy(false);
      render();
      if (run.error) setError(run.error);
    }
    if (!terminal) state.pollTimer = setTimeout(() => poll(id), 700);
  } catch (error) {
    setBusy(false);
    setError("获取运行状态失败：" + error.message + "。刷新页面可尝试重新连接已有任务。");
    render();
  }
}
async function startRun() {
  if (state.busy || !state.selected) return;
  setError("");
  clearTimeout(state.pollTimer);
  state.repeatIndex = 0;
  state.dimension = null;
  state.lastSignature = "";
  const mode = selectedMode();
  const repeat = Number($("repeat-count").value);
  setBusy(true);
  state.run = {status: "queued", case_id: state.selected, mode, repeat, events: [], results: [], elapsed_ms: 0};
  render();
  try {
    const result = await api("/api/runs", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({case_id: state.selected, mode, repeat, judge: $("judge-enabled").checked})});
    sessionStorage.setItem("evaluationRunId", result.run_id);
    await poll(result.run_id);
  } catch (error) {
    state.run = {...state.run, status: "failed", error: error.message};
    setBusy(false);
    setError(error.message);
    render();
  }
}
document.querySelectorAll("[data-tab]").forEach((button) => {
  button.addEventListener("click", () => {
    state.tab = button.dataset.tab;
    document.querySelectorAll("[data-tab]").forEach((tab) => {
      const active = tab === button;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", String(active));
    });
    $("execution-content").setAttribute("aria-labelledby", button.id);
    render();
  });
});
document.querySelectorAll('input[name="mode"]').forEach((input) => input.addEventListener("change", syncControls));
$("run-button").addEventListener("click", startRun);
$("repeat-view").addEventListener("change", (event) => { state.repeatIndex = Number(event.target.value); render(); });
document.querySelectorAll("[data-spec-tab]").forEach((button) => {
  button.addEventListener("click", () => { state.specTab = button.dataset.specTab; renderCaseSpec(); });
  button.addEventListener("keydown", (event) => {
    const tabs = [...document.querySelectorAll("[data-spec-tab]")];
    const index = tabs.indexOf(button);
    let next;
    if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = tabs.length - 1;
    else return;
    event.preventDefault();
    tabs[next].click();
    tabs[next].focus();
  });
});

async function init() {
  render();
  try {
    const [config, data] = await Promise.all([api("/api/config"), api("/api/cases")]);
    state.config = config;
    state.cases = Array.isArray(data.cases) ? data.cases : [];
    state.selected = state.cases[0]?.id || null;
    $("model-label").textContent = config.model || "模型未配置";
    renderCases();
    render();
    loadCaseSpec(state.selected);
    const id = sessionStorage.getItem("evaluationRunId");
    if (id) {
      try {
        const run = await api("/api/runs/" + encodeURIComponent(id));
        if (state.cases.some((item) => item.id === run.case_id) && state.selected !== run.case_id) { state.selected = run.case_id; loadCaseSpec(state.selected); }
        document.querySelectorAll('input[name="mode"]').forEach((input) => { input.checked = input.value === run.mode; });
        state.run = run;
        setBusy(!["completed", "failed"].includes(run.status));
        render();
        if (state.busy) poll(id);
      } catch (_) { sessionStorage.removeItem("evaluationRunId"); }
    }
  } catch (error) {
    $("model-label").textContent = "服务连接失败";
    setError(error.message + "。请检查本地服务是否已启动。");
  }
}
init();
