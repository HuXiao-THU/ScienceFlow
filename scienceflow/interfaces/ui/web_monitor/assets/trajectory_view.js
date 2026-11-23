// Entry point: shell, toolbar, lazy call loading and call selection.
const TRAJECTORY_CALL_CACHE_LIMIT = 8;
const TRAJECTORY_DETAIL_CACHE_LIMIT = 24;

function trajectoryPruneCache(cache, limit) {
  while (cache.size > limit) {
    cache.delete(cache.keys().next().value);
  }
}

function trajectoryResetState(key) {
  trajectoryState.key = key;
  trajectoryState.index = null;
  trajectoryState.indexSignature = "";
  trajectoryState.worker = null;
  trajectoryState.lineage = null;
  trajectoryState.calls = null;
  trajectoryState.callsSignature = "";
  trajectoryState.selectedCallId = null;
  trajectoryState.detail = null;
  trajectoryState.detailError = "";
  trajectoryState.detailLoading = false;
  trajectoryState.detailTab = "overview";
  trajectoryState.attemptIndex = 0;
  trajectoryState.onlyNew = false;
  trajectoryState.search = "";
  trajectoryState.stageFilter = "";
  trajectoryState.collapsedStages.clear();
  trajectoryState.navFilter = "";
  trajectoryState.showEphemeral = true;
  trajectoryState.showTools = true;
  trajectoryState.timeMode = "sequence";
  trajectoryState.callCache.clear();
  trajectoryState.detailCache.clear();
  trajectoryState.callsLoading = false;
  trajectoryState.callsError = "";
}

function forceTrajectoryRefresh() {
  trajectoryFresh = true;
  trajectoryState.callCache.clear();
  trajectoryState.detailCache.clear();
  refreshSoon();
}

function trajectoryChooseSelection() {
  const workers = trajectoryIndexWorkers();
  if (!workers.length) {
    trajectoryState.worker = null;
    trajectoryState.lineage = null;
    return;
  }
  const worker =
    workers.find((item) => item.id === trajectoryState.worker) || workers[0];
  trajectoryState.worker = worker.id;
  const lineages = worker.lineages || [];
  const lineage =
    lineages.find((item) => item.id === trajectoryState.lineage) ||
    lineages[0] ||
    null;
  trajectoryState.lineage = lineage?.id || null;
}

function trajectoryCallsCacheKey() {
  return `${trajectoryState.worker || ""}/${trajectoryState.lineage || ""}`;
}

function renderTrajectory(key, index) {
  if (!key) return;
  if (trajectoryState.key !== key) trajectoryResetState(key);
  if (index) {
    trajectoryState.index = index;
    trajectoryState.indexSignature = tjIndexSignature(index);
  }
  const indexData = trajectoryState.index;
  if (!indexData) {
    const sectionNode = add(view, "section", undefined, "section tj-section");
    add(sectionNode, "h2", "Agent trajectory");
    add(sectionNode, "p", "Loading trajectory index…", "tj-loading");
    return;
  }
  if (!indexData.available) {
    const sectionNode = add(view, "section", undefined, "section tj-section");
    add(sectionNode, "h2", "Agent trajectory");
    add(
      sectionNode,
      "p",
      "No agent runtime audit was found under this task. The audit files appear in workers/*/logs/agent_runtime_audit.",
      "tj-empty",
    );
    if (indexData.warnings?.length) {
      for (const warning of indexData.warnings)
        add(sectionNode, "p", warning, "tj-muted");
    }
    return;
  }
  trajectoryChooseSelection();
  const sectionNode = add(view, "section", undefined, "section tj-section");
  const summary = add(sectionNode, "div", undefined, "tj-summary-head");
  add(summary, "h2", "Agent trajectory");
  const totals = indexData.totals || {};
  add(
    summary,
    "p",
    `${totals.calls ?? 0} calls · ${totals.attempts ?? 0} attempts · ${totals.ephemeral ?? 0} ephemeral · ${totals.lineages ?? 0} lineages across ${totals.workers ?? 0} workers`,
    "tj-muted",
  );
  const body = add(sectionNode, "div", undefined, "tj-body");
  trajectoryState.navEl = add(body, "aside", undefined, "tj-nav");
  trajectoryState.mainEl = add(body, "div", undefined, "tj-main");
  trajectoryState.inspectorEl = add(body, "aside", undefined, "tj-inspector");
  trajectoryState.navEl.dataset.scrollKey = "trajectory-nav";
  trajectoryState.inspectorEl.dataset.scrollKey = "trajectory-inspector";
  renderTrajectoryNav();
  renderTrajectoryMain();
  renderTrajectoryInspector();

  const cacheKey = `${key}\0${trajectoryCallsCacheKey()}\0${trajectoryState.indexSignature}`;
  const cached = trajectoryState.callCache.get(cacheKey);
  if (cached) {
    trajectoryState.calls = cached;
    trajectoryState.callsSignature = trajectoryState.indexSignature;
    renderTrajectoryMain();
    if (trajectoryState.selectedCallId && !tjSelectedCall()) {
      trajectoryState.selectedCallId = null;
      trajectoryState.detail = null;
    }
    renderTrajectoryInspector();
    return;
  }
  trajectoryLoadCalls();
}

