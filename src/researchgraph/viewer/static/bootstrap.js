import { element } from "./dom.js";

const status = document.querySelector("#connection");
let token = new URLSearchParams(location.hash.slice(1)).get("token");
history.replaceState(null, "", location.pathname + location.search);

try {
  if (token) {
    const response = await fetch("/api/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
      credentials: "same-origin",
      cache: "no-store",
    });
    if (!response.ok) throw new Error("Authentication failed. Use a fresh launch URL.");
  }
  token = null;
  const response = await fetch("/api/profile", {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!response.ok) throw new Error(response.status === 403
    ? "Authentication required. Use the server's launch URL."
    : "Project unavailable.");
  await response.json();
  await import("./research-graph.js");
  await import("./inspector.js");
  await import("./review-queue.js");
  const graph = document.createElement("research-graph");
  graph.setAttribute("src", "/api");
  const inspector = document.createElement("research-inspector");
  inspector.setAttribute("src", "/api");
  const reviews = document.createElement("research-review-queue");
  reviews.setAttribute("src", "/api");
  const navigation = element("nav");
  navigation.setAttribute("aria-label", "Viewer navigation");
  const openReviews = element("button", "Review queue ");
  openReviews.type = "button";
  const reviewCount = element("span", "—");
  reviewCount.dataset.reviewNavBadge = "";
  openReviews.append(reviewCount);
  openReviews.addEventListener("click", () => reviews.scrollIntoView({ block: "start" }));
  navigation.append(openReviews);
  document.querySelector("#viewer").before(navigation);
  reviews.addEventListener("queue-updated", event => { reviewCount.textContent = String(event.detail.count); });
  graph.addEventListener("node-select", event => inspector.select(event.detail.id));
  inspector.addEventListener("mutation-complete", () => { void graph.reload(); void reviews.reload(); });
  reviews.addEventListener("node-select", async event => {
    await inspector.select(event.detail.id);
    if (event.detail.section) inspector.openSection(event.detail.section);
  });
  reviews.addEventListener("mutation-complete", () => { void graph.reload(); void inspector.reload(); });
  document.querySelector("#viewer").append(graph, inspector, reviews);
  status.textContent = "Authenticated.";
} catch (error) {
  status.textContent = error.message;
} finally {
  token = null;
}
