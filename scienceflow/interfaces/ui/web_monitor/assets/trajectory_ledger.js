// Chronological ledger with stage headers and nested tool rows.
function trajectoryStageMetric(stage) {
  const lineage = trajectoryCurrentLineage();
  const metrics = lineage?.stage_metrics || [];
  return metrics.find((item) => item.stage === stage) || null;
}

function trajectoryVisibleCalls(calls) {
  const query = trajectoryState.search.trim().toLowerCase();
  return (calls || []).filter((call) => {
    if (!trajectoryState.showEphemeral && call.call_kind === "ephemeral")
      return false;
    if (
      trajectoryState.stageFilter &&
      call.stage !== trajectoryState.stageFilter
    )
      return false;
    if (!query) return true;
    const haystack = [
      call.summary,
      call.stage,
      call.phase,
      call.phase_label,
      call.call_kind,
      call.turn_kind,
      call.route,
      call.session_id,
      call.status,
      ...(call.tools || []).map(
        (tool) => `${tool.name} ${tool.preview} ${tool.result_preview}`,
      ),
      ...(call.annotations || []).map((item) => JSON.stringify(item)),
    ]
      .join(" ")
      .toLowerCase();
    return haystack.includes(query);
  });
}

function trajectoryStageCounts(calls) {
  const counts = new Map();
  for (const call of calls) {
    if (!call.stage) continue;
    counts.set(call.stage, (counts.get(call.stage) || 0) + 1);
  }
  return counts;
}

function trajectoryEventCell(call) {
  const cell = el("span", undefined, "tj-event");
  const kind = add(
    cell,
    "span",
    call.call_kind === "ephemeral" ? "EPHEMERAL" : "AGENT",
    "tj-kind " + tjKindClass(call),
  );
  if (call.phase !== "normal" && call.phase_label) {
    add(cell, "span", call.phase_label, "tj-phase " + tjPhaseClass(call.phase));
  }
  if (call.status === "failed")
    add(cell, "span", "FAILED", "tj-status tj-status-failed");
  else if (call.status === "retried")
    add(cell, "span", "RETRY", "tj-status tj-status-retried");
  if (call.stage && call.stage !== trajectoryLedgerStage) {
    add(cell, "span", call.stage, "tj-stage-chip");
  }
  return cell;
}

let trajectoryLedgerStage = "";

function trajectoryToolRows(container, call) {
  if (!trajectoryState.showTools || !call.tools?.length) return;
  const list = add(container, "div", undefined, "tj-tool-list");
  for (const tool of call.tools) {
    const row = add(list, "div", undefined, "tj-tool-row");
    row.tabIndex = 0;
    row.onclick = (event) => {
      event.stopPropagation();
      trajectorySelectCall(call, "tools");
    };
    row.onkeydown = (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        trajectorySelectCall(call, "tools");
      }
    };
    add(row, "span", "↳", "tj-tool-arrow");
    add(row, "span", tool.name || "tool", "tj-tool-name");
    add(
      row,
      "span",
      tjShort(tool.preview || tool.result_preview, 160),
      "tj-tool-preview",
    );
    add(
      row,
      "span",
      tool.blocked ? "blocked" : tool.is_error ? "error" : tool.status || "ok",
      "tj-tool-status " +
        (tool.is_error || tool.blocked ? "tj-status-failed" : "tj-status-ok"),
    );
  }
}

function renderTrajectoryLedger(container, calls) {
  if (!container) return;
  tjClear(container);
  const visible = trajectoryVisibleCalls(calls);
  if (!visible.length) {
    add(
      container,
      "p",
      calls?.length
        ? "No calls match the current filter."
        : "No calls in this lineage.",
      "tj-empty",
    );
    return;
  }
  const counts = trajectoryStageCounts(visible);
  const ledger = add(container, "div", undefined, "tj-ledger");
  ledger.setAttribute("role", "table");
  const head = add(ledger, "div", undefined, "tj-ledger-head");
  head.setAttribute("role", "row");
  for (const [label, cls] of [
    ["#", "tj-col-index"],
    ["Event", "tj-col-event"],
    ["Call", "tj-col-call"],
    ["In", "tj-col-in"],
    ["Out", "tj-col-out"],
    ["Time", "tj-col-time"],
  ]) {
    const cell = add(head, "span", label, "tj-col " + cls);
    cell.setAttribute("role", "columnheader");
  }

  let currentStage = "";
  let collapsed = false;
  trajectoryLedgerStage = "";
  for (const call of visible) {
    if (
      call.session_kind === "main" &&
      call.stage &&
      call.stage !== currentStage
    ) {
      currentStage = call.stage;
      collapsed = trajectoryState.collapsedStages.has(currentStage);
      const metric = trajectoryStageMetric(currentStage);
      const stageHead = add(ledger, "div", undefined, "tj-stage-head");
      stageHead.setAttribute("role", "row");
      stageHead.tabIndex = 0;
      stageHead.onclick = () => {
        if (trajectoryState.collapsedStages.has(currentStage)) {
          trajectoryState.collapsedStages.delete(currentStage);
        } else {
          trajectoryState.collapsedStages.add(currentStage);
        }
        renderTrajectoryLedger(container, calls);
      };
      stageHead.onkeydown = (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          stageHead.onclick();
        }
      };
      add(stageHead, "span", collapsed ? "▸" : "▾", "tj-stage-caret");
      add(stageHead, "strong", currentStage, "tj-stage-name");
      add(
        stageHead,
        "span",
        `${counts.get(currentStage) || 0} calls`,
        "tj-stage-count",
      );
      if (metric && Number.isFinite(Number(metric.metric_value))) {
        const direction = metric.lower_is_better ? "↓" : "↑";
        add(
          stageHead,
          "span",
          `${metric.metric_name || "metric"} ${Number(metric.metric_value).toPrecision(6)} ${direction}`,
          "tj-stage-metric",
        );
      }
    }
    if (collapsed) continue;
    trajectoryLedgerStage = currentStage;
    const row = add(ledger, "div", undefined, "tj-row");
    row.setAttribute("role", "row");
    row.dataset.callId = call.call_id;
    row.tabIndex = 0;
    if (call.call_id === trajectoryState.selectedCallId)
      row.classList.add("selected");
    if (call.status === "failed") row.classList.add("tj-row-failed");
    if (call.status === "retried") row.classList.add("tj-row-retried");
    row.onclick = () => trajectorySelectCall(call);
    row.onkeydown = (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        trajectorySelectCall(call);
      }
    };
    add(row, "span", `#${call.index}`, "tj-index tj-col");
    const eventCell = trajectoryEventCell(call);
    eventCell.classList.add("tj-col");
    row.append(eventCell);
    const summary = add(
      row,
      "span",
      tjShort(call.summary, 220),
      "tj-summary tj-col",
    );
    summary.title = call.summary || "";
    add(row, "span", tjTokens(call.tokens?.input), "tj-metric tj-col");
    add(row, "span", tjTokens(call.tokens?.output), "tj-metric tj-col");
    add(row, "span", tjDuration(call.elapsed), "tj-time tj-col");
    trajectoryToolRows(ledger, call);
  }
}
