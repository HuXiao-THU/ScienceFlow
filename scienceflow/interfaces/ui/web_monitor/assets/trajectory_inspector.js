// Inspector drawer: full request context, response, tools, usage and raw JSON.
function trajectoryAttempt(detail) {
  if (!detail?.attempts?.length) return null;
  const index = Math.min(
    Math.max(0, trajectoryState.attemptIndex),
    detail.attempts.length - 1,
  );
  return detail.attempts[index] || detail.attempts[0];
}

function trajectoryDefinitionList(parent, rows) {
  const grid = add(parent, "div", undefined, "tj-kv");
  for (const [label, value] of rows) {
    if (value === undefined || value === null || value === "") continue;
    add(grid, "span", label, "tj-kv-key");
    add(grid, "span", String(value), "tj-kv-value");
  }
}

function trajectoryPre(parent, text, cls = "") {
  const value = String(text ?? "");
  if (!value) {
    add(parent, "p", "(empty)", "tj-empty");
    return;
  }
  if (value.length > 20000) {
    const details = add(parent, "details", undefined, "tj-long-text");
    add(details, "summary", `${value.length} chars · click to expand`);
    const pre = add(details, "pre", undefined, "tj-pre " + cls);
    pre.textContent = value;
    return;
  }
  const pre = add(parent, "pre", undefined, "tj-pre " + cls);
  pre.textContent = value;
}

function trajectoryContentBlocks(parent, content) {
  if (content === null || content === undefined) {
    add(parent, "p", "(empty)", "tj-empty");
    return;
  }
  if (typeof content === "string") {
    tjMarkdown(parent, content);
    return;
  }
  if (!Array.isArray(content)) {
    tjJsonTree(parent, content);
    return;
  }
  for (const block of content) {
    if (typeof block === "string") {
      tjMarkdown(parent, block);
      continue;
    }
    if (!block || typeof block !== "object") {
      add(parent, "p", String(block), "tj-md-p");
      continue;
    }
    if (block.type === "text" && typeof block.text === "string") {
      tjMarkdown(parent, block.text);
    } else if (block.type === "image" || block.type === "image_url") {
      add(parent, "p", "[image attachment]", "tj-muted");
    } else {
      tjJsonTree(parent, block);
    }
  }
}

function trajectoryMessageNode(parent, message, options = {}) {
  const box = add(parent, "div", undefined, "tj-msg");
  if (options.isNew) box.classList.add("tj-msg-new");
  const head = add(box, "div", undefined, "tj-msg-head");
  const role = String(message?.role || "unknown");
  add(
    head,
    "span",
    role,
    "tj-role tj-role-" + role.replace(/[^a-z0-9_-]/gi, ""),
  );
  if (options.index !== undefined)
    add(head, "span", `#${options.index}`, "tj-msg-index");
  if (message?.name) add(head, "span", message.name, "tj-msg-name");
  if (message?.tool_call_id) {
    add(head, "span", `call ${message.tool_call_id}`, "tj-msg-tool-id");
  }
  if (options.isNew) add(head, "span", "NEW", "tj-new-badge");
  const body = add(box, "div", undefined, "tj-msg-body");
  const content = message?.content;
  if (typeof content === "string" && role === "tool") {
    trajectoryPre(body, content, "tj-tool-text");
  } else {
    trajectoryContentBlocks(body, content);
  }
  if (message?.reasoning_content) {
    const details = add(body, "details", undefined, "tj-reasoning");
    add(details, "summary", "reasoning", "tj-summary-label");
    tjMarkdown(details, message.reasoning_content);
  }
  if (Array.isArray(message?.tool_calls) && message.tool_calls.length) {
    const calls = add(body, "div", undefined, "tj-msg-calls");
    for (const tool of message.tool_calls) {
      const fn = tool?.function || {};
      const details = add(calls, "details", undefined, "tj-tool-call");
      add(
        details,
        "summary",
        `tool call · ${fn.name || "unknown"}`,
        "tj-summary-label",
      );
      add(details, "div", tool?.id || "", "tj-muted");
      const args =
        typeof fn.arguments === "string"
          ? parseJsonSafe(fn.arguments)
          : fn.arguments;
      tjJsonTree(details, args);
    }
  }
}

