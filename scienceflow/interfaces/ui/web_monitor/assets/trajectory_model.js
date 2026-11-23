// Shared trajectory helpers: formatting, safe Markdown, JSON trees, state.
const trajectoryState = {
  key: null,
  index: null,
  indexSignature: "",
  worker: null,
  lineage: null,
  calls: null,
  callsSignature: "",
  selectedCallId: null,
  detail: null,
  detailError: "",
  detailLoading: false,
  detailTab: "overview",
  attemptIndex: 0,
  onlyNew: false,
  showEphemeral: true,
  showTools: true,
  search: "",
  timeMode: "sequence",
  stageFilter: "",
  collapsedStages: new Set(),
  navFilter: "",
  callCache: new Map(),
  detailCache: new Map(),
  navEl: null,
  mainEl: null,
  ledgerEl: null,
  timelineEl: null,
  inspectorEl: null,
  callsLoading: false,
  callsError: "",
};

let trajectoryFresh = false;

function tjNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function tjTokens(value) {
  const number = tjNumber(value);
  if (number === null) return "—";
  if (number >= 1e6) return (number / 1e6).toFixed(2) + "M";
  if (number >= 1e3) return (number / 1e3).toFixed(1) + "k";
  return String(Math.round(number));
}

function tjDuration(value) {
  const number = tjNumber(value);
  if (number === null) return "—";
  if (number >= 3600) return (number / 3600).toFixed(1) + "h";
  if (number >= 60) return (number / 60).toFixed(1) + "m";
  if (number >= 10) return number.toFixed(1) + "s";
  if (number >= 1) return number.toFixed(2) + "s";
  return Math.round(number * 1000) + "ms";
}

function tjClock(value) {
  const number = tjNumber(value);
  if (number === null) return "—";
  return new Date(number * 1000).toLocaleTimeString();
}

function tjDateTime(value) {
  const number = tjNumber(value);
  if (number === null) return "—";
  return new Date(number * 1000).toLocaleString();
}

function tjPercent(value) {
  const number = tjNumber(value);
  return number === null ? "—" : (number * 100).toFixed(1) + "%";
}

function tjShort(text, limit = 180) {
  const value = String(text ?? "")
    .replace(/\s+/g, " ")
    .trim();
  return value.length <= limit
    ? value
    : value.slice(0, Math.max(0, limit - 1)) + "…";
}

function tjClear(node) {
  if (node) node.replaceChildren();
}

function tjPhaseClass(value) {
  return "tj-phase-" + String(value || "normal").replace(/[^a-z0-9_-]/gi, "");
}

function tjKindClass(call) {
  return call?.call_kind === "ephemeral"
    ? "tj-kind-ephemeral"
    : "tj-kind-agent";
}

function tjStatusClass(status) {
  if (status === "failed") return "tj-status-failed";
  if (status === "retried") return "tj-status-retried";
  if (status === "completed") return "tj-status-ok";
  return "tj-status-unknown";
}

function tjStageList(stages) {
  return (stages || []).join(" → ") || "—";
}

function tjPhaseCounts(counts) {
  const labels = {
    stage_commit: "ST",
    compact: "CMP",
    estra: "ESTRA",
    archive: "ARC",
    context: "CTX",
    text_route: "TXT",
    resource: "RES",
    metric: "MET",
    final: "FIN",
    report: "RPT",
    merge: "MRG",
    ephemeral: "EPH",
  };
  return Object.entries(counts || {})
    .filter(([key, value]) => value && labels[key])
    .map(([key, value]) => `${labels[key]} ${value}`);
}

function tjCallIdentity(call) {
  return String(call?.call_id || "");
}

function tjSelectedCall() {
  if (!trajectoryState.calls) return null;
  const calls = trajectoryState.calls.calls || [];
  return (
    calls.find(
      (call) => tjCallIdentity(call) === trajectoryState.selectedCallId,
    ) || null
  );
}

function tjApiUrl(path, params) {
  const query = params ? "?" + new URLSearchParams(params).toString() : "";
  return base + path + query;
}

async function tjFetchJSON(url) {
  const response = await fetch(url, { signal: AbortSignal.timeout(60000) });
  let data;
  try {
    data = await response.json();
  } catch {
    data = {};
  }
  if (!response.ok) throw Error(data.error || "Request failed");
  return data;
}

function tjCopyText(text) {
  const value = String(text ?? "");
  if (navigator.clipboard?.writeText)
    return navigator.clipboard.writeText(value);
  const area = document.createElement("textarea");
  area.value = value;
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.append(area);
  area.select();
  document.execCommand("copy");
  area.remove();
  return Promise.resolve();
}

