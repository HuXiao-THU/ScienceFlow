const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { JSDOM } = require("jsdom");

const root = join(__dirname, "../../scienceflow/interfaces/ui/web_monitor");
const assets = join(root, "assets");
const order = [
  ...readFileSync(join(root, "page.py"), "utf8")
    .match(/_SCRIPTS = \(([\s\S]*?)\)/)[1]
    .matchAll(/"([^"]+\.js)"/g),
].map((match) => match[1]);
const source = order
  .map((name) => readFileSync(join(assets, name), "utf8"))
  .join("\n")
  .replace(/refresh\(\);\s*$/, "");

const tasks = {
  tasks: [{ key: "1", name: "Demo run", status: "completed" }],
  resources: "",
  updated_at: 1,
};
const index = {
  available: true,
  totals: { workers: 2, calls: 4, attempts: 4, ephemeral: 1, lineages: 2 },
  warnings: [],
  workers: [
    {
      id: "W00",
      call_count: 3,
      attempt_count: 4,
      ephemeral_count: 1,
      lineage_count: 1,
      session_count: 2,
      lineages: [
        {
          id: "L01",
          stages: ["S01"],
          call_count: 3,
          ephemeral_count: 1,
          main_session_count: 1,
          session_count: 2,
          best_metric: { name: "test_f1", value: 0.5, stage: "S01" },
          stage_metrics: [
            {
              stage: "S01",
              metric_name: "test_f1",
              metric_value: 0.5,
              lower_is_better: false,
            },
          ],
          phase_counts: { stage_commit: 1, estra: 1 },
          transition_in: null,
          transition_out: null,
          sessions: [],
        },
      ],
    },
    {
      id: "W01",
      call_count: 1,
      attempt_count: 1,
      ephemeral_count: 0,
      lineage_count: 1,
      session_count: 1,
      lineages: [
        {
          id: "L09",
          stages: ["S08"],
          call_count: 1,
          ephemeral_count: 0,
          main_session_count: 1,
          session_count: 1,
          best_metric: null,
          stage_metrics: [],
          phase_counts: {},
          transition_in: null,
          transition_out: null,
          sessions: [],
        },
      ],
    },
  ],
};
const call = (overrides) => ({
  index: 1,
  call_id: "sess:llm:1",
  session_id: "sess",
  session: "sess",
  session_kind: "main",
  call_kind: "agent",
  turn_kind: "tool",
  stage: "S01",
  lineage: "L01",
  turn: 1,
  model: "deepseek-flash",
  started_at: 1000,
  ended_at: 1003,
  elapsed: 3.2,
  ttft: 1.1,
  tokens: { input: 80, output: 12, cached: 10, cache_rate: 0.1, known: true },
  finish_reason: "tool_calls",
  status: "completed",
  attempt_count: 1,
  attempts: [{ attempt: 1, phase: "completed" }],
  tool_names: ["bash"],
  phase: "normal",
  phase_label: "AGENT",
  annotations: [],
  attempt_errors: [],
  summary: "bash · echo hi",
  tools: [
    {
      id: "call_1",
      name: "bash",
      preview: "echo hi",
      status: "ok",
      is_error: false,
      blocked: false,
      result_preview: "hi",
    },
  ],
  context: {
    messages: 3,
    system: 1,
    new: 2,
    removed: 0,
    reset: false,
    previous_call_id: null,
    new_message_indices: [1],
  },
  has_detail: true,
  ...overrides,
});
const calls = {
  lineage: "L01",
  total: 3,
  warnings: [],
  stats: { calls: 3, ephemeral: 1, retried: 1, failed: 0, tokens: {} },
  calls: [
    call({}),
    call({
      index: 2,
      call_id: "sess:llm:2",
      turn: 2,
      turn_kind: "qa",
      phase: "stage_commit",
      phase_label: "STAGE COMMIT",
      summary: "stage bookkeeping · S01",
      status: "retried",
      attempt_count: 2,
      attempts: [
        { attempt: 1, phase: "failed" },
        { attempt: 2, phase: "completed" },
      ],
      annotations: [
        {
          kind: "stage_commit",
          stage: "S01",
          parse_retries: 1,
          fallback: true,
        },
      ],
      tools: [],
    }),
    call({
      index: 3,
      call_id: "estra:llm:1",
      session_id: "estra",
      session: "estra",
      session_kind: "ephemeral",
      call_kind: "ephemeral",
      turn_kind: "estra",
      stage: "S02",
      turn: 1,
      phase: "estra",
      phase_label: "ESTRA",
      summary: "ESTRA · switch_stage · redirect",
      status: "completed",
      annotations: [
        { kind: "estra", action: "switch_stage", intent: "redirect" },
      ],
      tools: [],
    }),
  ],
};
const normalDetail = {
  worker: "W00",
  session: "sess",
  stage: "S01",
  lineage: "L01",
  call: calls.calls[0],
  attempts: [
    {
      attempt: 1,
      status: "completed",
      request: {
        model: "deepseek-flash",
        call_kind: "agent",
        turn_kind: "tool",
        route: null,
        system_messages: [{ role: "system", content: "system prompt" }],
        provider_messages: [
          { role: "user", content: "task prompt" },
          {
            role: "assistant",
            content: "",
            tool_calls: [
              {
                id: "call_1",
                function: { name: "bash", arguments: '{"command":"echo hi"}' },
              },
            ],
          },
        ],
      },
      response: {
        finish_reason: "tool_calls",
        message: {
          role: "assistant",
          content: "",
          tool_calls: [
            {
              id: "call_1",
              function: { name: "bash", arguments: '{"command":"echo hi"}' },
            },
          ],
        },
        usage: { input_tokens: 80, output_tokens: 12 },
      },
      error: null,
    },
  ],
  tools: [
    {
      id: "call_1",
      name: "bash",
      arguments: { command: "echo hi" },
      arguments_raw: '{"command":"echo hi"}',
      preview: "echo hi",
      status: "ok",
      is_error: false,
      blocked: false,
      rendered: "hi",
      message: { role: "tool", tool_call_id: "call_1", content: "hi" },
      shell_execution: null,
      raw_output: { id: "tool_000001_bash.txt", path: "x", chars: 3 },
    },
  ],
  annotations: [],
  context_change: {
    messages: 3,
    system: 1,
    new: 1,
    removed: 0,
    reset: false,
    previous_call_id: null,
    new_message_indices: [1],
  },
};
const retryDetail = {
  ...normalDetail,
  call: calls.calls[1],
  attempts: [
    {
      attempt: 1,
      status: "failed",
      request: null,
      response: null,
      error: { type: "BadRequestError", message: "bad reasoning replay" },
    },
    {
      attempt: 2,
      status: "completed",
      request: {
        model: "deepseek-flash",
        system_messages: [{ role: "system", content: "system prompt" }],
        provider_messages: [{ role: "user", content: "stage commit prompt" }],
      },
      response: {
        finish_reason: "stop",
        message: { role: "assistant", content: "stage commit text" },
        usage: { input_tokens: 90, output_tokens: 5 },
      },
      error: null,
    },
  ],
  tools: [],
  annotations: [{ kind: "stage_commit", stage: "S01", parse_retries: 1 }],
};

