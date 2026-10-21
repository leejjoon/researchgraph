import { apiPrefix } from "./api.js";

/** One browser-owned stream; EventSource retains the opaque reconnect cursor. */
export function subscribe(prefix, { onEvent, onState, onReconnect }) {
  const source = new EventSource(`${apiPrefix(prefix)}/events`);
  const lifetime = new AbortController();
  const seen = new Set();
  let closed = false;
  let cursor = null;
  const state = () => {
    if (closed) return;
    onState(navigator.onLine === false ? "offline" : source.readyState === EventSource.OPEN ? "connected" : source.readyState === EventSource.CLOSED ? "disconnected" : "reconnecting");
  };
  const receive = event => {
    if (closed) return;
    let envelope;
    try {
      envelope = JSON.parse(event.data);
      if (!envelope || typeof envelope !== "object" || Array.isArray(envelope)) throw new Error("Invalid event envelope.");
    } catch (error) {
      onState("error", String(error));
      return;
    }
    const next = event.lastEventId || envelope.cursor;
    if (typeof next === "string" && next) {
      cursor = next;
      if (seen.has(next)) return;
      seen.add(next);
      if (seen.size > 256) seen.delete(seen.values().next().value);
    }
    onEvent(envelope, event.type);
  };
  for (const kind of ["node", "edge", "index", "reset"]) source.addEventListener(kind, receive, { signal: lifetime.signal });
  source.addEventListener("open", () => {
    if (closed) return;
    state();
    onReconnect();
  }, { signal: lifetime.signal });
  source.addEventListener("error", state, { signal: lifetime.signal });
  window.addEventListener("offline", state, { signal: lifetime.signal });
  window.addEventListener("online", () => {
    if (closed) return;
    state();
    onReconnect();
  }, { signal: lifetime.signal });
  state();
  return {
    get cursor() { return cursor; },
    close() {
      if (closed) return;
      closed = true;
      lifetime.abort();
      source.close();
      seen.clear();
    },
  };
}
