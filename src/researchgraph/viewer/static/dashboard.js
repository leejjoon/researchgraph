import { apiPrefix, readOperation } from "./api.js";
import { element } from "./dom.js";
import { subscribe } from "./events.js";

const CATEGORIES = ["retired", "done", "failed", "ready", "blocked", "running", "proposed", "other"];
const DIGEST_KINDS = ["running", "ready", "blocked", "awaiting", "failed_24h", "claims"];
const WEEK = 7 * 24 * 60 * 60 * 1000;

export class ResearchDashboard extends HTMLElement {
  static observedAttributes = ["src"];
  #snapshot = null;
  #profile = null;
  #request = null;
  #subscription = null;
  #epoch = 0;
  #revision = 0;
  #dirty = true;
  #state = "disconnected";
  #error = null;
  #streamError = null;
  #lastIndex = null;
  #lastEvent = null;
  #log = [];
  #window = null;
  #shell = null;
  #expanded = new Set();

  get snapshot() { return structuredClone(this.#snapshot); }

  connectedCallback() {
    if (!this.#shell) {
      const stylesheet = element("link");
      stylesheet.rel = "stylesheet";
      stylesheet.href = new URL("./style.css", import.meta.url).href;
      this.#shell = element("section", undefined, "dashboard-shell");
      this.#shell.setAttribute("aria-label", "Live monitoring");
      this.append(stylesheet, this.#shell);
    }
    this.#start();
  }

  disconnectedCallback() {
    this.#stop();
    this.#state = "disconnected";
    this.#dirty = true;
  }

  attributeChangedCallback(name, previous, current) {
    if (previous === current) return;
    this.#stop();
    this.#snapshot = this.#profile = this.#window = null;
    this.#lastIndex = this.#lastEvent = null;
    this.#error = this.#streamError = null;
    this.#log = [];
    this.#dirty = true;
    this.#state = "disconnected";
    if (this.isConnected) this.#start();
  }

  #stop() {
    this.#epoch++;
    this.#request?.abort();
    this.#subscription?.close();
    this.#subscription = null;
  }

  #start() {
    this.#stop();
    const epoch = this.#epoch;
    try {
      const prefix = apiPrefix(this.getAttribute("src") ?? "/api");
      this.#subscription = subscribe(prefix, {
        onEvent: (envelope, type) => {
          if (epoch !== this.#epoch || !this.isConnected) return;
          this.#revision++;
          this.#dirty = true;
          this.#lastEvent = envelope.at ?? null;
          this.#log.push(`${type} ${JSON.stringify(envelope)}`);
          if (this.#log.length > 100) this.#log.shift();
          if (type === "index" || type === "reset") {
            this.#lastIndex = envelope.at ?? null;
            if (type === "reset" || envelope.reset === true) this.dispatchEvent(new CustomEvent("snapshot-reset", { bubbles: true, composed: true }));
            void this.reload();
          }
          this.#render();
        },
        onState: (state, error) => {
          if (epoch !== this.#epoch || !this.isConnected) return;
          if (state === "error") this.#streamError = error;
          else {
            this.#state = state;
            if (state === "connected") this.#streamError = null;
            else this.#dirty = true;
          }
          this.#render();
        },
        onReconnect: () => {
          if (epoch !== this.#epoch || !this.isConnected) return;
          this.#dirty = true;
          void this.reload();
        },
      });
      void this.reload();
    } catch (error) {
      this.#error = String(error);
      this.#render();
    }
  }

  /** Replace the snapshot only after every authoritative read succeeds. */
  async reload() {
    if (!this.isConnected) return;
    this.#request?.abort();
    const request = new AbortController();
    this.#request = request;
    const epoch = this.#epoch;
    const revision = this.#revision;
    this.#dirty = true;
    this.#render();
    try {
      const prefix = apiPrefix(this.getAttribute("src") ?? "/api");
      const options = { signal: request.signal };
      const [graph, digest, stale, profile, ready, blocked, running] = (await Promise.all([
        readOperation(prefix, "graph", {}, options),
        readOperation(prefix, "digest", {}, options),
        readOperation(prefix, "stale", {}, options),
        readOperation(prefix, "profile", {}, options),
        readOperation(prefix, "ready", {}, options),
        readOperation(prefix, "blocked", {}, options),
        readOperation(prefix, "where", { status: "running" }, options),
      ])).map(response => response.value);
      if (!Array.isArray(graph.nodes) || !Array.isArray(graph.edges) || !Array.isArray(digest.items) || !Array.isArray(stale.items) || !Array.isArray(profile.node_types) || !Array.isArray(profile.edge_types)) throw new Error("Invalid graph, digest, stale or profile response.");
      if (!Array.isArray(ready.items) || !Array.isArray(blocked.items) || !Array.isArray(running)) throw new Error("Invalid native ready, blocked or running response.");
      if (digest.items.some(group => !Array.isArray(group.nodes) || typeof group.count !== "number")) throw new Error("Invalid native digest groups.");
      const groups = new Map(digest.items.map(group => [group.kind, group.nodes]));
      if (DIGEST_KINDS.some(kind => !groups.has(kind))) throw new Error("Missing native digest group.");
      const readyIds = new Set(ready.items.map(node => node.id));
      const blockedIds = new Set(blocked.items.map(node => node.id));
      const hypotheses = profile.node_types.some(type => type.name === "Hypothesis") ? graph.nodes.filter(node => node.type === "Hypothesis") : [];
      const unique = new Set();
      const progress = await Promise.all(hypotheses.map(async hypothesis => {
        const { value } = await readOperation(prefix, "subtree", { id: hypothesis.id }, options);
        if (!Array.isArray(value.nodes)) throw new Error("Invalid native subtree response.");
        const members = new Map(value.nodes.map(node => [node.id, node]));
        const counts = Object.fromEntries(CATEGORIES.map(category => [category, 0]));
        for (const node of members.values()) {
          unique.add(node.id);
          const category = ["retired", "done", "failed"].includes(node.status) ? node.status : readyIds.has(node.id) ? "ready" : blockedIds.has(node.id) ? "blocked" : ["running", "proposed"].includes(node.status) ? node.status : "other";
          counts[category]++;
        }
        return { id: hypothesis.id, members: [...members.keys()], counts };
      }));
      const end = Math.floor(Date.now() / 1000) * 1000;
      const start = end - WEEK;
      const failures = graph.nodes.filter(node => node.type === "Failure" && Date.parse(node.created) >= start && Date.parse(node.created) <= end);
      const claimNodes = profile.node_types.some(type => type.name === "Claim") ? (await readOperation(prefix, "where", { type: "Claim" }, options)).value : [];
      if (!Array.isArray(claimNodes)) throw new Error("Invalid native Claim query response.");
      const claims = await Promise.all(claimNodes.map(async node => {
        const { value: evidence } = await readOperation(prefix, "evidence", { claim: node.id }, options);
        if (!Array.isArray(evidence.items)) throw new Error("Invalid native evidence response.");
        return {
          node,
          evidence,
          supports: graph.edges.filter(edge => edge.type === "supports" && (edge.from === node.id || edge.to === node.id)),
          contradicts: graph.edges.filter(edge => edge.type === "contradicts" && (edge.from === node.id || edge.to === node.id)),
        };
      }));
      if (request.signal.aborted || this.#request !== request || epoch !== this.#epoch || !this.isConnected) return;
      this.#snapshot = { graph, digest, stale, ready, blocked, running, progress, progressTotal: unique.size, failures, claims };
      this.#profile = profile;
      this.#window = { start: new Date(start).toISOString(), end: new Date(end).toISOString() };
      this.#error = null;
      this.#dirty = revision !== this.#revision;
      this.#render();
      this.dispatchEvent(new CustomEvent("snapshot-updated", { bubbles: true, composed: true }));
    } catch (error) {
      if (request.signal.aborted || this.#request !== request || epoch !== this.#epoch || !this.isConnected) return;
      this.#error = String(error);
      this.#dirty = true;
      request.abort();
      this.#render();
    }
  }

  #open(node) {
    const button = element("button", `${node.id} · ${node.title}`);
    button.type = "button";
    button.addEventListener("click", () => this.dispatchEvent(new CustomEvent("node-select", { bubbles: true, composed: true, detail: { id: node.id, node: structuredClone(node) } })));
    return button;
  }

  #section(title, collapsible = true) {
    const section = element(collapsible ? "details" : "section");
    if (collapsible) section.open = this.#expanded.has(title);
    section.append(element(collapsible ? "summary" : "h3", title));
    this.#shell.append(section);
    return section;
  }

  #render() {
    if (!this.#shell) return;
    this.#expanded = new Set([...this.#shell.querySelectorAll(":scope > details[open]")].map(section => section.firstElementChild.textContent));
    this.#shell.replaceChildren(element("h2", "Live monitoring"));
    const age = this.#lastIndex ? Math.floor((Date.now() - Date.parse(this.#lastIndex)) / 1000) : null;
    const status = element("p", `${this.#state} · ${this.#dirty || this.#error || this.#streamError || this.#state !== "connected" ? "Snapshot may be stale" : "Authoritative readback"} · Last index: ${this.#lastIndex ?? "not received"}${age !== null && age >= 0 ? ` (updated ${age} s ago)` : ""} · Last server event: ${this.#lastEvent ?? "not received"}`, "connection");
    status.setAttribute("role", "status");
    this.#shell.append(status);
    for (const error of [this.#error, this.#streamError]) if (error) this.#shell.append(element("p", error, "error"));
    const refresh = element("button", "Refresh monitoring");
    refresh.type = "button";
    refresh.addEventListener("click", () => { void this.reload(); });
    this.#shell.append(refresh);
    if (!this.#snapshot) {
      this.#shell.append(element("p", "No successful snapshot yet.", "muted"));
      this.#renderLog();
      return;
    }
    const { graph, digest, stale, ready, running, progress, progressTotal, failures, claims } = this.#snapshot;
    const summary = this.#section("Native digest", false);
    for (const group of digest.items) {
      const counter = element("p", `${group.kind}: ${group.count}`);
      counter.dataset.digestKind = group.kind;
      summary.append(counter);
    }
    summary.append(element("p", `Stale claims: ${stale.items.length}`));
    const staleDetails = element("details");
    staleDetails.append(element("summary", "Native stale result"), element("pre", JSON.stringify(stale, null, 2)));
    summary.append(staleDetails);
    const progressSection = this.#section("Hypothesis progress");
    progressSection.append(element("p", `Distinct members across all rows: ${progressTotal}. Buckets in precedence order: ${CATEGORIES.join(" → ")}.`));
    if (!this.#profile.node_types.some(type => type.name === "Hypothesis")) progressSection.append(element("p", "Hypothesis not declared by this profile."));
    else if (!progress.length) progressSection.append(element("p", "No items."));
    for (const row of progress) {
      const article = element("article");
      article.dataset.progressId = row.id;
      article.append(this.#open(graph.nodes.find(node => node.id === row.id)), element("p", CATEGORIES.map(category => `${category}: ${row.counts[category]}`).join(" · ")), element("p", `Members: ${row.members.join(", ")}`));
      progressSection.append(article);
    }
    const activity = this.#section("Activity — native running records");
    if (!running.length) activity.append(element("p", "No items."));
    for (const node of running) {
      const article = element("article");
      article.dataset.runningId = node.id;
      const lease = node.lease;
      const expired = lease ? Date.parse(lease.expires_at) <= Date.now() : false;
      article.dataset.expired = String(expired);
      article.append(this.#open(node), element("p", lease ? `Lease actor: ${JSON.stringify(lease.actor)} · acquired: ${lease.acquired_at} · expires: ${lease.expires_at}${expired ? " · expired (browser wall clock only)" : ""}` : "No lease recorded."));
      activity.append(article);
    }
    activity.append(element("h4", "Native ready records — unclaimed or reclaimable"));
    if (!ready.items.length) activity.append(element("p", "No items."));
    for (const node of ready.items) activity.append(this.#open(node));
    const feed = this.#section("Failures — rolling 7 days");
    feed.dataset.failureFeed = "";
    feed.append(element("p", `Failure creation window (inclusive): ${this.#window.start} through ${this.#window.end}.`));
    if (!this.#profile.node_types.some(type => type.name === "Failure")) feed.append(element("p", "Failure not declared by this profile."));
    else if (!failures.length) feed.append(element("p", "No items."));
    for (const node of failures) {
      const article = element("article");
      article.append(this.#open(node), element("p", `Created: ${node.created}`), element("p", node.body));
      for (const [key, value] of Object.entries(node.fields ?? {})) {
        if (value === null || typeof value !== "object") article.append(element("p", `${key}: ${String(value)}`));
      }
      feed.append(article);
    }
    const claimSection = this.#section("Claims — native where membership");
    if (!this.#profile.node_types.some(type => type.name === "Claim")) claimSection.append(element("p", "Claim not declared by this profile."));
    else if (!claims.length) claimSection.append(element("p", "No items."));
    const table = element("table");
    const head = element("tr");
    for (const title of ["Claim", "Supports", "Contradicts", "Evidence ids (native transitive inputs)", "Stale"]) head.append(element("th", title));
    table.append(head);
    const staleIds = new Set(stale.items.map(node => node.id));
    for (const claim of claims) {
      const row = element("tr");
      const name = element("td");
      name.append(this.#open(claim.node));
      row.append(name);
      for (const kind of ["supports", "contradicts"]) {
        const declared = this.#profile.edge_types.some(type => type.name === kind);
        row.append(element("td", !declared ? "Not declared" : claim[kind].length ? claim[kind].map(edge => `${edge.id}: ${edge.from} → ${edge.to}${edge.retired ? " (retired)" : ""}`).join("; ") : "No items"));
      }
      row.append(element("td", claim.evidence.items.length ? claim.evidence.items.map(node => node.id).join(", ") : "No items"), element("td", String(staleIds.has(claim.node.id))));
      table.append(row);
    }
    claimSection.append(table);
    this.#renderLog();
  }

  #renderLog() {
    const log = this.#section("Event log — last 100 received envelopes");
    log.append(element("pre", this.#log.length ? this.#log.join("\n") : "No events received."));
  }
}

customElements.define("research-dashboard", ResearchDashboard);
