// Connections of the messengers: what is missing, linking Signal, trying out Discord.
import { api, attempt, fill, h, icon, t } from "../api.js";

export async function discordPanel() {
  const s = await api("/api/discord");
  return h("section", { class: "panel stack" }, h("h2", {}, t("web.discord_status")),
    h("p", { class: s.connected ? "" : "notice" }, !s.enabled ? t("web.discord_setup") : s.problem ? t("web.discord_refused") : s.connected ? t("web.discord_connected", { name: s.name }) : t("web.discord_waiting")),
    s.invite ? h("a", { class: "button", href: s.invite, target: "_blank", rel: "noopener", title: t("web.discord_invite_tip") }, icon("plus"), t("web.discord_invite")) : null,
    s.connected ? h("button", { title: t("web.discord_test_tip"), onclick: () => attempt(() => api("/api/discord/test", { method: "POST" }), t("web.discord_test_sent")) }, icon("bell"), t("web.discord_test")) : null);
}

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
      state === "stopped" ? h("pre", { class: "mono wrap" }, "cd ~/pi-guard && sudo ./scripts/signal.sh install") : null,
      state === "unlinked" ? link : null);
    if (s.link?.state === "waiting") timer = setTimeout(draw, 4000);
  };
  await draw();
  return panel;
}