function trajectoryLoadCalls() {
  const key = trajectoryState.key;
  const worker = trajectoryState.worker;
  const lineage = trajectoryState.lineage;
  if (!key || !worker || !lineage) return;
  const cacheKey = `${key}\0${worker}/${lineage}\0${trajectoryState.indexSignature}`;
  const cached = trajectoryState.callCache.get(cacheKey);
  if (cached) {
    trajectoryState.calls = cached;
    trajectoryState.callsSignature = trajectoryState.indexSignature;
    trajectoryState.callsLoading = false;
    trajectoryState.callsError = "";
    renderTrajectoryMain();
    renderTrajectoryInspector();
    return;
  }
  trajectoryState.callsLoading = true;
  trajectoryState.callsError = "";
  renderTrajectoryMain();
  const url = tjApiUrl(`task/${key}/trajectory/calls`, { worker, lineage });
  tjFetchJSON(url)
    .then((data) => {
      trajectoryState.callCache.set(cacheKey, data);
      trajectoryPruneCache(
        trajectoryState.callCache,
        TRAJECTORY_CALL_CACHE_LIMIT,
      );
      if (
        trajectoryState.key !== key ||
        trajectoryState.worker !== worker ||
        trajectoryState.lineage !== lineage
      ) {
        return;
      }
      trajectoryState.calls = data;
      trajectoryState.callsSignature = trajectoryState.indexSignature;
      trajectoryState.callsLoading = false;
      if (trajectoryState.selectedCallId && !tjSelectedCall()) {
        trajectoryState.selectedCallId = null;
        trajectoryState.detail = null;
      }
      renderTrajectoryMain();
      renderTrajectoryInspector();
    })
    .catch((error) => {
      if (
        trajectoryState.key !== key ||
        trajectoryState.worker !== worker ||
        trajectoryState.lineage !== lineage
      ) {
        return;
      }
      trajectoryState.callsLoading = false;
      trajectoryState.callsError = error.message || "Could not load calls";
      renderTrajectoryMain();
    });
}

function trajectoryStageIds() {
  const lineage = trajectoryCurrentLineage();
  return lineage?.stages || [];
}