function fixture(t) {
  const dom = new JSDOM(
    readFileSync(join(assets, "shell.html"), "utf8").replace(
      "__SCIENCEFLOW_CSS__",
      "",
    ),
    {
      runScripts: "outside-only",
      url: "http://localhost/capability/#task/1/trajectory",
      pretendToBeVisual: true,
    },
  );
  t.after(() => dom.window.close());
  const w = dom.window;
  w.AbortSignal.timeout = () => new w.AbortController().signal;
  w.fetch = async (url) => {
    const decoded = decodeURIComponent(url);
    let body;
    if (url.includes("/api")) body = tasks;
    else if (url.includes("/trajectory/calls")) body = calls;
    else if (url.includes("/trajectory/call")) {
      body = decoded.includes("llm:2") ? retryDetail : normalDetail;
    } else if (url.includes("/trajectory")) body = index;
    else if (url.includes("/report")) body = { available: false };
    else body = { points: [], events: [], lineage: [] };
    return { ok: true, json: async () => body };
  };
  w.eval(
    `(()=>{'use strict';\n${source}\nwindow.testTrajectory={refresh,trajectoryState};})();`,
  );
  return { w, doc: w.document, api: w.testTrajectory };
}

const wait = (ms = 40) => new Promise((resolve) => setTimeout(resolve, ms));
const text = (node) => (node?.textContent || "").trim();

