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
  const profile = await response.json();
  status.textContent = `Connected to ${profile.name} ${profile.version}.`;
} catch (error) {
  status.textContent = error.message;
} finally {
  token = null;
}
