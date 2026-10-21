export function apiPrefix(value) {
  const message = "src must be a same-origin API prefix without credentials, query or fragment.";
  if (!value || typeof value !== "string" || value.includes("?") || value.includes("#")) throw new Error(message);
  const prefix = new URL(value, document.baseURI);
  if (prefix.origin !== location.origin || prefix.username || prefix.password || prefix.search || prefix.hash) throw new Error(message);
  return prefix.href.replace(/\/+$/, "");
}

export class APIError extends Error {
  constructor(status, rule, message, ids) {
    super(message);
    this.name = "APIError";
    this.status = status;
    this.rule = rule;
    this.ids = ids;
  }

  toString() {
    return `${this.name} (${this.status})${this.rule ? ` ${this.rule}` : ""}: ${this.message}${this.ids?.length ? ` · ${JSON.stringify(this.ids)}` : ""}`;
  }
}

async function operationRequest(prefix, operation, args, options, method) {
  const url = new URL(`${apiPrefix(prefix)}/${encodeURIComponent(operation)}`);
  const values = Object.fromEntries(Object.entries(args).filter(([key, value]) => value !== undefined && (key !== "role" || value !== "")));
  if (options.role !== undefined && options.role !== "") values.role = options.role;
  const request = {
    method, credentials: "same-origin", cache: "no-store", redirect: "error",
    signal: options.signal, headers: { Accept: "application/json" },
  };
  if (method === "GET") {
    for (const [key, value] of Object.entries(values)) url.searchParams.set(key, JSON.stringify(value));
  } else {
    request.headers["Content-Type"] = "application/json";
    request.body = JSON.stringify(values);
  }
  const response = await fetch(url, request);
  const text = await response.text();
  let value;
  try { value = JSON.parse(text); }
  catch {
    if (response.ok) throw new APIError(response.status, null, `Invalid JSON response: ${text || response.statusText}`, []);
  }
  if (!response.ok) {
    throw new APIError(response.status, value?.rule ?? null, value?.message ?? (text || response.statusText), value?.ids ?? []);
  }
  const header = response.headers.get("X-Researchgraph-Read-Only");
  return {
    value,
    readOnly: header === "true" ? true : header === "false" ? false : null,
    cursor: response.headers.get("X-Researchgraph-Cursor"),
  };
}

export function readOperation(prefix, operation, args = {}, options = {}) {
  return operationRequest(prefix, operation, args, options, "GET");
}

export function writeOperation(prefix, operation, args = {}, options = {}) {
  return operationRequest(prefix, operation, args, options, "POST");
}