test("trajectory tab renders workers, lineages, calls and ephemeral labels", async (t) => {
  const { doc, api } = fixture(t);
  await api.refresh();
  await wait();
  assert.deepEqual([...doc.querySelectorAll(".task-tab")].map(text), [
    "Run monitor",
    "Research report",
    "Agent trajectory",
  ]);
  assert.equal(doc.querySelectorAll(".tj-lineage").length, 1);
  assert.equal(doc.querySelectorAll(".tj-row").length, 3);
  assert.equal(doc.querySelectorAll(".tj-stage-head").length, 1);
  assert.equal(doc.querySelectorAll(".tj-kind-ephemeral").length, 1);
  assert.equal(
    doc.querySelectorAll(".tj-row .tj-phase-stage_commit").length,
    1,
  );
  assert.equal(doc.querySelectorAll(".tj-row .tj-phase-estra").length, 1);
  assert.equal(doc.querySelectorAll(".tj-tool-row").length, 1);
  assert.match(text(doc.querySelector(".tj-lineage")), /L01/);
  assert.match(text(doc.querySelector(".tj-lineage")), /ST 1/);
});

test("worker tabs switch the active worker and lineage", async (t) => {
  const { doc, api } = fixture(t);
  await api.refresh();
  await wait();
  assert.deepEqual([...doc.querySelectorAll(".tj-worker-tab")].map(text), [
    "W00",
    "W01",
  ]);
  const w01 = [...doc.querySelectorAll(".tj-worker-tab")].find(
    (node) => text(node) === "W01",
  );
  w01.click();
  await wait();
  assert.match(text(doc.querySelector(".tj-main-title")), /W01 · L09/);
  assert.equal(doc.querySelectorAll(".tj-worker-tab.active").length, 1);
});

test("inspector opens the request context, tools and raw JSON", async (t) => {
  const { doc, api } = fixture(t);
  await api.refresh();
  await wait();
  const row = [...doc.querySelectorAll(".tj-row")].find(
    (node) => node.dataset.callId === "sess:llm:1",
  );
  row.click();
  await wait();
  assert.match(text(doc.querySelector(".tj-inspector-title")), /echo hi/);

  const tab = (label) =>
    [...doc.querySelectorAll(".tj-tab")].find((node) => text(node) === label);
  tab("Context").click();
  await wait();
  assert.equal(doc.querySelectorAll(".tj-msg").length, 3);
  assert.equal(doc.querySelectorAll(".tj-msg-new").length, 1);

  tab("Tools · 1").click();
  await wait();
  assert.equal(doc.querySelectorAll(".tj-tool-detail").length, 1);
  assert.equal(doc.querySelectorAll(".tj-raw-link a").length, 1);

  tab("Raw").click();
  await wait();
  assert.ok(doc.querySelectorAll(".tj-json-node").length > 0);
});

test("retried stage commit call shows both attempts and the failure error", async (t) => {
  const { doc, api } = fixture(t);
  await api.refresh();
  await wait();
  const row = [...doc.querySelectorAll(".tj-row")].find(
    (node) => node.dataset.callId === "sess:llm:2",
  );
  row.click();
  await wait();
  assert.equal(doc.querySelectorAll(".tj-attempt-tabs button").length, 2);
  assert.ok(
    text(doc.querySelector(".tj-inspector")).includes("stage bookkeeping"),
  );
  const usage = [...doc.querySelectorAll(".tj-tab")].find(
    (node) => text(node) === "Usage",
  );
  usage.click();
  await wait();
  assert.ok(
    text(doc.querySelector(".tj-error-text")).includes("bad reasoning replay"),
  );
});
