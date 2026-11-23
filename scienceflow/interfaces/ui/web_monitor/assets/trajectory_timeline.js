// Compact sequence/time overview for the selected lineage.
function trajectoryTimelineBarTitle(call) {
  const tokens = call.tokens || {};
  return [
    `#${call.index}`,
    call.stage || "?",
    call.phase_label || call.call_kind || "",
    call.summary || "",
    `in ${tjTokens(tokens.input)}`,
    `out ${tjTokens(tokens.output)}`,
    tjDuration(call.elapsed),
  ]
    .filter(Boolean)
    .join(" · ");
}

function trajectoryTimelinePosition(call, index, total, domain) {
  if (trajectoryState.timeMode === "time" && domain.span > 0) {
    const start = tjNumber(call.started_at);
    if (start !== null) {
      const duration = Math.max(0, tjNumber(call.elapsed) || 0);
      return {
        left: ((start - domain.min) / domain.span) * 100,
        width: Math.max(0.18, Math.min(100, (duration / domain.span) * 100)),
      };
    }
  }
  return {
    left: (index / Math.max(1, total)) * 100,
    width: Math.max(0.35, 100 / Math.max(1, total)),
  };
}

function renderTrajectoryTimeline(container, calls) {
  if (!container) return;
  tjClear(container);
  if (!calls?.length) {
    add(container, "p", "No calls to plot.", "tj-empty");
    return;
  }
  const head = add(container, "div", undefined, "tj-overview-head");
  add(head, "strong", "Overview");
  add(
    head,
    "small",
    `${calls.length} calls · ${calls.filter((call) => call.call_kind === "ephemeral").length} ephemeral`,
    "tj-muted",
  );
  const mode = add(
    head,
    "button",
    trajectoryState.timeMode === "time" ? "Time" : "Sequence",
    "tj-mini-button",
  );
  mode.title = "Toggle sequence or recorded wall-clock projection";
  mode.onclick = () => {
    trajectoryState.timeMode =
      trajectoryState.timeMode === "time" ? "sequence" : "time";
    renderTrajectoryTimeline(container, calls);
  };
  const legend = add(head, "div", undefined, "tj-legend");
  for (const [label, cls] of [
    ["Agent", "tj-legend-agent"],
    ["Ephemeral", "tj-legend-ephemeral"],
    ["Stage commit", "tj-legend-stage"],
    ["Failed", "tj-legend-failed"],
  ]) {
    const item = add(legend, "span");
    add(item, "i", undefined, cls);
    item.append(document.createTextNode(label));
  }

  const times = calls
    .map((call) => tjNumber(call.started_at))
    .filter((value) => value !== null);
  const min = times.length ? Math.min(...times) : 0;
  const max = times.length ? Math.max(...times) : 1;
  const domain = { min, span: Math.max(1, max - min) };

  const track = add(container, "div", undefined, "tj-timeline-track");
  const laneAgent = add(
    track,
    "div",
    undefined,
    "tj-timeline-lane tj-timeline-lane-agent",
  );
  const laneEphemeral = add(
    track,
    "div",
    undefined,
    "tj-timeline-lane tj-timeline-lane-ephemeral",
  );
  add(laneAgent, "span", "agent", "tj-lane-label");
  add(laneEphemeral, "span", "ephemeral", "tj-lane-label");

  const markers = add(track, "div", undefined, "tj-timeline-markers");
  let lastMainStage = "";
  calls.forEach((call, index) => {
    const position = trajectoryTimelinePosition(
      call,
      index,
      calls.length,
      domain,
    );
    const lane = call.call_kind === "ephemeral" ? laneEphemeral : laneAgent;
    const bar = add(lane, "button", undefined, "tj-timeline-bar");
    bar.classList.add(tjPhaseClass(call.phase));
    if (call.call_kind === "ephemeral") bar.classList.add("tj-bar-ephemeral");
    if (call.status === "failed") bar.classList.add("tj-bar-failed");
    if (call.status === "retried") bar.classList.add("tj-bar-retried");
    if (call.call_id === trajectoryState.selectedCallId)
      bar.classList.add("selected");
    bar.style.left = Math.max(0, Math.min(99.8, position.left)) + "%";
    bar.style.width = Math.max(0.35, Math.min(100, position.width)) + "%";
    bar.title = trajectoryTimelineBarTitle(call);
    bar.setAttribute("aria-label", bar.title);
    bar.onclick = () => trajectorySelectCall(call);

    if (
      call.call_kind !== "ephemeral" &&
      call.stage &&
      call.stage !== lastMainStage
    ) {
      lastMainStage = call.stage;
      const marker = add(markers, "span", call.stage, "tj-stage-marker");
      marker.style.left = Math.max(0, Math.min(99, position.left)) + "%";
      marker.title = `Stage ${call.stage}`;
    }
  });
  if (trajectoryState.timeMode === "time") {
    const axis = add(track, "div", undefined, "tj-timeline-axis");
    add(axis, "span", tjClock(min), "tj-axis-start");
    add(axis, "span", tjClock(max), "tj-axis-end");
  }
}