function renderTrajectoryToolbar(main) {
  const toolbar = add(main, "div", undefined, "tj-toolbar");
  const search = add(toolbar, "input", undefined, "tj-search");
  search.type = "search";
  search.placeholder = "Search calls, stages, phases, tools…";
  search.value = trajectoryState.search;
  search.setAttribute("aria-label", "Search trajectory calls");
  search.oninput = () => {
    trajectoryState.search = search.value;
    renderTrajectoryLedger(
      trajectoryState.ledgerEl,
      trajectoryState.calls?.calls,
    );
  };
  const ephemeral = add(
    toolbar,
    "button",
    trajectoryState.showEphemeral ? "Ephemeral shown" : "Ephemeral hidden",
    "tj-mini-button",
  );
  ephemeral.setAttribute("aria-pressed", String(trajectoryState.showEphemeral));
  ephemeral.onclick = () => {
    trajectoryState.showEphemeral = !trajectoryState.showEphemeral;
    renderTrajectoryToolbar(main);
    renderTrajectoryLedger(
      trajectoryState.ledgerEl,
      trajectoryState.calls?.calls,
    );
  };
  const stageSelect = add(toolbar, "select", undefined, "tj-select");
  stageSelect.setAttribute("aria-label", "Filter by stage");
  add(stageSelect, "option", "All stages").value = "";
  for (const stage of trajectoryStageIds()) {
    const option = add(stageSelect, "option", stage);
    option.value = stage;
  }
  stageSelect.value = trajectoryState.stageFilter;
  stageSelect.onchange = () => {
    trajectoryState.stageFilter = stageSelect.value;
    renderTrajectoryLedger(
      trajectoryState.ledgerEl,
      trajectoryState.calls?.calls,
    );
  };
  const collapse = add(toolbar, "button", "Fold stages", "tj-mini-button");
  collapse.onclick = () => {
    const stages = trajectoryStageIds();
    if (stages.every((stage) => trajectoryState.collapsedStages.has(stage))) {
      trajectoryState.collapsedStages.clear();
    } else {
      for (const stage of stages) trajectoryState.collapsedStages.add(stage);
    }
    renderTrajectoryLedger(
      trajectoryState.ledgerEl,
      trajectoryState.calls?.calls,
    );
  };
  const tools = add(
    toolbar,
    "button",
    trajectoryState.showTools ? "Tools shown" : "Tools hidden",
    "tj-mini-button",
  );
  tools.setAttribute("aria-pressed", String(trajectoryState.showTools));
  tools.onclick = () => {
    trajectoryState.showTools = !trajectoryState.showTools;
    renderTrajectoryToolbar(main);
    renderTrajectoryLedger(
      trajectoryState.ledgerEl,
      trajectoryState.calls?.calls,
    );
  };
  const refresh = add(toolbar, "button", "Reload", "tj-mini-button");
  refresh.onclick = () => forceTrajectoryRefresh();
  const calls = trajectoryState.calls;
  if (calls?.total !== undefined) {
    add(
      toolbar,
      "small",
      `${calls.total} calls · ${calls.stats?.ephemeral ?? 0} ephemeral · ${calls.stats?.retried ?? 0} retried`,
      "tj-muted",
    );
  }
}

function renderTrajectoryMain() {
  const main = trajectoryState.mainEl;
  if (!main) return;
  tjClear(main);
  if (!trajectoryState.worker || !trajectoryState.lineage) {
    add(main, "p", "Select a worker and lineage to inspect calls.", "tj-empty");
    return;
  }
  const lineage = trajectoryCurrentLineage();
  const head = add(main, "div", undefined, "tj-main-head");
  add(
    head,
    "h3",
    `${trajectoryState.worker} · ${trajectoryState.lineage}`,
    "tj-main-title",
  );
  if (lineage) {
    add(head, "small", tjStageList(lineage.stages), "tj-muted");
    const transition = trajectoryLineageTransition(lineage);
    if (transition) {
      add(
        head,
        "small",
        `${transition.direction === "in" ? "↳" : "↗"} ${transition.text}`,
        "tj-lineage-transition",
      );
    }
  }
  if (trajectoryState.callsLoading) {
    add(main, "p", "Scanning operation logs for this lineage…", "tj-loading");
    return;
  }
  if (trajectoryState.callsError) {
    const error = add(main, "div", undefined, "tj-error-box");
    add(error, "strong", "Could not load lineage calls");
    add(error, "p", trajectoryState.callsError, "tj-muted");
    const retry = add(error, "button", "Retry", "tj-mini-button");
    retry.onclick = () => trajectoryLoadCalls();
    return;
  }
  const calls = trajectoryState.calls;
  if (!calls) {
    add(main, "p", "Loading calls…", "tj-loading");
    return;
  }
  renderTrajectoryToolbar(main);
  trajectoryState.timelineEl = add(main, "div", undefined, "tj-overview");
  renderTrajectoryTimeline(trajectoryState.timelineEl, calls.calls || []);
  trajectoryState.ledgerEl = add(main, "div", undefined, "tj-ledger-wrap");
  trajectoryState.ledgerEl.dataset.scrollKey = "trajectory-ledger";
  renderTrajectoryLedger(trajectoryState.ledgerEl, calls.calls || []);
  if (calls.warnings?.length) {
    const warning = add(main, "details", undefined, "tj-warning");
    add(warning, "summary", `${calls.warnings.length} warning(s)`);
    for (const message of calls.warnings)
      add(warning, "p", message, "tj-muted");
  }
}

