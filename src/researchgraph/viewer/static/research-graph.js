import { apiPrefix, readOperation } from "./api.js";
import { element } from "./dom.js";
import { subscribe } from "./events.js";

const RANK_TYPES = new Set(["blocks", "derived-from", "supersedes", "cites", "part-of"]);
const SVG = "http://www.w3.org/2000/svg";
const compareText = (a, b) => a < b ? -1 : a > b ? 1 : 0;
const compareNodes = (a, b) => compareText(a.created || "", b.created || "") || compareText(a.id, b.id);
const actorKey = actor => JSON.stringify([actor?.kind || "", actor?.name || ""]);

/** Rank a detached snapshot; presentation never changes these ranks. */
export function layoutGraph(graph) {
  const nodes = structuredClone(graph.nodes).sort(compareNodes);
  const edges = structuredClone(graph.edges);
  const byId = new Map(nodes.map(node => [node.id, node]));
  const incoming = new Map(nodes.map(node => [node.id, 0]));
  const outgoing = new Map(nodes.map(node => [node.id, []]));
  for (const node of nodes) node.rank = 0;
  for (const edge of edges) {
    if (edge.retired || !RANK_TYPES.has(edge.type) || !byId.has(edge.from) || !byId.has(edge.to)) continue;
    outgoing.get(edge.from).push(edge.to);
    incoming.set(edge.to, incoming.get(edge.to) + 1);
  }
  const ready = nodes.filter(node => incoming.get(node.id) === 0);
  const placed = [];
  const seen = new Set();
  let cursor = 0;
  while (cursor < ready.length) {
    const node = ready[cursor++];
    placed.push(node);
    seen.add(node.id);
    for (const id of outgoing.get(node.id)) {
      const next = byId.get(id);
      next.rank = Math.max(next.rank, node.rank + 1);
      incoming.set(id, incoming.get(id) - 1);
      if (incoming.get(id) === 0) ready.push(next);
    }
  }
  // Invalid I1 cycles still have a deterministic, finite presentation.
  placed.push(...nodes.filter(node => !seen.has(node.id)));
  return { nodes: placed, edges };
}

function svgElement(tag, attributes) {
  const result = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attributes)) result.setAttribute(key, String(value));
  return result;
}

export class ResearchGraph extends HTMLElement {
  static observedAttributes = ["src"];
  #graph = null;
  #profile = null;
  #digest = null;
  #layout = null;
  #selectedId = null;
  #zoom = 1;
  #request = null;
  #source = null;
  #base = null;
  #profileDirty = false;
  #lifetime = null;
  #drag = null;
  #controls = new Map();
  #status;
  #content;
  #viewport;
  #canvas;
  #sizer;
  #width = 0;
  #height = 0;

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    const stylesheet = element("link");
    stylesheet.rel = "stylesheet";
    stylesheet.href = new URL("./style.css", import.meta.url).href;
    this.shadowRoot.append(stylesheet);
    const shell = element("section", undefined, "graph-shell");
    shell.setAttribute("aria-label", "Research graph");
    shell.append(element("h2", "Research graph"), element("p", "Snapshot · type columns and dependency ranks. Select a card to identify a node.", "muted"));
    this.#status = element("p", "Loading graph…", "connection");
    this.#status.setAttribute("role", "status");
    this.#status.setAttribute("aria-live", "polite");
    this.#content = element("div");
    shell.append(this.#status, this.#content);
    this.shadowRoot.append(shell);
  }

