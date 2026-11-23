// Worker / lineage navigation for the trajectory view.
function trajectoryIndexWorkers() {
  return trajectoryState.index?.workers || [];
}

function trajectoryCurrentWorker() {
  const workers = trajectoryIndexWorkers();
  return (
    workers.find((worker) => worker.id === trajectoryState.worker) ||
    workers[0] ||
    null
  );
}

function trajectoryCurrentLineage() {
  const worker = trajectoryCurrentWorker();
  if (!worker) return null;
  return (
    (worker.lineages || []).find(
      (lineage) => lineage.id === trajectoryState.lineage,
    ) ||
    (worker.lineages || [])[0] ||
    null
  );
}

function trajectorySelectWorker(workerId) {
  const worker = trajectoryIndexWorkers().find((item) => item.id === workerId);
  if (!worker) return;
  const lineage = (worker.lineages || [])[0];
  trajectorySelectLineage(worker.id, lineage?.id || "");
}

function trajectorySelectLineage(workerId, lineageId) {
  trajectoryState.worker = workerId;
  trajectoryState.lineage = lineageId;
  trajectoryState.stageFilter = "";
  trajectoryState.collapsedStages.clear();
  trajectoryState.selectedCallId = null;
  trajectoryState.detail = null;
  trajectoryState.detailError = "";
  trajectoryState.detailLoading = false;
  trajectoryState.calls = null;
  renderTrajectoryNavWithScroll();
  renderTrajectoryMain();
  renderTrajectoryInspector();
  trajectoryLoadCalls();
}

function renderTrajectoryNavWithScroll() {
  const top = trajectoryState.navEl?.scrollTop || 0;
  renderTrajectoryNav();
  if (trajectoryState.navEl) trajectoryState.navEl.scrollTop = top;
}

function trajectoryLineageTransition(lineage) {
  const incoming = lineage.transition_in;
  const outgoing = lineage.transition_out;
  if (incoming) {
    const target = incoming.target_stage
      ? ` · target ${incoming.target_stage}`
      : incoming.target_node_uid
        ? ` · ${incoming.target_node_uid}`
        : "";
    return {
      direction: "in",
      text: `from ${incoming.from || "?"} · ${incoming.kind}${target}`,
    };
  }
  if (outgoing) {
    return {
      direction: "out",
      text: `to ${outgoing.to || "?"} · ${outgoing.kind}`,
    };
  }
  return null;
}

function trajectoryNavWorkerSummary(worker) {
  const parts = [
    `${worker.call_count ?? 0} calls`,
    `${worker.lineage_count ?? 0} lineages`,
  ];
  if (worker.ephemeral_count) parts.push(`${worker.ephemeral_count} ephemeral`);
  return parts.join(" · ");
}

function renderTrajectoryNav() {
  const node = trajectoryState.navEl;
  if (!node) return;
  tjClear(node);
  const index = trajectoryState.index;
  add(node, "h3", "Workers", "tj-nav-title");
  if (!index?.available) {
    add(
      node,
      "p",
      "No agent runtime audit was found under this task.",
      "tj-empty",
    );
    return;
  }
  const head = add(node, "div", undefined, "tj-nav-head");
  const refresh = add(head, "button", "Refresh", "tj-mini-button");
  refresh.onclick = () => forceTrajectoryRefresh();
  add(
    head,
    "small",
    `${index.totals?.calls ?? 0} calls · ${index.totals?.ephemeral ?? 0} ephemeral`,
    "tj-muted",
  );

  const workerTabs = add(node, "div", undefined, "tj-worker-tabs");
  for (const worker of trajectoryIndexWorkers()) {
    const button = add(workerTabs, "button", worker.id, "tj-worker-tab");
    button.title = trajectoryNavWorkerSummary(worker);
    button.setAttribute(
      "aria-pressed",
      String(worker.id === trajectoryState.worker),
    );
    if (worker.id === trajectoryState.worker) button.classList.add("active");
    button.onclick = () => trajectorySelectWorker(worker.id);
  }

  const search = add(node, "input", undefined, "tj-nav-search");
  search.placeholder = "Find lineage…";
  search.value = trajectoryState.navFilter;
  search.setAttribute("aria-label", "Find lineage");
  search.oninput = () => {
    trajectoryState.navFilter = search.value;
    renderTrajectoryNavWithScroll();
    const next = trajectoryState.navEl?.querySelector(".tj-nav-search");
    if (next) {
      next.focus();
      next.setSelectionRange(next.value.length, next.value.length);
    }
  };

  const worker = trajectoryCurrentWorker();
  if (!worker) {
    add(node, "p", "No worker audit records.", "tj-empty");
    return;
  }
  add(node, "div", trajectoryNavWorkerSummary(worker), "tj-worker-summary");
  const filter = trajectoryState.navFilter.trim().toLowerCase();
  const lineages = (worker.lineages || []).filter(
    (lineage) =>
      !filter ||
      String(lineage.id || "")
        .toLowerCase()
        .includes(filter) ||
      (lineage.stages || []).join(" ").toLowerCase().includes(filter),
  );
  if (!lineages.length) {
    add(node, "p", "No matching lineage.", "tj-empty");
    return;
  }
  const list = add(node, "div", undefined, "tj-lineage-list");
  for (const lineage of lineages) {
    const card = add(list, "button", undefined, "tj-lineage");
    card.setAttribute(
      "aria-pressed",
      String(lineage.id === trajectoryState.lineage),
    );
    if (lineage.id === trajectoryState.lineage) card.classList.add("active");
    card.onclick = () => trajectorySelectLineage(worker.id, lineage.id);

    const top = add(card, "div", undefined, "tj-lineage-top");
    add(top, "strong", lineage.id, "tj-lineage-id");
    add(top, "span", tjStageList(lineage.stages), "tj-lineage-stages");
    const counts = add(card, "div", undefined, "tj-lineage-counts");
    add(
      counts,
      "span",
      `${lineage.call_count ?? 0} calls · ${lineage.ephemeral_count ?? 0} ephemeral`,
    );
    if (
      lineage.best_metric?.value !== null &&
      lineage.best_metric?.value !== undefined &&
      Number.isFinite(Number(lineage.best_metric.value))
    ) {
      add(
        counts,
        "span",
        `${lineage.best_metric.name || "metric"} ${Number(lineage.best_metric.value).toPrecision(5)}`,
        "tj-lineage-metric",
      );
    }
    const phases = tjPhaseCounts(lineage.phase_counts);
    if (phases.length) {
      const badges = add(card, "div", undefined, "tj-lineage-badges");
      for (const label of phases)
        add(badges, "span", label, "tj-lineage-badge");
    }
    const transition = trajectoryLineageTransition(lineage);
    if (transition) {
      add(
        card,
        "div",
        `${transition.direction === "in" ? "↳" : "↗"} ${transition.text}`,
        "tj-lineage-transition",
      );
    }
  }
  if (index.warnings?.length) {
    const warning = add(node, "details", undefined, "tj-nav-warning");
    add(warning, "summary", `${index.warnings.length} warning(s)`);
    for (const message of index.warnings)
      add(warning, "p", message, "tj-muted");
  }
}
