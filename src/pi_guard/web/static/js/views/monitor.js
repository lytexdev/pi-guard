// Camera room: every camera at once, like a security desk.
import { api, attempt, fill, h, icon, t, toast } from "../api.js";
import { state } from "../app.js";
import { assistantIcon, liveSwitch } from "../widgets.js";

const KEY = "monitor.columns";

export async function render(main) {
  const overview = await api("/api/overview");
  const grid = h("div", { class: "monitor" });
  const clock = h("span", { class: "mono" });
  const columns = (n) => { grid.dataset.columns = n; localStorage.setItem(KEY, n); };
  columns(localStorage.getItem(KEY) || (overview.cameras.length > 4 ? 3 : 2));
  const describe = async (camera, out) => {
    out.textContent = t("web.mon_looking");
    const first = await attempt(() => api("/api/assistant/chat", { method: "POST", body: { text: `Schau auf die Kamera ${camera.name}` } }));
    if (!first?.pending) { out.textContent = first?.reply || ""; return; }
    const done = await attempt(() => api("/api/assistant/confirm", { method: "POST", body: { token: first.pending, yes: true } }));
    out.textContent = done?.reply || "";
  };
  overview.cameras.forEach((camera) => {
    const img = h("img", { src: `/api/live/${camera.id}/video`, alt: camera.name, loading: "lazy" });
    const note = h("div", { class: "mon-note", "aria-live": "polite" });
    const tile = h("figure", { class: "mon-tile" }, img,
      h("figcaption", {}, h("span", { class: `dot ${camera.state === "running" ? "ok" : "danger"}` }), h("span", { class: "grow" }, camera.name, camera.room ? ` · ${camera.room}` : ""),
        state.session.assistant ? h("button", { class: "icon-only", title: t("web.mon_ai"), onclick: () => describe(camera, note) }, assistantIcon()) : null,
        h("button", { class: "icon-only", title: t("web.fullscreen"), onclick: () => tile.requestFullscreen?.() }, icon("maximize")),
        h("a", { class: "button icon-only", href: `#/live/${camera.id}`, title: t("web.nav_live") }, icon("video"))), note);
    img.addEventListener("error", () => tile.classList.add("offline"));
    grid.append(tile);
  });
  fill(main, h("div", { class: "topbar" }, h("h1", {}, t("web.nav_monitor")), liveSwitch("monitor"),
    h("div", { class: "row" }, clock, h("div", { class: "segmented" }, [1, 2, 3, 4].map((n) => h("button", { onclick: () => columns(n) }, n))))),
    overview.cameras.length ? grid : h("p", { class: "empty" }, t("web.no_cameras")));
  const tick = () => { clock.textContent = new Date().toLocaleTimeString(); };
  tick();
  const timer = setInterval(tick, 1000);
  return () => { clearInterval(timer); grid.querySelectorAll("img").forEach((i) => i.removeAttribute("src")); };
}
