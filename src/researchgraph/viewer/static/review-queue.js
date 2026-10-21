import { apiPrefix, readOperation, writeOperation } from "./api.js";
import { element } from "./dom.js";

export class ResearchReviewQueue extends HTMLElement {
  static observedAttributes = ["src"];
  #items = null;
  #profile = null;
  #blocked = [];
  #readOnly = null;
  #role = "";
  #busy = false;
  #lastAction = null;
  #request = null;
  #mutation = null;
  #epoch = 0;
  #error = null;
  #shell;

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    const stylesheet = element("link");
    stylesheet.rel = "stylesheet";
    stylesheet.href = new URL("./style.css", import.meta.url).href;
    this.#shell = element("section", undefined, "review-shell");
    this.#shell.setAttribute("aria-label", "Review queue");
    this.shadowRoot.append(stylesheet, this.#shell);
    this.#render();
  }

  get items() { return structuredClone(this.#items); }
  get busy() { return this.#busy; }
  get lastAction() { return structuredClone(this.#lastAction); }
  get readOnly() { return this.#readOnly; }

  connectedCallback() { void this.reload(); }
  disconnectedCallback() {
    this.#epoch++;
    this.#request?.abort();
    this.#mutation?.abort();
  }
  attributeChangedCallback(name, previous, current) {
    if (previous === current) return;
    this.#epoch++;
    this.#request?.abort();
    this.#mutation?.abort();
    this.#items = this.#profile = null;
    this.#readOnly = null;
    this.#lastAction = null;
    if (this.isConnected) void this.reload();
  }

  async reload() {
    this.#request?.abort();
    const request = new AbortController();
    this.#request = request;
    this.#error = null;
    try {
      const prefix = apiPrefix(this.getAttribute("src"));
      const options = { signal: request.signal };
      const [queue, blocked, profile] = await Promise.all([
        readOperation(prefix, "queue", {}, options),
        readOperation(prefix, "blocked", {}, options),
        readOperation(prefix, "profile", {}, options),
      ]);
      if (request.signal.aborted || this.#request !== request || !this.isConnected) return;
      if (!Array.isArray(queue.value.items) || !Array.isArray(blocked.value.items) || !Array.isArray(profile.value.roles) || !Array.isArray(profile.value.gates)) throw new Error("Invalid queue, blocked or profile response.");
      this.#items = queue.value.items;
      this.#blocked = blocked.value.items;
      this.#profile = profile.value;
      this.#readOnly = [queue, blocked, profile].some(value => value.readOnly === true) ? true : [queue, blocked, profile].every(value => value.readOnly === false) ? false : null;
      if (!this.#profile.roles.includes(this.#role)) this.#role = "";
      this.dispatchEvent(new CustomEvent("queue-updated", { bubbles: true, composed: true, detail: { count: this.#items.length } }));
    } catch (error) {
      if (request.signal.aborted || this.#request !== request) return;
      this.#error = `${error} Displayed reviews may be stale.`;
      this.#readOnly = null;
      request.abort();
    } finally {
      if (this.#request === request) this.#render();
    }
  }

  #navigate(node, section) {
    this.dispatchEvent(new CustomEvent("node-select", { bubbles: true, composed: true, detail: { id: node.id, node: structuredClone(node), section } }));
  }

  #render() {
    const drafts = new Map([...this.#shell.querySelectorAll("form[data-review-action]")].map(form => [`${form.closest("[data-review-id]").dataset.reviewId}:${form.dataset.reviewAction}`, form.querySelector("textarea").value]));
    const focused = this.shadowRoot.activeElement;
    const focusedForm = focused?.matches("textarea") ? focused.closest("form") : null;
    const focusKey = focusedForm ? `${focusedForm.closest("[data-review-id]").dataset.reviewId}:${focusedForm.dataset.reviewAction}` : null;
    const selection = focusKey ? [focused.selectionStart, focused.selectionEnd] : null;
    let restoreFocus = null;
    const heading = element("h2", "Review queue ");
    const badge = element("span", this.#items === null ? "—" : String(this.#items.length));
    badge.dataset.reviewBadge = "";
    heading.append(badge);
    this.#shell.replaceChildren(heading);
    const status = element("p", this.#error || (this.#items === null ? "Loading reviews…" : "Authoritative queue snapshot."), this.#error ? "error" : "muted");
    status.setAttribute("role", "status");
    this.#shell.append(status);
    if (this.#lastAction) this.#shell.append(element("p", this.#lastAction.error || `${this.#lastAction.operation} returned successfully; queue refetched.`, this.#lastAction.error ? "error" : "muted"));
    const refresh = element("button", "Refresh reviews");
    refresh.type = "button";
    refresh.disabled = this.#busy;
    refresh.addEventListener("click", () => { void this.reload(); });
    this.#shell.append(refresh);
    if (!this.#profile || this.#items === null) return;
    this.#shell.append(element("p", "Each actor can approve a node once. Changing roles does not create another reviewer.", "muted"));
    const role = element("select");
    role.setAttribute("aria-label", "Review role");
    for (const name of ["", ...this.#profile.roles]) {
      const option = element("option", name || "Launch default");
      option.value = name;
      role.append(option);
    }
    role.value = this.#role;
    role.disabled = this.#busy;
    role.addEventListener("change", () => { this.#role = role.value; });
    const roleLabel = element("label");
    roleLabel.append(element("span", "Review role"), role);
    this.#shell.append(roleLabel);
    if (!this.#items.length) this.#shell.append(element("p", "No reviews awaiting activation.", "muted"));
    for (const node of this.#items) {
      const card = element("article", undefined, "review-card");
      card.dataset.reviewId = node.id;
      card.append(element("h3", node.title), element("p", `${node.id} · ${node.type}`), element("p", node.body));
      const elapsed = Math.floor((Date.now() - Date.parse(node.created)) / 1000);
      card.append(element("p", elapsed >= 0 ? `Waiting ${elapsed} seconds · created ${node.created}` : `Created in the future: ${node.created}; waiting time unavailable.`));
      card.append(element("p", `${this.#blocked.filter(item => item.blocking.includes(node.id)).length} blocked behind`));
      card.append(element("h4", "Applicable gate metadata (server)"), element("p", `Roles: ${node.gate.roles.join(", ")} · agents allowed: ${node.gate.agents_allowed}`));
      const declarations = this.#profile.gates.filter(gate => gate.node_type === node.type || gate.node_type === "*");
      const conditions = element("details");
      conditions.open = true;
      conditions.append(element("summary", "Declared gate conditions"));
      for (const gate of declarations) {
        conditions.append(element("p", `${gate.node_type} · roles: ${gate.roles.join(", ")} · agents allowed: ${gate.agents_allowed}`));
        conditions.append(element("pre", gate.when_fields === null ? "Unconditional" : JSON.stringify(gate.when_fields, null, 1)));
      }
      card.append(conditions, element("h4", "Approvals so far"));
      card.append(node.approvals.length ? element("pre", JSON.stringify(node.approvals, null, 2)) : element("p", "No approvals yet.", "muted"));
      const open = element("button", "Open node");
      open.type = "button";
      open.addEventListener("click", () => this.#navigate(node));
      card.append(open);
      if (node.type === "Claim") {
        const evidence = element("button", "Open evidence");
        evidence.type = "button";
        evidence.addEventListener("click", () => this.#navigate(node, "evidence"));
        card.append(evidence);
      }
      if (this.#readOnly === false) {
        for (const [operation, label, argument] of [["approve", "Approve", "note"], ["reject", "Reject", "reason"]]) {
          const form = element("form");
          form.dataset.reviewAction = operation;
          const field = element("textarea");
          const key = `${node.id}:${operation}`;
          field.value = drafts.get(key) ?? "";
          if (key === focusKey) restoreFocus = field;
          field.setAttribute("aria-label", operation === "approve" ? "Approval note" : "Reject reason");
          field.disabled = this.#busy;
          const fieldLabel = element("label");
          fieldLabel.append(element("span", field.getAttribute("aria-label")), field);
          const button = element("button", label);
          button.type = "submit";
          button.disabled = this.#busy;
          form.append(fieldLabel, button);
          form.addEventListener("submit", event => {
            event.preventDefault();
            const args = { node: node.id };
            if (operation === "reject" || field.value !== "") args[argument] = field.value;
            void this.#act(operation, args);
          });
          card.append(form);
        }
      }
      this.#shell.append(card);
    }
    if (restoreFocus && !restoreFocus.disabled) {
      restoreFocus.focus({ preventScroll: true });
      restoreFocus.setSelectionRange(...selection);
    }
  }

  async #act(operation, args) {
    if (this.#busy || this.#readOnly !== false) return;
    const epoch = this.#epoch;
    const request = new AbortController();
    this.#mutation = request;
    this.#busy = true;
    this.#lastAction = null;
    this.#render();
    let result = null, error = null;
    try {
      const response = await writeOperation(apiPrefix(this.getAttribute("src")), operation, args, { signal: request.signal, role: this.#role });
      result = response.value;
    } catch (failure) { error = String(failure); }
    finally {
      if (epoch === this.#epoch && this.isConnected) await this.reload();
      if (this.#mutation === request) {
        this.#mutation = null;
        this.#busy = false;
      }
      if (epoch === this.#epoch) this.#lastAction = { operation, node: args.node, result, error };
      this.#render();
      if (epoch === this.#epoch && this.isConnected) this.dispatchEvent(new CustomEvent("mutation-complete", { bubbles: true, composed: true, detail: this.lastAction }));
    }
  }
}

customElements.define("research-review-queue", ResearchReviewQueue);