  get graph() { return this.#graph; }
  get profile() { return this.#profile; }
  get digest() { return this.#digest; }
  get layout() { return this.#layout; }
  get selectedId() { return this.#selectedId; }
  get zoom() { return this.#zoom; }

  connectedCallback() {
    this.#lifetime = new AbortController();
    const options = { signal: this.#lifetime.signal };
    this.shadowRoot.addEventListener("change", event => {
      if (event.target.matches("input, select")) this.#renderCanvas();
    }, options);
    this.shadowRoot.addEventListener("input", event => {
      if (event.target.matches('input[type="date"]')) this.#renderCanvas();
    }, options);
    this.shadowRoot.addEventListener("click", event => this.#click(event), options);
    this.shadowRoot.addEventListener("keydown", event => this.#navigate(event), options);
    this.shadowRoot.addEventListener("pointerdown", event => this.#panStart(event), options);
    this.shadowRoot.addEventListener("pointermove", event => this.#panMove(event), options);
    for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) {
      this.shadowRoot.addEventListener(type, () => { this.#drag = null; }, options);
    }
    void this.reload();
  }

  disconnectedCallback() {
    this.#source?.close();
    this.#source = null;
    this.#request?.abort();
    this.#lifetime?.abort();
    this.#drag = null;
  }

  attributeChangedCallback(name, previous, current) {
    if (previous !== current) {
      this.#base = null;
      if (this.isConnected) void this.reload();
    }
  }

  async reload() {
    let base;
    try { base = apiPrefix(this.getAttribute("src")); }
    catch { /* The full load below reports an invalid source and discards its state. */ }
    if (this.#graph && this.#source && this.#base === base) {
      this.#profileDirty = true;
      return this.#refreshSnapshot(this.#source);
    }
    this.#source?.close();
    this.#source = null;
    this.#request?.abort();
    const request = new AbortController();
    this.#request = request;
    this.#graph = this.#profile = this.#digest = this.#layout = null;
    this.#selectedId = null;
    this.#zoom = 1;
    this.#controls.clear();
    this.#content.replaceChildren();
    this.#status.textContent = "Loading graph…";
    this.#status.classList.remove("error");
    try {
      const base = apiPrefix(this.getAttribute("src"));
      this.#base = base;
      const read = async name => (await readOperation(base, name, {}, { signal: request.signal })).value;
      const [profile, graph, digest] = await Promise.all([read("profile"), read("graph"), read("digest")]);
      if (request.signal.aborted || !this.isConnected) return;
      if (!Array.isArray(profile.node_types) || !Array.isArray(profile.gates) || !Array.isArray(profile.roles) || !Array.isArray(graph.nodes) || !Array.isArray(graph.edges) || !Array.isArray(digest.items)) {
        throw new Error("Invalid profile, graph or digest response.");
      }
      const layout = layoutGraph(graph);
      this.#profile = profile;
      this.#digest = digest;
      this.#layout = layout;
      this.#renderControls();
      this.#renderCanvas();
      this.#status.textContent = "Snapshot loaded.";
      this.#graph = graph;
      this.#profileDirty = false;
      this.#subscribe();
    } catch (error) {
      if (request.signal.aborted) return;
      request.abort();
      this.#graph = this.#profile = this.#digest = this.#layout = null;
      this.#content.replaceChildren();
      this.#status.textContent = `Unable to load graph: ${error}`;
      this.#status.classList.add("error");
    }
  }

  #subscribe() {
    let source;
    source = subscribe(this.#base, {
      onEvent: (envelope, type) => {
        if (this.#source !== source || !this.isConnected) return;
        if (type === "reset" || envelope.reset === true) this.#profileDirty = true;
        if (envelope.kind === "node" || envelope.kind === "edge") this.#applyDelta(envelope);
        else if (envelope.kind === "index" || type === "reset") void this.#refreshSnapshot(source);
      },
      onState: (state, message) => {
        if (this.#source !== source || state === "connected") return;
        this.#status.textContent = `Updates ${state}${message ? `: ${message}` : ""}. Displayed snapshot may be stale.`;
        this.#status.classList.add("error");
      },
      onReconnect: () => {
        if (this.#source !== source || !this.isConnected) return;
        this.#profileDirty = true;
        void this.#refreshSnapshot(source);
      },
    });
    this.#source = source;
  }

  #applyDelta(event) {
    if (!this.#graph) return;
    const kind = event.kind === "node" ? "nodes" : "edges";
    const record = event[event.kind];
    if (!record || record.id !== event.id) return;
    this.#request?.abort();
    const records = this.#graph[kind].filter(item => item.id !== record.id);
    records.push(record);
    records.sort((left, right) => left.created.localeCompare(right.created) || left.id.localeCompare(right.id));
    this.#graph = { ...this.#graph, [kind]: records };
    this.#layout = layoutGraph(this.#graph);
    const left = this.#viewport.scrollLeft, top = this.#viewport.scrollTop;
    const focused = this.shadowRoot.activeElement?.dataset.nodeId;
    this.#renderCanvas();
    [...this.#canvas.querySelectorAll("[data-node-id]")].find(card => card.dataset.nodeId === focused)?.focus({ preventScroll: true });
    this.#viewport.scrollTo(left, top);
  }

  async #refreshSnapshot(source) {
    this.#request?.abort();
    const request = new AbortController();
    this.#request = request;
    const refreshProfile = this.#profileDirty;
    this.#status.textContent = "Refreshing snapshot…";
    const read = async name => (await readOperation(this.#base, name, {}, { signal: request.signal })).value;
    try {
      const [graph, digest, profile] = await Promise.all([
        read("graph"), read("digest"), refreshProfile ? read("profile") : this.#profile,
      ]);
      if (request.signal.aborted || this.#source !== source || !this.isConnected) return;
      if (!Array.isArray(graph.nodes) || !Array.isArray(graph.edges) || !Array.isArray(digest.items) || !Array.isArray(profile.node_types) || !Array.isArray(profile.gates) || !Array.isArray(profile.roles)) {
        throw new Error("Invalid profile, graph or digest response.");
      }
      const layout = layoutGraph(graph);
      const filters = [...this.#controls].map(([name, control]) => [name, control.value, control.checked]);
      const left = this.#viewport.scrollLeft, top = this.#viewport.scrollTop;
      const focused = this.shadowRoot.activeElement;
      const focusedLabel = focused?.getAttribute("aria-label");
      const focusedId = focused?.dataset.nodeId;
      const focusedAction = focused?.dataset.action;
      this.#profile = profile;
      this.#digest = digest;
      this.#layout = layout;
      this.#graph = graph;
      if (!graph.nodes.some(node => node.id === this.#selectedId)) this.#selectedId = null;
      this.#controls.clear();
      this.#renderControls();
      for (const [name, value, checked] of filters) {
        const control = this.#controls.get(name);
        if (control.tagName !== "SELECT" || [...control.options].some(option => option.value === value)) control.value = value;
        control.checked = checked;
      }
      this.#renderCanvas();
      const focusTarget = this.#controls.get(focusedLabel) ||
        [...this.#canvas.querySelectorAll("[data-node-id]")].find(card => card.dataset.nodeId === focusedId) ||
        [...this.#content.querySelectorAll("[data-action]")].find(button => button.dataset.action === focusedAction);
      focusTarget?.focus({ preventScroll: true });
      this.#viewport.scrollTo(left, top);
      if (refreshProfile) this.#profileDirty = false;
      this.#status.textContent = "Snapshot refreshed.";
      this.#status.classList.remove("error");
    } catch (error) {
      if (request.signal.aborted || this.#source !== source) return;
      request.abort();
      this.#status.textContent = `Unable to refresh graph: ${error} Displayed snapshot may be stale.`;
      this.#status.classList.add("error");
    }
  }

  #control(label, kind, entries = []) {
    const wrapper = element("label", undefined, "filter");
    const control = element(kind === "select" ? "select" : "input");
    if (kind !== "select") control.type = kind;
    control.setAttribute("aria-label", label);
    if (kind === "select") {
      for (const [value, text] of entries) {
        const option = element("option", text);
        option.value = value;
        control.append(option);
      }
    }
    wrapper.append(element("span", label), control);
    this.#controls.set(label, control);
    return wrapper;
  }

  #renderControls() {
    const toolbar = element("div", undefined, "filters");
    const authors = new Map();
    for (const node of this.#layout.nodes) authors.set(actorKey(node.author), node.author);
    toolbar.append(
      this.#control("Type", "select", [["", "All types"], ...this.#profile.node_types.map(type => [type.name, type.name])]),
      this.#control("Status", "select", [["", "All statuses"], ...[...new Set(this.#layout.nodes.map(node => node.status))].sort(compareText).map(status => [status, status])]),
      this.#control("Author", "select", [["", "All authors"], ...[...authors].sort(([a], [b]) => compareText(a, b)).map(([key, actor]) => [key, `${actor?.name || "Unknown"} (${actor?.kind || "unknown"})`])]),
      this.#control("Created from", "date"), this.#control("Created through", "date"),
      this.#control("Show retired", "checkbox"), this.#control("Expand history", "checkbox"),
    );
    const actions = element("div", undefined, "view-actions");
    for (const [name, action] of [["Zoom in", "in"], ["Zoom out", "out"], ["Reset view", "reset"], ["Refresh", "reload"]]) {
      const button = element("button", name);
      button.type = "button";
      button.dataset.action = action;
      actions.append(button);
    }
    const metadata = element("details", undefined, "metadata");
    metadata.open = true;
    metadata.append(element("summary", "Profile roles and gates"), element("p", `Roles: ${this.#profile.roles.join(", ") || "None declared"}`));
    for (const gate of this.#profile.gates) {
      metadata.append(element("p", `${gate.node_type} · ${gate.transition || "activate"} · roles: ${gate.roles.join(", ")} · agents allowed: ${gate.agents_allowed === true} · conditions: ${gate.when_fields ? JSON.stringify(gate.when_fields) : "unconditional"}`));
    }
    const legend = element("div", undefined, "legend");
    legend.setAttribute("aria-label", "Edge legend");
    legend.append(element("span", "Edges: solid = rank constraint; dashed = other relationship; dotted = retired. Arrows point from source to target."));
    const types = [...new Set(this.#layout.edges.map(edge => edge.type))].sort(compareText);
    for (const type of types) legend.append(element("span", type, RANK_TYPES.has(type) ? "legend-rank" : "legend-other"));
    this.#viewport = element("div", undefined, "viewport");
    this.#viewport.dataset.viewport = "";
    this.#viewport.tabIndex = 0;
    this.#viewport.setAttribute("aria-label", "Graph viewport; scroll or drag blank canvas to pan");
    this.#sizer = element("div", undefined, "canvas-sizer");
    this.#canvas = element("div", undefined, "graph-canvas");
    this.#sizer.append(this.#canvas);
    this.#viewport.append(this.#sizer);
    this.#content.replaceChildren(toolbar, actions, metadata, legend, this.#viewport);
  }

  #visibleNodes() {
    const value = name => this.#controls.get(name).value;
    const checked = name => this.#controls.get(name).checked;
    const history = new Set(this.#layout.edges.filter(edge => edge.type === "supersedes").map(edge => edge.to));
    const from = value("Created from") ? Date.parse(`${value("Created from")}T00:00:00Z`) : -Infinity;
    const through = value("Created through") ? Date.parse(`${value("Created through")}T00:00:00Z`) + 86400000 : Infinity;
    return this.#layout.nodes.filter(node => {
      const created = Date.parse(node.created);
      return (checked("Show retired") || node.status !== "retired") &&
        (checked("Expand history") || !history.has(node.id)) &&
        (!value("Type") || node.type === value("Type")) &&
        (!value("Status") || node.status === value("Status")) &&
        (!value("Author") || actorKey(node.author) === value("Author")) &&
        created >= from && created < through;
    });
  }

  #renderCanvas() {
    if (!this.#layout) return;
    const nodes = this.#visibleNodes();
    const types = this.#profile.node_types;
    const positions = new Map();
    const nextRow = new Map();
    const columnWidth = 280;
    let bottom = 0;
    this.#canvas.replaceChildren();
    types.forEach((type, index) => {
      const header = element("header", undefined, "column-heading");
      header.dataset.columnType = type.name;
      header.dataset.claimable = String(type.claimable === true);
      header.style.left = `${index * columnWidth + 16}px`;
      header.append(element("h3", type.name), element("span", `${type.prefix} · ${type.claimable ? "Claimable" : "Not claimable"}`));
      this.#canvas.append(header);
    });
    for (const node of nodes) {
      const column = Math.max(0, types.findIndex(type => type.name === node.type));
      const row = Math.max(node.rank, nextRow.get(column) || 0);
      nextRow.set(column, row + 1);
      const x = column * columnWidth + 16;
      const y = 92 + row * 144;
      positions.set(node.id, { x, y });
      bottom = Math.max(bottom, y + 132);
      const card = element("button", undefined, "node-card");
      card.type = "button";
      card.dataset.nodeId = node.id;
      card.dataset.type = node.type;
      card.dataset.status = node.status;
      card.dataset.rank = String(node.rank);
      card.setAttribute("aria-pressed", String(this.#selectedId === node.id));
      card.style.left = `${x}px`;
      card.style.top = `${y}px`;
      card.append(element("span", node.id, "node-id"), element("strong", node.title, "node-title"), element("span", node.status, "node-status"));
      this.#canvas.append(card);
    }
    this.#width = Math.max(1440, types.length * columnWidth + 32);
    this.#height = Math.max(1000, bottom + 240);
    const svg = svgElement("svg", { class: "graph-edges", width: this.#width, height: this.#height, role: "img", "aria-label": "Relationships between visible nodes" });
    const title = svgElement("title", {});
    title.textContent = "Graph relationships; each arrow is labelled by type and endpoints";
    svg.append(title);
    for (const edge of this.#layout.edges) {
      const source = positions.get(edge.from);
      const target = positions.get(edge.to);
      if (!source || !target) continue;
      const x1 = source.x + 244, y1 = source.y + 58;
      const x2 = target.x + 244, y2 = target.y + 58;
      const bend = Math.max(x1, x2) + 22 + (svg.childElementCount % 5) * 5;
      const path = svgElement("path", {
        "data-edge-id": edge.id,
        class: edge.retired ? "edge retired-edge" : RANK_TYPES.has(edge.type) ? "edge rank-edge" : "edge other-edge",
        d: `M ${x1} ${y1} C ${bend} ${y1}, ${bend} ${y2}, ${x2} ${y2} M ${x2 + 7} ${y2 - 4} L ${x2} ${y2} L ${x2 + 7} ${y2 + 4}`,
      });
      const label = `${edge.type}: ${edge.from} → ${edge.to}${edge.retired ? " (retired)" : ""}`;
      path.setAttribute("aria-label", label);
      const description = svgElement("title", {});
      description.textContent = label;
      path.append(description);
      svg.append(path);
    }
    this.#canvas.prepend(svg);
    if (!nodes.length) this.#canvas.append(element("p", "No nodes match these filters.", "empty-graph"));
    this.#scale();
  }

  #scale() {
    this.#canvas.style.width = `${this.#width}px`;
    this.#canvas.style.height = `${this.#height}px`;
    this.#canvas.style.transform = `scale(${this.#zoom})`;
    this.#sizer.style.width = `${this.#width * this.#zoom}px`;
    this.#sizer.style.height = `${this.#height * this.#zoom}px`;
  }

  #click(event) {
    const card = event.target.closest("[data-node-id]");
    if (card) {
      this.#selectedId = card.dataset.nodeId;
      for (const item of this.#canvas.querySelectorAll("[data-node-id]")) item.setAttribute("aria-pressed", String(item === card));
      const node = this.#graph.nodes.find(item => item.id === this.#selectedId);
      this.dispatchEvent(new CustomEvent("node-select", { bubbles: true, composed: true, detail: { id: this.#selectedId, node } }));
      return;
    }
    const action = event.target.closest("[data-action]")?.dataset.action;
    if (action === "reload") { void this.reload(); return; }
    if (!action) return;
    this.#zoom = action === "reset" ? 1 : Math.max(0.25, Math.min(3, this.#zoom * (action === "in" ? 1.2 : 1 / 1.2)));
    this.#scale();
    if (action === "reset") this.#viewport.scrollTo(0, 0);
  }

  #navigate(event) {
    const card = event.target.closest("[data-node-id]");
    if (!card || !["ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const cards = [...this.#canvas.querySelectorAll("[data-node-id]")];
    let index = cards.indexOf(card);
    if (event.key === "Home") index = 0;
    else if (event.key === "End") index = cards.length - 1;
    else index += ["ArrowDown", "ArrowRight"].includes(event.key) ? 1 : -1;
    event.preventDefault();
    cards[Math.max(0, Math.min(cards.length - 1, index))].focus();
  }

  #panStart(event) {
    if (event.button !== 0 || !this.#viewport?.contains(event.target) || event.target.closest("button, input, select")) return;
    this.#drag = { id: event.pointerId, x: event.clientX, y: event.clientY, left: this.#viewport.scrollLeft, top: this.#viewport.scrollTop };
    this.#viewport.setPointerCapture(event.pointerId);
    event.preventDefault();
  }

  #panMove(event) {
    if (!this.#drag || this.#drag.id !== event.pointerId) return;
    this.#viewport.scrollLeft = this.#drag.left + this.#drag.x - event.clientX;
    this.#viewport.scrollTop = this.#drag.top + this.#drag.y - event.clientY;
  }
}

customElements.define("research-graph", ResearchGraph);
