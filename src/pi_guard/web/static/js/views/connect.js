// Computer-Connect: for each computer with the agent, what it allows: a media remote, sending a
// file, ringing it and sending a text. The phone app shows this page too, so it works from there.
import { api, attempt, fill, h, icon, t, toast, upload, whileShown } from "../api.js";

const MEDIA_BUTTONS = [["previous", "player-track-prev"], ["play-pause", null], ["next", "player-track-next"]];
const VOLUME_BUTTONS = [["volume-down", "volume-2"], ["mute", "volume-off"], ["volume-up", "volume"]];
const CONFIG_EXAMPLE = "CONNECT_USER=anna\nCONNECT=media,files,ring,text\n# RECEIVE_DIR=/home/anna/Downloads/pi-guard";

function size(bytes) {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1e3))} kB`;
}

function mediaPanel(pc) {
  const box = h("div", { class: "cc-media", "aria-live": "polite" });
  let player = null;
  const draw = (m) => {
    if (!box.isConnected && m) return;
    const playing = m?.status === "playing";
    const send = async (action) => {
      const next = await attempt(() => api(`/api/connect/${pc.id}/media`, { method: "POST", body: { action, player } }));
      if (next) draw(next);
    };
    const picker = (m?.players || []).length > 1 ? h("select", { "aria-label": t("web.cc_player"), onchange: (e) => { player = e.target.value; refresh(); } },
      m.players.map((p) => h("option", { value: p.id, selected: p.id === m.player }, p.name))) : null;
    fill(box, picker,
      h("div", { class: "cc-now" }, h("span", { class: `cc-disc${playing ? " spinning" : ""}`, "aria-hidden": "true" }, icon("music")),
        h("div", { class: "grow clip" },
          m && m.status !== "none" ? [h("strong", { class: "block clip" }, m.title || t("web.cc_untitled")),
            h("small", { class: "muted block clip" }, [m.artist, m.name].filter(Boolean).join(" · "))]
            : h("span", { class: "muted" }, m ? t("web.cc_no_player") : t("web.loading")))),
      h("div", { class: "cc-controls", role: "group", "aria-label": t("web.cc_media") },
        MEDIA_BUTTONS.map(([action, ic]) => h("button", { class: action === "play-pause" ? "cc-play primary" : "icon-only", disabled: !m || m.status === "none",
          title: t(`web.cc_${action}`), "aria-label": t(`web.cc_${action}`), onclick: () => send(action) }, icon(ic || (playing ? "player-pause" : "player-play"))))),
      h("div", { class: "cc-volume" },
        VOLUME_BUTTONS.map(([action, ic]) => h("button", { class: `icon-only${action === "mute" && m?.muted ? " on" : ""}`, title: t(`web.cc_${action}`), "aria-label": t(`web.cc_${action}`), onclick: () => send(action) }, icon(ic))),
        m?.volume != null ? h("div", { class: "meter grow", role: "meter", "aria-valuenow": m.volume, "aria-valuemin": 0, "aria-valuemax": 100, "aria-label": t("web.cc_volume") },
          (() => { const bar = h("i"); bar.style.width = `${Math.min(100, m.volume)}%`; return bar; })()) : null,
        m?.volume != null ? h("small", { class: "muted" }, m.muted ? t("web.cc_muted") : `${m.volume} %`) : null));
  };
  const refresh = async () => {
    const m = await api(`/api/connect/${pc.id}/media${player ? `?player=${encodeURIComponent(player)}` : ""}`).catch(() => null);
    if (!box.isConnected) return;
    if (m) { player = player || m.player; draw(m); } else fill(box, h("p", { class: "muted" }, t("web.cc_media_failed")));
    whileShown(box, refresh, 10000);  // each look starts a few programs on that computer
  };
  draw(null);
  setTimeout(refresh, 0);
  return h("section", { class: "cc-part" }, h("h3", {}, icon("headphones"), " ", t("web.cc_media")), box);
}

function filePanel(pc) {
  const input = h("input", { type: "file", multiple: true, "aria-label": t("web.cc_pick") });
  const list = h("ul", { class: "list compact cc-files" });
  const send = h("button", { class: "primary", disabled: true }, icon("send"), t("web.cc_send"));
  const cancel = h("button", { class: "ghost hidden", onclick: () => upload.current?.abort() }, t("web.cancel"));
  input.addEventListener("change", () => {
    send.disabled = !input.files.length;
    fill(list, [...input.files].map((f) => h("li", {}, icon("file-upload"), h("span", { class: "grow clip" }, f.name), h("small", { class: "muted" }, size(f.size)))));
  });
  send.addEventListener("click", async () => {
    const files = [...input.files];
    const max = pc.connect.max_file || 2e9;
    send.disabled = true;
    input.disabled = true;
    cancel.classList.remove("hidden");
    let done = 0;
    for (const [i, file] of files.entries()) {
      const row = list.children[i];
      if (file.size > max) { row.append(h("span", { class: "badge danger" }, t("web.cc_too_large"))); continue; }
      const bar = h("i");
      const meter = h("div", { class: "meter cc-progress", role: "progressbar", "aria-valuemin": 0, "aria-valuemax": 100, "aria-label": file.name }, bar);
      row.append(meter);
      const form = new FormData();
      form.append("size", String(file.size));  // first: the Pi checks it before the file
      form.append("file", file, file.name);
      try {
        await upload(`/api/connect/${pc.id}/file`, form, (p) => { bar.style.width = `${Math.round(p * 100)}%`; meter.setAttribute("aria-valuenow", Math.round(p * 100)); });
        meter.replaceWith(h("span", { class: "badge ok" }, icon("circle-check"), " ", t("web.cc_arrived")));
        done += 1;
      } catch (error) {
        meter.replaceWith(h("span", { class: "badge danger", title: t(error.message) }, t(error.message === "web.cc_cancelled" ? "web.cc_cancelled" : "web.cc_failed")));
        if (error.message === "web.cc_cancelled") break;
      }
    }
    cancel.classList.add("hidden");
    input.disabled = false;
    input.value = "";
    if (done) toast(t("web.cc_sent", { count: done, name: pc.name }));
  });
  return h("section", { class: "cc-part" }, h("h3", {}, icon("file-upload"), " ", t("web.cc_files")),
    h("label", { class: "cc-drop" }, icon("upload"), h("span", {}, t("web.cc_pick")), input), list,
    h("div", { class: "row" }, send, cancel),
    h("small", { class: "muted" }, t("web.cc_files_hint", { max: size(pc.connect.max_file || 2e9) })));
}

function textPanel(pc) {
  const area = h("textarea", { rows: 3, maxlength: 4000, placeholder: t("web.cc_text_placeholder"), "aria-label": t("web.cc_text") });
  const copy = h("input", { type: "checkbox", checked: true });
  return h("section", { class: "cc-part" }, h("h3", {}, icon("clipboard"), " ", t("web.cc_text")), area,
    h("div", { class: "row wrap" }, h("label", { class: "check grow" }, copy, t("web.cc_copy")),
      h("button", { onclick: async () => {
        const r = await attempt(() => api(`/api/connect/${pc.id}/text`, { method: "POST", body: { text: area.value, copy: copy.checked } }));
        if (r) { toast(t(r.copied ? "web.cc_text_copied" : "web.cc_text_shown")); area.value = ""; }
      } }, icon("send"), t("web.cc_send"))));
}

function computerCard(pc) {
  const c = pc.connect || {};
  const parts = [c.media ? mediaPanel(pc) : null, c.files ? filePanel(pc) : null, c.text ? textPanel(pc) : null];
  const tooOld = pc.connect == null;  // agents before 0.5 report no "connect" at all
  const nothing = !parts.some(Boolean) && !c.ring;
  return h("article", { class: `panel stack cc-card${pc.online ? "" : " offline"}` },
    h("div", { class: "cc-head" },
      h("div", { class: "agent-head" }, h("span", { class: `dot ${pc.online ? "ok" : ""}` }), icon("devices-pc"),
        h("div", { class: "grow agent-name" }, h("h2", {}, pc.name), h("small", { class: "muted" }, [pc.os, pc.online ? t("web.mob_online") : t("web.mob_offline")].filter(Boolean).join(" · ")))),
      c.ring && pc.online ? h("button", { onclick: () => attempt(() => api(`/api/connect/${pc.id}/ring`, { method: "POST" }), t("web.cc_ringing")) }, icon("bell-ringing"), t("web.cc_ring")) : null),
    !pc.online ? h("p", { class: "muted" }, t("web.cc_offline")) : null,
    pc.online && (tooOld || nothing) ? h("div", { class: "notice stack" }, h("p", {}, t(tooOld ? "web.cc_too_old" : "web.cc_nothing")),
      h("pre", { class: "mono wrap" }, CONFIG_EXAMPLE), h("small", { class: "muted" }, t("web.cc_config_hint"))) : null,
    pc.online ? h("div", { class: "cc-grid" }, parts) : null);
}

export async function connectPanel(box) {
  const list = await api("/api/connect").catch(() => null);
  if (!list) { fill(box, h("p", { class: "notice" }, t("web.addon_restart"))); return; }
  fill(box, list.length ? list.map(computerCard)
    : h("section", { class: "panel stack" }, h("p", {}, t("web.cc_none")), h("p", { class: "muted" }, t("web.ag_desktop_app")), h("a", { class: "button primary", href: "#/addons/agents" }, icon("plus"), t("web.cc_add"))));
}