function parseJsonSafe(text) {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function trajectoryContextTab(body, detail) {
  const attempt = trajectoryAttempt(detail);
  const tools = add(body, "div", undefined, "tj-tab-tools");
  const onlyNew = add(
    tools,
    "button",
    trajectoryState.onlyNew ? "Only new context" : "Show full context",
    "tj-mini-button",
  );
  onlyNew.setAttribute("aria-pressed", String(trajectoryState.onlyNew));
  onlyNew.onclick = () => {
    trajectoryState.onlyNew = !trajectoryState.onlyNew;
    renderTrajectoryInspector();
  };
  const change = detail.context_change || {};
  add(
    tools,
    "small",
    `${change.messages ?? "—"} messages · ${change.system ?? "—"} system · +${change.new ?? "—"} · -${change.removed ?? "—"}${change.reset ? " · context reset" : ""}`,
    "tj-muted",
  );
  if (!attempt?.request) {
    add(body, "p", "This attempt has no recorded request payload.", "tj-empty");
    return;
  }
  const newSet = new Set(change.new_message_indices || []);
  const systemMessages = attempt.request.system_messages || [];
  if (systemMessages.length) {
    add(
      body,
      "h4",
      `System messages · ${systemMessages.length}`,
      "tj-section-title",
    );
    const list = add(body, "div", undefined, "tj-message-list");
    systemMessages.forEach((message, index) => {
      trajectoryMessageNode(list, message, { index });
    });
  }
  const providerMessages = attempt.request.provider_messages || [];
  add(
    body,
    "h4",
    `Provider messages · ${providerMessages.length}`,
    "tj-section-title",
  );
  const list = add(body, "div", undefined, "tj-message-list");
  let rendered = 0;
  providerMessages.forEach((message, index) => {
    const isNew = newSet.has(index);
    if (trajectoryState.onlyNew && !isNew) return;
    rendered += 1;
    trajectoryMessageNode(list, message, { index, isNew });
  });
  if (!rendered) {
    add(
      list,
      "p",
      trajectoryState.onlyNew
        ? "No new messages in this call."
        : "No provider messages.",
      "tj-empty",
    );
  }
}

function trajectoryResponseTab(body, detail) {
  const attempt = trajectoryAttempt(detail);
  if (!attempt) {
    add(body, "p", "No attempts recorded.", "tj-empty");
    return;
  }
  if (attempt.error) {
    const error = add(body, "div", undefined, "tj-error-box");
    add(error, "strong", "Attempt failed");
    add(error, "p", attempt.error.message || "", "tj-muted");
  }
  if (!attempt.response) {
    add(body, "p", "This attempt has no recorded response.", "tj-empty");
    return;
  }
  const response = attempt.response;
  trajectoryDefinitionList(body, [
    ["finish reason", response.finish_reason],
    ["usage", response.usage ? JSON.stringify(response.usage) : ""],
  ]);
  const message = response.message || {};
  add(body, "h4", "Assistant content", "tj-section-title");
  trajectoryContentBlocks(body, message.content);
  if (message.reasoning_content) {
    add(body, "h4", "Reasoning", "tj-section-title");
    const details = add(body, "details", undefined, "tj-reasoning");
    add(details, "summary", "reasoning content", "tj-summary-label");
    tjMarkdown(details, message.reasoning_content);
  }
  if (Array.isArray(message.tool_calls) && message.tool_calls.length) {
    add(
      body,
      "h4",
      `Tool calls · ${message.tool_calls.length}`,
      "tj-section-title",
    );
    for (const tool of message.tool_calls) {
      const fn = tool?.function || {};
      const details = add(body, "details", undefined, "tj-tool-call");
      details.open = true;
      add(
        details,
        "summary",
        `${fn.name || "tool"} · ${tool?.id || ""}`,
        "tj-summary-label",
      );
      tjJsonTree(details, parseJsonSafe(fn.arguments));
    }
  }
}

function trajectoryRawToolUrl(detail, tool) {
  const raw = tool.raw_output;
  if (!raw?.id) return "";
  return tjApiUrl("task/" + trajectoryState.key + "/trajectory/tool", {
    worker: detail.worker || trajectoryState.worker,
    stage: detail.stage || "",
    raw: raw.id,
  });
}

function trajectoryToolsTab(body, detail) {
  const tools = detail.tools || [];
  if (!tools.length) {
    add(body, "p", "No tool calls in this attempt.", "tj-empty");
    return;
  }
  for (const tool of tools) {
    const details = add(body, "details", undefined, "tj-tool-detail");
    details.open = true;
    const status = tool.blocked
      ? "blocked"
      : tool.is_error
        ? "error"
        : tool.status || "ok";
    add(
      details,
      "summary",
      `${tool.name || "tool"} · ${status} · ${tjShort(tool.preview || "", 120)}`,
      "tj-summary-label",
    );
    if (tool.arguments !== null && tool.arguments !== undefined) {
      add(details, "h5", "Arguments", "tj-subtitle");
      tjJsonTree(details, tool.arguments);
    } else if (tool.arguments_raw) {
      add(details, "h5", "Arguments (raw)", "tj-subtitle");
      trajectoryPre(details, tool.arguments_raw);
    }
    if (tool.rendered) {
      add(details, "h5", "Rendered result", "tj-subtitle");
      trajectoryPre(details, tool.rendered, "tj-tool-text");
    }
    const message = tool.message || {};
    if (
      typeof message.content === "string" &&
      message.content !== tool.rendered
    ) {
      add(details, "h5", "Tool message", "tj-subtitle");
      trajectoryPre(details, message.content, "tj-tool-text");
    }
    if (tool.shell_execution) {
      add(details, "h5", "Shell execution", "tj-subtitle");
      tjJsonTree(details, tool.shell_execution);
    }
    if (tool.raw_output) {
      const raw = tool.raw_output;
      const url = trajectoryRawToolUrl(detail, tool);
      const line = add(details, "div", undefined, "tj-raw-link");
      add(line, "span", `raw output · ${raw.id || "?"}`, "tj-muted");
      if (url) {
        const link = add(line, "a", "open", "tj-link");
        link.href = url;
        link.target = "_blank";
        link.rel = "noreferrer";
      } else {
        add(line, "span", "file unavailable", "tj-muted");
      }
    }
  }
}

function trajectoryUsageTab(body, detail) {
  const call = detail.call || {};
  const tokens = call.tokens || {};
  trajectoryDefinitionList(body, [
    ["input tokens", tjTokens(tokens.input)],
    ["output tokens", tjTokens(tokens.output)],
    ["cached tokens", tjTokens(tokens.cached)],
    ["cache rate", tjPercent(tokens.cache_rate)],
    ["elapsed", tjDuration(call.elapsed)],
    ["TTFT", tjDuration(call.ttft)],
    [
      "TPOT",
      call.tpot === null || call.tpot === undefined
        ? ""
        : `${(call.tpot * 1000).toFixed(2)} ms`,
    ],
    ["model", call.model],
    ["started", tjDateTime(call.started_at)],
    ["status", call.status],
  ]);
  const attempts = detail.attempts || [];
  if (attempts.length) {
    add(body, "h4", "Attempts", "tj-section-title");
    const table = add(body, "table", undefined, "tj-table");
    const head = add(table, "tr");
    for (const label of ["#", "Status", "Time", "Finish", "Usage", "Error"]) {
      add(head, "th", label);
    }
    for (const attempt of attempts) {
      const row = add(table, "tr");
      add(row, "td", String(attempt.attempt));
      add(row, "td", attempt.status || "");
      add(row, "td", tjDuration(attempt.elapsed));
      add(row, "td", attempt.response?.finish_reason || "");
      add(
        row,
        "td",
        attempt.response?.usage ? JSON.stringify(attempt.response.usage) : "",
      );
      add(row, "td", attempt.error?.message || "", "tj-error-text");
    }
  }
  if (detail.context_change) {
    add(body, "h4", "Context", "tj-section-title");
    trajectoryDefinitionList(body, [
      ["messages", detail.context_change.messages],
      ["system", detail.context_change.system],
      ["new", detail.context_change.new],
      ["removed", detail.context_change.removed],
      ["reset", detail.context_change.reset ? "yes" : "no"],
      ["previous call", detail.context_change.previous_call_id],
    ]);
  }
}

function trajectoryAnnotations(body, detail) {
  const annotations = detail.annotations || detail.call?.annotations || [];
  if (!annotations.length) return;
  add(body, "h4", "Annotations", "tj-section-title");
  const list = add(body, "div", undefined, "tj-annotations");
  for (const item of annotations) {
    const row = add(list, "div", undefined, "tj-annotation");
    add(
      row,
      "span",
      item.kind || "note",
      "tj-annotation-kind tj-annotation-" + String(item.kind || "note"),
    );
    if (item.kind === "stage_commit") {
      add(
        row,
        "span",
        `stage ${item.stage || "?"} · retries ${item.parse_retries ?? 0}${item.fallback ? " · fallback" : ""}${item.ok ? " · ok" : ""}`,
      );
    } else if (item.kind === "compact") {
      add(
        row,
        "span",
        `${item.result || item.reason || "compaction"}${item.summary_chars ? ` · ${item.summary_chars} chars` : ""}`,
      );
    } else if (item.kind === "estra") {
      add(
        row,
        "span",
        `${item.action || ""} · ${item.intent || ""}${item.target_stage ? ` → ${item.target_stage}` : ""}${item.reason ? ` · ${item.reason}` : ""}`,
      );
    } else if (item.kind === "archive") {
      add(
        row,
        "span",
        `${item.terminal_stage || ""} → ${item.target_stage || ""} · ${item.summary || ""}`,
      );
    } else if (item.kind === "failure") {
      add(
        row,
        "span",
        `attempt ${item.attempt || "?"} · ${item.error_type || "error"} · ${item.error || ""}`,
      );
    } else if (item.kind === "retry") {
      add(row, "span", `${item.attempts} attempts`);
    } else if (item.kind === "context") {
      add(row, "span", `${item.route || ""} · ${item.reason || ""}`);
    } else {
      add(row, "span", JSON.stringify(item));
    }
  }
}

function trajectoryOverviewTab(body, detail) {
  const call = detail.call || {};
  trajectoryDefinitionList(body, [
    ["worker", detail.worker || trajectoryState.worker],
    ["lineage", call.lineage || trajectoryState.lineage],
    ["stage", call.stage],
    ["session", call.session_id],
    ["session kind", call.session_kind],
    ["call kind", call.call_kind],
    ["phase", call.phase_label],
    ["turn", call.turn],
    ["model", call.model],
    ["call id", call.call_id],
    ["started", tjDateTime(call.started_at)],
    ["status", call.status],
    ["attempts", call.attempt_count],
    ["finish reason", call.finish_reason],
  ]);
  add(body, "h4", "Summary", "tj-section-title");
  const summary = add(body, "div", undefined, "tj-overview-summary");
  tjMarkdown(summary, call.summary || "");
  trajectoryAnnotations(body, detail);
  if (detail.tools?.length) {
    add(body, "h4", `Tools · ${detail.tools.length}`, "tj-section-title");
    const list = add(body, "div", undefined, "tj-overview-tools");
    for (const tool of detail.tools) {
      add(
        list,
        "div",
        `${tool.name || "tool"} · ${tool.blocked ? "blocked" : tool.is_error ? "error" : tool.status || "ok"} · ${tjShort(tool.preview || "", 120)}`,
        "tj-overview-tool",
      );
    }
  }
}

function trajectoryRawTab(body, detail) {
  const actions = add(body, "div", undefined, "tj-tab-tools");
  tjCopyButton(actions, () => JSON.stringify(detail, null, 2), "Copy JSON");
  const download = add(actions, "button", "Download JSON", "tj-mini-button");
  download.onclick = () => {
    const blob = new Blob([JSON.stringify(detail, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "scienceflow-call.json";
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  tjJsonTree(body, detail);
}

function renderTrajectoryInspector() {
  const node = trajectoryState.inspectorEl;
  if (!node) return;
  tjClear(node);
  const call = tjSelectedCall();
  if (!call) {
    add(node, "h3", "Inspector", "tj-nav-title");
    add(
      node,
      "p",
      "Select a call to inspect its request context, response and tool results.",
      "tj-empty",
    );
    return;
  }
  const head = add(node, "div", undefined, "tj-inspector-head");
  const title = add(head, "div");
  add(
    title,
    "strong",
    `#${call.index} · ${call.summary || ""}`,
    "tj-inspector-title",
  );
  add(
    title,
    "small",
    `${call.call_kind} · ${call.phase_label} · ${call.stage || "?"} · turn ${call.turn}`,
    "tj-muted",
  );
  const close = add(head, "button", "×", "tj-close-button");
  close.title = "Close inspector";
  close.onclick = () => {
    trajectoryState.selectedCallId = null;
    trajectoryState.detail = null;
    trajectoryHighlightSelectedCall();
    renderTrajectoryInspector();
  };

  if (trajectoryState.detailLoading) {
    add(node, "p", "Loading full call payload…", "tj-loading");
    return;
  }
  if (trajectoryState.detailError) {
    const error = add(node, "div", undefined, "tj-error-box");
    add(error, "strong", "Could not load call");
    add(error, "p", trajectoryState.detailError, "tj-muted");
    const retry = add(error, "button", "Retry", "tj-mini-button");
    retry.onclick = () => trajectorySelectCall(call, trajectoryState.detailTab);
    return;
  }
  const detail = trajectoryState.detail;
  if (!detail) {
    add(node, "p", "Select the call again to load its payload.", "tj-empty");
    return;
  }
  const attempts = detail.attempts || [];
  if (attempts.length > 1) {
    const tabs = add(node, "div", undefined, "tj-attempt-tabs");
    attempts.forEach((attempt, index) => {
      const button = add(
        tabs,
        "button",
        `Attempt ${attempt.attempt}${attempt.status === "failed" ? " · failed" : ""}`,
        "tj-mini-button",
      );
      if (index === trajectoryState.attemptIndex)
        button.classList.add("active");
      button.onclick = () => {
        trajectoryState.attemptIndex = index;
        renderTrajectoryInspector();
      };
    });
  }
  const tabs = add(node, "div", undefined, "tj-tabs");
  const definitions = [
    ["overview", "Overview"],
    ["context", "Context"],
    ["response", "Response"],
    [
      "tools",
      `Tools${detail.tools?.length ? ` · ${detail.tools.length}` : ""}`,
    ],
    ["usage", "Usage"],
    ["raw", "Raw"],
  ];
  for (const [value, label] of definitions) {
    const button = add(tabs, "button", label, "tj-tab");
    button.setAttribute(
      "aria-pressed",
      String(trajectoryState.detailTab === value),
    );
    if (trajectoryState.detailTab === value) button.classList.add("active");
    button.onclick = () => {
      trajectoryState.detailTab = value;
      renderTrajectoryInspector();
    };
  }
  const body = add(node, "div", undefined, "tj-tab-body");
  if (trajectoryState.detailTab === "context")
    trajectoryContextTab(body, detail);
  else if (trajectoryState.detailTab === "response")
    trajectoryResponseTab(body, detail);
  else if (trajectoryState.detailTab === "tools")
    trajectoryToolsTab(body, detail);
  else if (trajectoryState.detailTab === "usage")
    trajectoryUsageTab(body, detail);
  else if (trajectoryState.detailTab === "raw") trajectoryRawTab(body, detail);
  else trajectoryOverviewTab(body, detail);
}
