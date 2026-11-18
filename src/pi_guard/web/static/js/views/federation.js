// Linked households: help another pi-guard (for example a grandmother's) without being in its
// network, or let someone help this one. The settings above switch it on; here are the links.
import { api, askDialog, attempt, confirmDialog, fill, h, icon, t, toast, when, whileShown } from "../api.js";

function modal(...children) {
  const dialog = h("dialog", { class: "wg-dialog wide" }, ...children);
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
  return dialog;
}

const password = () => askDialog(t("web.confirm_password"), { type: "password" });

function rightsPicker(data, role = "admin", extras = ["alerts"]) {
  const pick = h("select", { id: "fed_role" }, data.roles.map((r) => h("option", { value: r, selected: r === role }, t(`web.fed_role_${r}`))));
  const boxes = Object.fromEntries(data.extras.map((x) => [x, h("input", { type: "checkbox", checked: extras.includes(x) })]));
  return {
    node: h("div", { class: "stack" },
      h("div", { class: "field" }, h("label", { for: "fed_role" }, t("web.fed_role")), pick),
      data.extras.map((x) => h("label", { class: "check" }, boxes[x], h("span", {}, t(`web.fed_extra_${x}`), h("small", { class: "muted block" }, t(`web.fed_extra_${x}_hint`))))),
      h("p", { class: "muted" }, h("small", {}, t("web.fed_never")))),
    value: () => ({ role: pick.value, extras: data.extras.filter((x) => boxes[x].checked) }),
  };
}

async function makeCode(redraw) {
  const pass = await password();
  if (!pass) return;
  const made = await attempt(() => api("/api/federation/invite", { method: "POST", body: { password: pass } }));
  if (!made) return;
  const code = h("textarea", { class: "mono", rows: 4, readonly: true }, made.code);
  modal(h("h2", {}, icon("link"), " ", t("web.fed_code_title")),
    h("p", {}, t("web.fed_code_text", { hours: made.hours })), code,
    h("div", { class: "row end" },
      h("button", { onclick: () => navigator.clipboard?.writeText(made.code).then(() => toast(t("web.copied"))) }, icon("copy"), t("web.copy")),
      h("button", { class: "primary", onclick: (e) => { e.target.closest("dialog").close(); redraw(); } }, t("web.done"))));
  code.select();
}

async function enterCode(data, redraw) {
  const code = h("textarea", { id: "fed_code", class: "mono", rows: 4, placeholder: "PGF1.…", autocomplete: "off" });
  const rights = rightsPicker(data);
  const dialog = modal(h("h2", {}, icon("link"), " ", t("web.fed_join_title")), h("p", { class: "muted" }, t("web.fed_join_text")),
    h("div", { class: "field" }, h("label", { for: "fed_code" }, t("web.fed_code")), code), rights.node,
    h("div", { class: "row end" }, h("button", { onclick: () => dialog.close() }, t("web.cancel")),
      h("button", { class: "primary", onclick: async () => {
        const pass = await password();
        if (!pass) return;
        if (await attempt(() => api("/api/federation/join", { method: "POST", body: { code: code.value, ...rights.value(), password: pass } }), t("web.fed_joined"))) { dialog.close(); redraw(); }
      } }, icon("link"), t("web.fed_join"))));
  code.focus();
}

function statusLine(link) {
  const s = link.status;
  if (!s) return t(link.online ? "web.fed_waiting" : "web.fed_never_seen");
  const cameras = s.cameras || [];
  return [t(`mode.${(s.mode || "").toLowerCase()}`), t("web.fed_cameras", { online: cameras.filter((c) => c.online).length, total: cameras.length }),
    s.open_alerts ? t("web.fed_open_alerts", { count: s.open_alerts }) : null,
    s.last_event ? `${t(`event.${s.last_event.type}`)} ${when(s.last_event.ts)}` : null, s.version ? `v${s.version}` : null].filter(Boolean).join(" · ");
}

