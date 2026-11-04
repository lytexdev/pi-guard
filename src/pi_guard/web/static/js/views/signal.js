// Connections of the messengers: what is missing, linking Signal, trying out Discord.
import { restartPrompt } from "./settings.js";
import { api, attempt, fill, h, icon, t } from "../api.js";

export async function discordPanel() {
  const s = await api("/api/discord");
  return h("section", { class: "panel stack" }, h("h2", {}, t("web.discord_status")),
    h("p", { class: s.connected ? "" : "notice" }, !s.enabled ? t("web.discord_setup") : s.problem ? t("web.discord_refused") : s.connected ? t("web.discord_connected", { name: s.name }) : t("web.discord_waiting")),
    s.invite ? h("a", { class: "button", href: s.invite, target: "_blank", rel: "noopener", title: t("web.discord_invite_tip") }, icon("plus"), t("web.discord_invite")) : null,
    s.connected ? h("button", { title: t("web.discord_test_tip"), onclick: () => attempt(() => api("/api/discord/test", { method: "POST" }), t("web.discord_test_sent")) }, icon("bell"), t("web.discord_test")) : null);
}

export function emailPanel() {
  return h("section", { class: "panel stack" }, h("h2", {}, t("web.email_status")), h("p", { class: "muted" }, t("web.email_hint")),
    h("div", { class: "row" },
      h("button", { title: t("web.email_test_tip"), onclick: () => attempt(() => api("/api/email/test", { method: "POST" }), t("web.email_sent")) }, icon("mail"), t("web.email_test")),
      h("button", { title: t("web.email_export_tip"), onclick: async () => {
        const password = prompt(t("web.confirm_password"));
        if (password) await attempt(() => api("/api/email/export", { method: "POST", body: { password } }), t("web.email_sent"));
      } }, icon("download"), t("web.email_export"))));
}

// A code only works for two minutes, so it is replaced in time while someone is still here.
const CODES = 5;

export async function signalPanel() {
  const panel = h("section", { class: "panel stack" });
  let timer = null;
  let problem = null;
  const showCode = async (round = 1) => {
    clearTimeout(timer);
    const code = await attempt(() => api("/api/signal/link", { method: "POST", body: {} }));
    if (!code) { draw(); return; }
    const started = Date.now();
    const bar = h("i");
    const left = h("small", { class: "muted" });
    fill(panel, h("h2", {}, t("web.signal_status")),
      h("ol", { class: "steps-list" }, ["1", "2", "3"].map((n) => h("li", {}, t(`web.signal_step${n}`)))),
      h("img", { class: "qr light big", src: code.qr, alt: "QR" }),
      h("div", { class: "meter" }, bar), left,
      h("small", { class: "muted" }, t("web.signal_other_screen")),
      h("div", { class: "row" }, h("button", { onclick: () => { clearTimeout(timer); draw(); } }, t("web.cancel"))));
    let ticks = 0;
    const tick = async () => {
      if (!panel.isConnected) return;
      const rest = Math.max(0, Math.ceil(code.seconds - (Date.now() - started) / 1000));
      bar.style.width = `${(rest / code.seconds) * 100}%`;
      left.textContent = t("web.signal_code_left", { seconds: rest });
      if (ticks++ % 3 === 0) {
        const state = (await api("/api/signal").catch(() => null))?.link?.state;
        if (state === "linked" || state === "failed") { problem = state === "failed" ? "web.signal_failed" : null; draw(); return; }
      }
      if (rest > 0) { timer = setTimeout(tick, 1000); return; }
      if (round < CODES) { showCode(round + 1); return; }
      problem = "web.signal_expired";
      draw();
    };
    tick();
  };
  const draw = async () => {
    clearTimeout(timer);
    const s = await api("/api/signal");
    const state = !s.installed && !s.running ? "missing" : !s.running ? "stopped" : !s.linked ? "unlinked" : !s.connected ? "restart" : "ready";
    const link = h("button", { class: "primary", title: t("web.signal_link_tip"), onclick: () => { problem = null; showCode(); } }, icon("qrcode"), t("web.signal_link"));
    fill(panel, h("h2", {}, t("web.signal_status")),
      problem ? h("p", { class: "notice danger" }, t(problem)) : null,
      h("p", { class: state === "ready" ? "" : "notice" }, t(`web.signal_${state}`)),
      s.version ? h("small", { class: "muted" }, `signal-cli ${s.version}`) : null,
      state === "missing" ? h("pre", { class: "mono wrap" }, "sudo ./scripts/signal.sh install") : null,
      state === "stopped" ? h("pre", { class: "mono wrap" }, "cd ~/pi-guard && sudo ./scripts/signal.sh install") : null,
      state === "unlinked" ? link : null,
      state === "restart" ? h("button", { class: "primary", onclick: () => restartPrompt() }, icon("refresh"), t("web.signal_restart_now")) : null);
  };
  await draw();
  return panel;
}
