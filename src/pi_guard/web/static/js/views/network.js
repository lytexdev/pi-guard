// Network: Pi-hole, WireGuard and devices in the LAN (filled in by the network API).
import { api, h, t, fill } from "../api.js";

export async function render(main, params = []) {
  fill(main, h("div", { class: "topbar" }, h("h1", {}, t("addons.analytics"))));
  let data = null;
  try { data = await api("/api/network"); } catch { /* network API not available */ }
  if (!data) { main.append(h("section", { class: "panel" }, h("p", { class: "muted" }, t("web.network_disabled")))); return; }
  const { render: draw } = await import("./network-detail.js");
  await draw(main, data, params);
}