function linkRow(link, data, redraw) {
  const helper = link.side === "helper";
  const remove = async () => {
    if (!await confirmDialog(t(helper ? "web.fed_remove_helped" : "web.fed_remove_helper", { name: link.name }), t("web.fed_remove"), true)) return;
    const pass = await password();
    if (pass && await attempt(() => api(`/api/federation/links/${link.id}`, { method: "DELETE", body: { password: pass } }), t("web.saved"))) redraw();
  };
  const rename = async () => {
    const name = await askDialog(t("web.fed_rename"), { value: link.name });
    if (name && await attempt(() => api(`/api/federation/links/${link.id}`, { method: "PATCH", body: { name } }), t("web.saved"))) redraw();
  };
  const rights = () => {
    const picker = rightsPicker(data, link.role, link.extras);
    const dialog = modal(h("h2", {}, t("web.fed_rights_title", { name: link.name })), picker.node,
      h("div", { class: "row end" }, h("button", { onclick: () => dialog.close() }, t("web.cancel")),
        h("button", { class: "primary", onclick: async () => {
          const pass = await password();
          if (pass && await attempt(() => api(`/api/federation/links/${link.id}`, { method: "PATCH", body: { ...picker.value(), password: pass } }), t("web.saved"))) { dialog.close(); redraw(); }
        } }, icon("check"), t("web.save"))));
  };
  return h("li", { class: "stack fed-link" },
    h("div", { class: "row wrap" }, h("span", { class: `dot ${link.online ? "ok" : ""}`, title: t(link.online ? "web.topo_online" : "web.topo_offline") }),
      h("div", { class: "grow" }, h("strong", {}, link.name), " ",
        helper ? null : h("span", { class: "badge" }, t(`web.fed_role_${link.role}`)),
        h("small", { class: "muted block" }, helper ? statusLine(link) : [link.extras.map((x) => t(`web.fed_extra_${x}`)).join(", ") || t("web.fed_no_extras"), link.host ? ` · ${link.host}` : ""]),
        h("small", { class: "muted block" }, t("web.fed_key", { fingerprint: link.fingerprint }), link.last_seen ? ` · ${t("web.fed_last_seen", { time: when(link.last_seen) })}` : "")),
      helper ? h("a", { class: `button small${link.online ? " primary" : ""}`, href: `/fed/${link.id}/`, target: "_blank", rel: "noopener", "aria-disabled": String(!link.online) }, icon("home"), t("web.fed_open")) : h("button", { class: "small", onclick: rights }, icon("key"), t("web.fed_rights")),
      h("button", { class: "icon-only", title: t("web.fed_rename"), onclick: rename }, icon("pencil")),
      h("button", { class: "icon-only danger", title: t("web.fed_remove"), onclick: remove }, icon("trash"))),
    helper && link.alerts?.length ? h("ul", { class: "list compact" }, link.alerts.map((a) => h("li", {}, icon(a.kind === "sos" ? "alert-triangle" : "bell"),
      h("span", { class: "grow" }, a.kind === "sos" ? "SOS" : t(`event.${a.kind}`), a.camera ? ` · ${a.camera}` : ""), h("small", { class: "muted" }, when(a.ts))))) : null);
}

export async function render(box) {
  const draw = async () => {
    const data = await api("/api/federation").catch(() => null);
    if (!data) return;
    if (!data.enabled) { fill(box, h("section", { class: "panel stack" }, h("p", { class: "muted" }, t("web.fed_off_hint")))); return; }
    const helping = data.links.filter((l) => l.side === "helper");
    const helped = data.links.filter((l) => l.side === "helped");
    fill(box,
      h("section", { class: "panel stack" },
        h("div", { class: "row between wrap" }, h("h2", {}, icon("users"), " ", t("web.fed_helping")),
          h("button", { class: "primary", disabled: !data.listen || !data.address, onclick: () => makeCode(draw) }, icon("plus"), t("web.fed_make_code"))),
        h("p", { class: "muted" }, t("web.fed_helping_text")),
        !data.listen ? h("p", { class: "notice" }, t("web.fed_listen_hint", { port: data.port }))
          : data.address ? h("p", { class: "muted" }, h("small", {}, t("web.fed_address", { address: data.address, port: data.port })))
            : h("p", { class: "notice" }, t(data.address_problem || "web.fed_need_address")),
        h("ul", { class: "list" }, helping.length ? helping.map((l) => linkRow(l, data, draw)) : h("li", { class: "empty" }, t("web.fed_none_helping")))),
      h("section", { class: "panel stack" },
        h("div", { class: "row between wrap" }, h("h2", {}, icon("shield-check"), " ", t("web.fed_helped")),
          h("button", { onclick: () => enterCode(data, draw) }, icon("link"), t("web.fed_enter_code"))),
        h("p", { class: "muted" }, t("web.fed_helped_text")),
        h("ul", { class: "list" }, helped.length ? helped.map((l) => linkRow(l, data, draw)) : h("li", { class: "empty" }, t("web.fed_none_helped")))),
      h("p", { class: "muted" }, h("small", {}, t("web.fed_own_key", { name: data.name, fingerprint: data.fingerprint }))));
  };
  await draw();
  whileShown(box, draw, 15000);
}
