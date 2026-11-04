// Storage: what takes up the space, and clearing what can go.
import { api, attempt, confirmDialog, fill, h, icon, t, toast, when } from "../api.js";
import { meter } from "./overview.js";

const size = (bytes) => (bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.round(bytes / 1e6)} MB`);

export async function render(box) {
  const draw = async (refresh = false) => {
    const s = await api(`/api/storage${refresh ? "?refresh=1" : ""}`);
    const largest = Math.max(...s.items.map((i) => i.bytes), 1);
    const clean = async (item) => {
      const name = [t(`web.st_${item.id}`), item.name].filter(Boolean).join(" ");
      let body = { target: item.id, camera: item.id === "recordings" ? item.name : "" };
      if (item.id === "database") {
        if (!await confirmDialog(t("web.st_vacuum_confirm"), t("web.st_clean"))) return;
      } else {
        const days = Number(prompt(t("web.st_days", { name }), String(s.retention_days)));
        if (!days || days < 1) return;
        if (!await confirmDialog(t("web.st_confirm", { name, days }), t("web.st_clean"), true)) return;
        body = { ...body, days };
      }
      const result = await attempt(() => api("/api/storage/clean", { method: "POST", body }));
      if (result) { toast(t("web.st_done", { files: result.files, size: size(result.bytes) })); draw(); }
    };
    const system = async (target) => {
      if (!await confirmDialog(t(`web.st_${target}_confirm`), t("web.st_clean"))) return;
      if (await attempt(() => api("/api/storage/clean", { method: "POST", body: { target } }), t("web.st_started"))) setTimeout(draw, 6000);
    };
    fill(box,
      h("section", { class: "panel stack" }, h("h2", {}, t("web.set_storage")),
        h("div", { class: "stat" }, t("web.st_free", { free: size(s.free), total: size(s.total) })),
        meter(Math.round((s.used / s.total) * 100)),
        h("p", { class: "muted" }, t("web.st_intro", { days: s.retention_days }))),
      h("section", { class: "panel stack" }, h("h2", {}, t("web.st_pi_guard")),
        h("ul", { class: "list" }, s.items.filter((i) => i.bytes).map((i) => h("li", {},
          h("div", { class: "storage-row" }, h("div", { class: "row between" }, h("span", {}, t(`web.st_${i.id}`), i.name ? ` · ${i.name}` : ""), h("strong", {}, size(i.bytes))),
            meter(Math.round((i.bytes / largest) * 100), 101, 101),
            h("small", { class: "muted" }, t("web.st_files", { count: i.files }), i.oldest ? ` · ${t("web.st_oldest", { time: when(new Date(i.oldest * 1000).toISOString()) })}` : "")),
          i.cleanable ? h("button", { class: "icon-only", title: t("web.st_clean_tip"), onclick: () => clean(i) }, icon("trash")) : null))),
        h("p", { class: "muted" }, h("small", {}, t("web.st_evidence_note")))),
      s.root_job ? h("section", { class: "panel stack" }, h("h2", {}, t("web.st_system")),
        s.system ? [
          h("ul", { class: "list" },
            h("li", {}, h("span", { class: "grow" }, t("web.st_journal")), h("strong", {}, size(s.system.journal)), h("button", { class: "icon-only", title: t("web.st_journal_tip"), onclick: () => system("journal") }, icon("trash"))),
            h("li", {}, h("span", { class: "grow" }, t("web.st_apt")), h("strong", {}, size(s.system.apt)), h("button", { class: "icon-only", title: t("web.st_apt_tip"), onclick: () => system("apt") }, icon("trash")))),
          h("h3", {}, t("web.st_folders")),
          h("ul", { class: "list" }, s.system.folders.map((f) => h("li", {}, h("span", { class: "grow mono" }, f.path), h("span", {}, size(f.bytes)))))]
          : h("p", { class: "muted" }, t("web.st_measuring")),
        h("button", { class: "ghost", onclick: async () => { await draw(true); setTimeout(draw, 8000); } }, icon("refresh"), t("web.st_refresh"))) : null);
    if (s.root_job && !s.system) setTimeout(draw, 8000);
  };
  await draw();
}
