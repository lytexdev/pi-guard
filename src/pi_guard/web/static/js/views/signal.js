// Signal: what is missing, and linking the phone by QR code.
import { api, attempt, fill, h, icon, t } from "../api.js";

export async function signalPanel() {
  const panel = h("section", { class: "panel stack" });
  let timer = null;
  const draw = async () => {
    clearTimeout(timer);
    const s = await api("/api/signal");
    const state = !s.installed && !s.running ? "missing" : !s.running ? "stopped" : !s.linked ? "unlinked" : !s.connected ? "restart" : "ready";
    const link = h("button", { class: "primary", title: t("web.signal_link_tip"), onclick: async () => {
      const result = await attempt(() => api("/api/signal/link", { method: "POST", body: {} }));
      if (!result) return;
      fill(panel, h("h2", {}, t("web.signal_status")), h("p", {}, t("web.signal_scan")), h("img", { class: "qr light", src: result.qr, alt: "QR" }));
      timer = setTimeout(draw, 4000);
    } }, icon("qrcode"), t("web.signal_link"));
    fill(panel, h("h2", {}, t("web.signal_status")),
      h("p", { class: state === "ready" ? "" : "notice" }, t(`web.signal_${state}`)),
      s.version ? h("small", { class: "muted" }, `signal-cli ${s.version}`) : null,
      state === "missing" ? h("pre", { class: "mono wrap" }, "sudo ./scripts/signal.sh install") : null,
      state === "stopped" ? h("pre", { class: "mono wrap" }, "sudo systemctl restart pi-guard-signal") : null,
      state === "unlinked" ? link : null);
    if (s.link?.state === "waiting") timer = setTimeout(draw, 4000);
  };
  await draw();
  return panel;
}