function tjCopyButton(parent, text, label = "Copy") {
  const button = add(parent, "button", label, "tj-mini-button");
  button.onclick = async (event) => {
    event.stopPropagation();
    try {
      await tjCopyText(typeof text === "function" ? text() : text);
      button.textContent = "Copied";
      setTimeout(() => (button.textContent = label), 1200);
    } catch {
      button.textContent = "Copy failed";
    }
  };
  return button;
}

function tjInline(parent, text) {
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\))/g;
  let last = 0;
  let match;
  while ((match = pattern.exec(text))) {
    if (match.index > last) {
      parent.append(document.createTextNode(text.slice(last, match.index)));
    }
    const token = match[0];
    if (token.startsWith("`")) {
      add(parent, "code", token.slice(1, -1), "tj-inline-code");
    } else if (token.startsWith("**")) {
      add(parent, "strong", token.slice(2, -2), "tj-strong");
    } else {
      const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token);
      if (link && /^(https?:|mailto:)/i.test(link[2])) {
        const anchor = add(parent, "a", link[1], "tj-link");
        anchor.href = link[2];
        anchor.target = "_blank";
        anchor.rel = "noreferrer";
      } else {
        parent.append(document.createTextNode(token));
      }
    }
    last = match.index + token.length;
  }
  if (last < text.length)
    parent.append(document.createTextNode(text.slice(last)));
}

function tjMarkdown(parent, text) {
  const source = String(text ?? "");
  if (!source.trim()) {
    add(parent, "p", "(empty)", "tj-empty");
    return;
  }
  const parts = source.split(/```/);
  parts.forEach((part, index) => {
    if (index % 2 === 1) {
      const pre = add(parent, "pre", undefined, "tj-code-block");
      const code = add(pre, "code");
      code.textContent = part.replace(/^[A-Za-z0-9_+-]*\n/, "");
      return;
    }
    let list = null;
    for (const rawLine of part.split("\n")) {
      const line = rawLine.replace(/\s+$/, "");
      if (!line.trim()) {
        list = null;
        continue;
      }
      const heading = /^(#{1,4})\s+(.*)$/.exec(line);
      if (heading) {
        list = null;
        const node = add(parent, "div", undefined, "tj-md-heading");
        tjInline(node, heading[2]);
        continue;
      }
      const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
      if (bullet) {
        if (!list) list = add(parent, "ul", undefined, "tj-md-list");
        const item = add(list, "li");
        tjInline(item, bullet[1]);
        continue;
      }
      const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
      if (numbered) {
        if (!list) list = add(parent, "ol", undefined, "tj-md-list");
        const item = add(list, "li");
        tjInline(item, numbered[1]);
        continue;
      }
      list = null;
      const paragraph = add(parent, "p", undefined, "tj-md-p");
      tjInline(paragraph, line);
    }
  });
}

function tjJsonTree(parent, value, depth = 0) {
  if (value === null || typeof value !== "object") {
    const type = value === null ? "null" : typeof value;
    if (type === "string" && value.length > 400) {
      const details = add(parent, "details", undefined, "tj-json-string");
      add(
        details,
        "summary",
        `${value.slice(0, 120)}… (${value.length} chars)`,
      );
      add(details, "pre", value);
      return;
    }
    const node = add(parent, "span", String(value), "tj-json-" + type);
    if (type === "string") node.title = value;
    return;
  }
  const isArray = Array.isArray(value);
  const entries = isArray
    ? value.map((item, index) => [index, item])
    : Object.entries(value);
  const details = add(parent, "details", undefined, "tj-json-node");
  details.open = depth < 1;
  add(
    details,
    "summary",
    `${isArray ? "Array" : "Object"} ${isArray ? `[${entries.length}]` : `{${entries.length}}`}`,
    "tj-json-summary",
  );
  const body = add(details, "div", undefined, "tj-json-body");
  for (const [key, child] of entries) {
    const row = add(body, "div", undefined, "tj-json-row");
    add(row, "span", String(key), "tj-json-key");
    tjJsonTree(row, child, depth + 1);
  }
}

function tjBadge(parent, text, cls) {
  return add(parent, "span", text, "tj-badge " + (cls || ""));
}

function tjDetailCacheKey(call) {
  return `${call.session_id}\0${call.call_id}`;
}

function tjIndexSignature(index) {
  if (!index) return "";
  return JSON.stringify([
    index.generated_at,
    (index.workers || []).map((worker) => [
      worker.id,
      worker.call_count,
      (worker.lineages || []).map((lineage) => [
        lineage.id,
        lineage.call_count,
      ]),
    ]),
  ]);
}