function trajectoryScrollToCall(callId) {
  const rows = trajectoryState.ledgerEl?.querySelectorAll(".tj-row");
  if (!rows?.length) return;
  const row = [...rows].find((node) => node.dataset.callId === callId);
  if (row && typeof row.scrollIntoView === "function") {
    row.scrollIntoView({ block: "nearest" });
  }
}

function trajectoryHighlightSelectedCall() {
  const ledger = trajectoryState.ledgerEl;
  if (!ledger) return;
  for (const node of ledger.querySelectorAll(".tj-row.selected")) {
    node.classList.remove("selected");
  }
  if (!trajectoryState.selectedCallId) return;
  const row = [...ledger.querySelectorAll(".tj-row")].find(
    (node) => node.dataset.callId === trajectoryState.selectedCallId,
  );
  if (row) row.classList.add("selected");
}

function trajectoryPreferredAttempt(detail) {
  const attempts = detail?.attempts || [];
  for (let index = attempts.length - 1; index >= 0; index -= 1) {
    if (attempts[index].status === "completed") return index;
  }
  return 0;
}

function trajectorySelectCall(call, tab) {
  if (!call) return;
  trajectoryState.selectedCallId = call.call_id;
  trajectoryState.detailTab = tab || "overview";
  trajectoryState.attemptIndex = 0;
  trajectoryState.onlyNew = false;
  const detailKey = `${tjDetailCacheKey(call)}\0${trajectoryState.indexSignature}`;
  const cached = trajectoryState.detailCache.get(detailKey);
  if (cached) {
    trajectoryState.detail = cached;
    trajectoryState.attemptIndex = trajectoryPreferredAttempt(cached);
    trajectoryState.detailLoading = false;
    trajectoryState.detailError = "";
    if (trajectoryState.ledgerEl) {
      trajectoryHighlightSelectedCall();
      trajectoryScrollToCall(call.call_id);
    }
    renderTrajectoryInspector();
    return;
  }
  trajectoryState.detail = null;
  trajectoryState.detailLoading = true;
  trajectoryState.detailError = "";
  if (trajectoryState.ledgerEl) {
    trajectoryHighlightSelectedCall();
    trajectoryScrollToCall(call.call_id);
  }
  renderTrajectoryInspector();
  const url = tjApiUrl(`task/${trajectoryState.key}/trajectory/call`, {
    worker: trajectoryState.worker,
    session: call.session_id,
    call: call.call_id,
  });
  tjFetchJSON(url)
    .then((detail) => {
      if (trajectoryState.selectedCallId !== call.call_id) return;
      trajectoryState.detailCache.set(detailKey, detail);
      trajectoryPruneCache(
        trajectoryState.detailCache,
        TRAJECTORY_DETAIL_CACHE_LIMIT,
      );
      trajectoryState.detail = detail;
      trajectoryState.attemptIndex = trajectoryPreferredAttempt(detail);
      trajectoryState.detailLoading = false;
      trajectoryState.detailError = "";
      renderTrajectoryInspector();
    })
    .catch((error) => {
      if (trajectoryState.selectedCallId !== call.call_id) return;
      trajectoryState.detailLoading = false;
      trajectoryState.detailError =
        error.message || "Could not load call detail";
      renderTrajectoryInspector();
    });
}
