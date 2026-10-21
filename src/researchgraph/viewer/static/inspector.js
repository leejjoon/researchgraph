import { apiPrefix, readOperation, writeOperation } from "./api.js";
import { element } from "./dom.js";

const RELATED = [
  ["history", "id"], ["evidence", "claim"], ["weakest", "claim"],
  ["alternatives", "hypothesis"], ["predictions", "hypothesis"],
  ["notes", "id"], ["subtree", "id"],
];
const ACTIONS = [
  ["Approve", "approve"], ["Reject", "reject"], ["Instruct", "instruct"],
  ["Re-run with instruction", "supersede"], ["Retire", "retire"],
  ["Budget", "budget"], ["Tag", "tag"], ["Pin link", "link"], ["Expand", "expand"],
];
const labelFor = name => {
  const text = name.replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
};
const json = value => JSON.stringify(value, null, 2);
const empty = value => value == null || (Array.isArray(value) && !value.length) ||
  (typeof value === "object" && !Object.keys(value).length) ||
  (Array.isArray(value?.items) && !value.items.length);

export class ResearchInspector extends HTMLElement {
  static observedAttributes = ["src", "node-id"];
  #node = null;
  #related = {};
  #profile = null;
  #operations = [];
  #graph = null;
  #readOnly = null;
  #busy = false;
  #lastAction = null;
  #role = "";
  #read = null;
  #readPromise = null;
  #mutation = null;
  #epoch = 0;
  #error = null;
  #loading = false;
  #action = null;
  #shell;

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    const stylesheet = element("link");
    stylesheet.rel = "stylesheet";
    stylesheet.href = new URL("./style.css", import.meta.url).href;
    this.#shell = element("section", undefined, "inspector-shell");
    this.#shell.setAttribute("aria-label", "Node inspector");
    this.shadowRoot.append(stylesheet, this.#shell);
    this.#render();
  }

  get node() { return structuredClone(this.#node); }
  get related() { return structuredClone(this.#related); }
  get readOnly() { return this.#readOnly; }
  get busy() { return this.#busy; }
  get lastAction() { return structuredClone(this.#lastAction); }

  connectedCallback() { void this.reload(); }

  disconnectedCallback() {
    this.#epoch++;
    this.#read?.abort();
    this.#mutation?.abort();
  }

  attributeChangedCallback(name, previous, current) {
    if (previous === current) return;
    this.#read?.abort();
    this.#node = null;
    this.#related = {};
    this.#action = null;
    this.#error = null;
    this.#readOnly = null;
    if (name === "src") {
      this.#epoch++;
      this.#mutation?.abort();
      this.#profile = this.#graph = null;
      this.#operations = [];
      this.#role = "";
      this.#lastAction = null;
    }
    if (this.isConnected) void this.reload();
  }

  select(id) {
    if (this.getAttribute("node-id") === id) return this.reload();
    if (id == null) this.removeAttribute("node-id");
    else this.setAttribute("node-id", id);
    return this.#readPromise;
  }

  reload() {
    this.#readPromise = this.#load();
    return this.#readPromise;
  }

  async #load() {
    this.#read?.abort();
    const request = new AbortController();
    this.#read = request;
    const id = this.getAttribute("node-id");
    this.#error = null;
    this.#loading = Boolean(id);
    this.#render();
    if (!this.isConnected || !id) return;
    try {
      const base = apiPrefix(this.getAttribute("src"));
      const read = (name, args = {}) => readOperation(base, name, args, { signal: request.signal, role: this.#role });
      const responses = await Promise.all([
        read("show", { id }), read("graph"), read("profile"), read("operations"),
        ...RELATED.map(([name, key]) => read(name, { [key]: id })),
      ]);
      if (request.signal.aborted || this.#read !== request || !this.isConnected) return;
      const [shown, graph, profile, operations, ...related] = responses;
      if (!Array.isArray(graph.value.nodes) || !Array.isArray(graph.value.edges) ||
          !Array.isArray(profile.value.roles) || !Array.isArray(profile.value.edge_types) ||
          !Array.isArray(operations.value)) {
        throw new Error("Invalid graph, profile or operation catalogue response.");
      }
      this.#graph = graph.value;
      this.#profile = profile.value;
      this.#operations = operations.value;
      this.#related = Object.fromEntries(RELATED.map(([name], index) => [name, related[index].value]));
      this.#readOnly = responses.some(response => response.readOnly === true) ? true :
        responses.every(response => response.readOnly === false) ? false : null;
      if (!this.#profile.roles.includes(this.#role)) this.#role = "";
      this.#node = shown.value;
    } catch (error) {
      if (request.signal.aborted || this.#read !== request) return;
      request.abort();
      this.#readOnly = null;
      this.#error = String(error);
    } finally {
      if (this.#read === request) {
        this.#loading = false;
        this.#render();
      }
    }
  }

  #fields(container, fields) {
    const list = element("dl");
    for (const [key, value] of Object.entries(fields || {})) {
      const description = element("dd");
      if (value !== null && typeof value === "object") description.append(element("pre", json(value)));
      else description.textContent = value === null ? "null" : String(value);
      list.append(element("dt", labelFor(key)), description);
    }
    container.append(list);
  }

  #section(name, value, render) {
    const section = element("section");
    section.dataset.section = name;
    section.append(element("h3", labelFor(name)));
    if (empty(value)) section.append(element("p", "No items", "muted"));
    if (render) render(section, value);
    else if (!empty(value)) section.append(element("pre", json(value)));
    this.#shell.append(section);
    return section;
  }

  #render() {
    this.#shell.replaceChildren(element("h2", "Node inspector"));
    const status = element("p", this.#error || (this.#loading ? "Loading node…" : this.#node ? `${this.#node.id} · ${this.#node.status || this.#node.type}` : "Select a node."), this.#error ? "error" : "muted");
    status.setAttribute("role", "status");
    this.#shell.append(status);
    if (this.#lastAction) {
      const action = this.#lastAction;
      this.#shell.append(element("pre", action.error || json(action.result), action.error ? "error" : "action-result"));
    }
    if (!this.#node) return;
    this.#shell.append(element("h3", this.#node.title || this.#node.id));
    if (this.#node.body) this.#shell.append(element("pre", this.#node.body));
    this.#renderActions();
    this.#section("fields", this.#node.fields, (section, fields) => this.#fields(section, fields));
    this.#section("approvals", this.#node.approvals);
    const artifacts = this.#section("artifacts", this.#node.artifacts);
    for (const artifact of this.#node.artifacts || []) {
      const parts = artifact.path?.split("/");
      if (!parts || parts.length < 3 || parts[0] !== "artifacts" || parts[1] !== this.#node.id || parts.some(part => !part || part === "." || part === "..")) continue;
      const anchor = element("a", artifact.path);
      anchor.href = `/artifacts/${parts.slice(1).map(encodeURIComponent).join("/")}`;
      anchor.referrerPolicy = "no-referrer";
      anchor.download = parts.at(-1);
      artifacts.append(anchor);
    }
    const byId = new Map(this.#graph.nodes.map(node => [node.id, node]));
    this.#section("incidents", this.#node.edges || [], (section, edges) => {
      for (const edge of edges) {
        const incident = element("article");
        const edgeRecord = element("details");
        edgeRecord.append(element("summary", "Full edge record"), element("pre", json(edge)));
        incident.append(element("h4", `${edge.type}: ${edge.from} → ${edge.to}`), edgeRecord);
        for (const [direction, id] of [["From", edge.from], ["To", edge.to]]) {
          if (id === this.#node.id) continue;
          const node = byId.get(id);
          if (!node) continue;
          incident.append(element("h5", `${direction}: ${node.title || node.id}`));
          incident.append(element("p", `${node.id} · ${node.type} · ${node.status}`));
          if (empty(node.fields)) incident.append(element("p", "No items", "muted"));
          this.#fields(incident, node.fields);
          const record = element("details");
          record.append(element("summary", "Full node record"), element("pre", json(node)));
          incident.append(record);
        }
        section.append(incident);
      }
    });
    for (const name of ["instructions", "pins", "flags", "warnings"]) this.#section(name, this.#node[name]);
    for (const [name] of RELATED) {
      this.#section(name, this.#related[name], (section, value) => {
        if (empty(value)) return;
        const record = element("details");
        record.append(element("summary", `Full ${name} result`), element("pre", json(value)));
        section.append(record);
      });
    }
  }

  #renderActions() {
    const roleLabel = element("label");
    const role = element("select");
    role.setAttribute("aria-label", "Role");
    const launch = element("option", "Launch default");
    launch.value = "";
    role.append(launch);
    for (const name of this.#profile.roles) {
      const option = element("option", name);
      option.value = name;
      role.append(option);
    }
    role.value = this.#role;
    role.disabled = this.#busy;
    role.addEventListener("change", () => { this.#role = role.value; });
    roleLabel.append(element("span", "Role"), role);
    this.#shell.append(roleLabel);
    if (this.#readOnly !== false) {
      this.#shell.append(element("p", this.#readOnly === true ? "Read-only session." : "Write availability unknown; actions hidden.", "muted"));
      return;
    }
    const actions = element("div", undefined, "inspector-actions");
    for (const [label, operation] of ACTIONS) {
      const registered = this.#operations.find(item => item.name === operation && item.kind === "write");
      if (!registered || (operation === "link" && !this.#profile.edge_types.some(type => type.name === "pins"))) continue;
      const button = element("button", label);
      button.type = "button";
      button.dataset.action = operation;
      button.disabled = this.#busy || this.#loading;
      button.addEventListener("click", () => {
        this.#action = operation;
        this.#render();
      });
      actions.append(button);
    }
    this.#shell.append(actions);
    if (this.#action) this.#form();
  }

  #presets(operation, id) {
    return operation === "expand" ? { nodes: [id] } :
      operation === "link" ? { type: "pins", from: id } : { node: id };
  }

  #form() {
    const operation = this.#action;
    const registered = this.#operations.find(item => item.name === operation);
    if (!registered) return;
    const label = ACTIONS.find(([, name]) => name === operation)[0];
    const form = element("form");
    form.dataset.action = operation;
    form.append(element("h3", label));
    const explanation = operation === "supersede" ? "Creates a graph replacement and, when supplied, a separate Instruction. This does not execute work." :
      operation === "instruct" ? "Creates an Instruction node linked to this node. This does not execute work." :
      operation === "expand" ? "Creates an expansion Task for the selected node. This does not execute work." : null;
    if (explanation) form.append(element("p", explanation, "muted"));
    const presets = this.#presets(operation, this.#node.id);
    const controls = [];
    for (const argument of registered.arguments) {
      if (Object.hasOwn(presets, argument.name)) continue;
      const name = labelFor(argument.name);
      const types = Array.isArray(argument.schema.type) ? argument.schema.type : [argument.schema.type];
      const boolean = types.includes("boolean");
      const input = element(boolean ? "input" : "textarea");
      if (boolean) {
        input.type = "checkbox";
        if (Object.hasOwn(argument, "default")) input.checked = argument.default === true;
      }
      input.setAttribute("aria-label", name);
      input.disabled = this.#busy;
      const wrapper = element("label");
      wrapper.append(element("span", name), input);
      form.append(wrapper);
      let include = null;
      if (!argument.required) {
        const hint = Object.hasOwn(argument, "default") ?
          `Omitted unless Set ${name} is checked. Server default: ${JSON.stringify(argument.default)}.` :
          `Omitted unless Set ${name} is checked.`;
        form.append(element("p", hint, "muted"));
        include = element("input");
        include.type = "checkbox";
        include.setAttribute("aria-label", `Set ${name}`);
        include.disabled = this.#busy;
        const toggle = element("label");
        toggle.append(include, element("span", `Set ${name}`));
        form.append(toggle);
        const enable = () => { include.checked = true; };
        input.addEventListener("input", enable);
        input.addEventListener("change", enable);
      }
      controls.push({ argument, input, include, types });
    }
    const submit = element("button", `Apply ${label}`);
    submit.type = "submit";
    submit.disabled = this.#busy || this.#loading;
    form.append(submit);
    form.addEventListener("submit", event => {
      event.preventDefault();
      if (this.#busy || this.#readOnly !== false || this.#loading) return;
      try {
        const args = { ...presets };
        for (const { argument, input, include, types } of controls) {
          if (include && !include.checked) continue;
          args[argument.name] = types.includes("boolean") ? input.checked :
            types.includes("string") ? input.value : JSON.parse(input.value);
        }
        void this.#mutate(operation, args);
      } catch (error) {
        this.#error = `Invalid form JSON: ${error.message}`;
        // Keep entered values available for correction.
        let message = form.querySelector('[role="alert"]');
        if (!message) {
          message = element("p", undefined, "error");
          message.setAttribute("role", "alert");
          form.append(message);
        }
        message.textContent = this.#error;
      }
    });
    this.#shell.append(form);
  }

  async #mutate(operation, args) {
    if (this.#busy) return;
    const epoch = this.#epoch;
    const node = this.#node.id;
    const role = this.#role;
    const request = new AbortController();
    this.#mutation = request;
    this.#busy = true;
    this.#lastAction = null;
    this.#error = null;
    this.#render();
    let result = null;
    let error = null;
    try {
      const response = await writeOperation(apiPrefix(this.getAttribute("src")), operation, args, { signal: request.signal, role });
      result = response.value;
    } catch (failure) {
      error = String(failure);
    } finally {
      // Selection only cancels reads, never a write. An error does not imply rollback:
      // every settled write is followed by authoritative persisted-state readback.
      if (epoch === this.#epoch && this.isConnected) {
        await this.reload();
        let pending = this.#readPromise;
        while (pending) {
          await pending;
          if (pending === this.#readPromise || epoch !== this.#epoch || !this.isConnected) break;
          pending = this.#readPromise;
        }
      }
      if (this.#mutation === request) {
        this.#mutation = null;
        this.#busy = false;
      }
      if (epoch === this.#epoch) {
        this.#lastAction = { operation, node, result, error };
        this.#action = null;
      }
      this.#render();
      if (epoch === this.#epoch && this.isConnected) {
        this.dispatchEvent(new CustomEvent("mutation-complete", {
          bubbles: true, composed: true, detail: this.lastAction,
        }));
      }
    }
  }
}

customElements.define("research-inspector", ResearchInspector);
