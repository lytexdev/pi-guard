// Reverse proxy: this Pi as server for other machines, or as client of a server elsewhere.
import { askDialog, api, attempt, confirmDialog, fill, h, icon, t, whileShown } from "../api.js";

const bytes = (n) => (n > 1e9 ? `${(n / 1e9).toFixed(1)} GB` : n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.round((n || 0) / 1e3)} kB`);
const password = () => askDialog(t("web.confirm_password"), { type: "password" });
const ports = (text) => text.split(/[\s,]+/).filter(Boolean).map(Number);

function copyBox(text, label) {
  const area = h("textarea", { class: "mono", rows: Math.min(14, text.split("\n").length + 1), readonly: true, "aria-label": label }, text);
  return h("div", { class: "stack tight" }, h("label", {}, label), area,
    h("div", {}, h("button", { onclick: async () => { try { await navigator.clipboard.writeText(text); } catch { area.select(); } } }, icon("copy"), t("web.copy"))));
}

function serverPart(data, redraw, box) {
  const live = Object.fromEntries((data.status?.clients || []).map((c) => [c.name, c]));
  const name = h("input", { placeholder: t("web.px_client_name"), "aria-label": t("web.px_client_name"), maxlength: 40 });
  const wanted = h("input", { placeholder: t("web.px_ports_hint"), "aria-label": t("web.px_ports") });
  const host = h("input", { placeholder: t("web.px_host_hint"), "aria-label": t("web.px_host") });
  const local = h("input", { type: "checkbox" });
  return [
    h("p", {}, t("web.px_server_hint", { port: data.listen_port })),
    data.fingerprint ? h("p", { class: "mono wrap" }, h("small", { class: "muted" }, t("web.px_fingerprint"), " "), data.fingerprint) : null,
    h("h3", {}, t("web.px_clients")),
    h("ul", { class: "list" }, data.clients.length ? data.clients.map((c) => {
      const s = live[c.name] || {};
      return h("li", { class: "row" },
        h("div", { class: "grow" }, h("strong", {}, c.name), " ",
          h("span", { class: `badge ${s.connected ? "ok" : ""}` }, t(s.connected ? "web.px_online" : "web.px_offline")),
          h("small", { class: "block muted" }, `${t("web.px_ports")}: ${c.ports.map((p) => `${p.bind === "127.0.0.1" ? "127.0.0.1:" : ""}${p.port}`).join(", ")}`,
            s.address ? ` · ${s.address}` : "", s.connections ? ` · ${s.connections} ${t("web.px_connections")} · ↓${bytes(s.bytes_in)} ↑${bytes(s.bytes_out)}` : "")),
        h("button", { class: "icon-only danger", title: t("web.delete"), onclick: async () => {
          if (await confirmDialog(t("web.px_remove", { name: c.name }), t("web.delete"), true) && await attempt(() => api(`/api/proxy/clients/${encodeURIComponent(c.name)}`, { method: "DELETE" }), t("web.deleted"))) redraw();
        } }, icon("trash")));
    }) : h("li", { class: "empty" }, t("web.px_no_clients"))),
    h("h3", {}, t("web.px_add")),
    h("div", { class: "row wrap" }, name, wanted, host),
    h("label", { class: "check" }, local, t("web.px_local_only")),
    h("div", {}, h("button", { class: "primary", onclick: async () => {
      const pass = await password();
      if (!pass) return;
      const made = await attempt(() => api("/api/proxy/clients", { method: "POST", body: { name: name.value.trim(), ports: ports(wanted.value), host: host.value.trim(), bind: local.checked ? "127.0.0.1" : "0.0.0.0", password: pass } }), t("web.saved"));
      if (!made) return;
      await redraw();
      // Shown once: the server keeps only a hash of the token.
      box.append(h("section", { class: "panel stack" }, h("h3", {}, t("web.px_made", { name: made.config.name })),
        h("p", { class: "notice" }, t("web.px_once")),
        copyBox(JSON.stringify(made.config, null, 2), t("web.px_config_file")),
        copyBox(`pgproxy client client.json`, t("web.px_run"))));
    } }, icon("plus"), t("web.px_add_button"))),
  ];
}

function clientPart(data, redraw) {
  const c = data.client || {};
  const server = h("input", { value: c.server || "", placeholder: "proxy.example.org:7000", "aria-label": t("web.px_server") });
  const fingerprint = h("input", { class: "mono", value: c.fingerprint || "", "aria-label": t("web.px_fingerprint") });
  const name = h("input", { value: c.name || "", "aria-label": t("web.px_client_name"), maxlength: 40 });
  const token = h("input", { type: "password", autocomplete: "off", placeholder: c.name ? t("web.px_token_keep") : "", "aria-label": t("web.px_token") });
  const rows = h("div", { class: "stack tight" });
  const row = (f = {}) => {
    const remote = h("input", { type: "number", min: 1024, max: 65535, value: f.remote_port || "", placeholder: t("web.px_remote_port"), "aria-label": t("web.px_remote_port") });
    const target = h("input", { value: f.local || "", placeholder: "127.0.0.1:8123", "aria-label": t("web.px_local") });
    const line = h("div", { class: "row px-forward" }, remote, "→", target, h("button", { class: "icon-only", title: t("web.delete"), onclick: () => line.remove() }, icon("x")));
    rows.append(line);
  };
  (c.forwards || []).forEach(row);
  if (!(c.forwards || []).length) row();
  const s = data.status || {};
  return [
    h("p", {}, t("web.px_client_hint")),
    data.client ? h("p", {}, h("span", { class: `badge ${s.connected_since ? "ok" : ""}` }, t(s.connected_since ? "web.px_online" : "web.px_offline")),
      s.refused ? h("span", { class: "muted" }, " ", s.refused) : null,
      s.connections ? h("small", { class: "muted" }, ` · ${s.connections} ${t("web.px_connections")} · ↓${bytes(s.bytes_in)} ↑${bytes(s.bytes_out)}`) : null) : null,
    h("label", { class: "field" }, h("span", {}, t("web.px_server")), server),
    h("label", { class: "field" }, h("span", {}, t("web.px_fingerprint")), fingerprint),
    h("label", { class: "field" }, h("span", {}, t("web.px_client_name")), name),
    h("label", { class: "field" }, h("span", {}, t("web.px_token")), token),
    h("h3", {}, t("web.px_forwards")), h("p", { class: "notice" }, t("web.px_expose_warning")), rows,
    h("div", { class: "row" }, h("button", { onclick: () => row() }, icon("plus"), t("web.px_add_forward")),
      h("button", { class: "primary", onclick: async () => {
        const forwards = [...rows.querySelectorAll(".px-forward")].map((line) => {
          const [remote, target] = line.querySelectorAll("input");
          return { remote_port: Number(remote.value), local: target.value.trim() };
        }).filter((f) => f.remote_port || f.local);
        const pass = await password();
        if (pass && await attempt(() => api("/api/proxy/client", { method: "PUT", body: { server: server.value.trim(), fingerprint: fingerprint.value.trim(), name: name.value.trim(), token: token.value.trim(), forwards, password: pass } }), t("web.saved"))) redraw();
      } }, icon("check"), t("web.save"))),
  ];
}

export async function proxyPanel(box) {
  const redraw = async () => {
    let data;
    try { data = await api("/api/proxy"); } catch { fill(box, h("p", { class: "notice" }, t("web.addon_not_running"))); return; }
    if (!box.isConnected) return;
    const role = h("select", { "aria-label": t("web.px_role") }, ["off", "server", "client"].map((r) => h("option", { value: r, selected: r === data.role }, t(`web.px_role_${r}`))));
    const port = h("input", { type: "number", min: 1024, max: 65535, value: data.listen_port, "aria-label": t("web.px_listen_port") });
    fill(box,
      h("section", { class: "panel stack" }, h("h2", {}, icon("network"), " ", t("addons.proxy")),
        h("p", { class: "muted" }, t("web.px_intro")),
        data.installed ? null : h("p", { class: "notice" }, t("web.px_not_installed")),
        data.error ? h("p", { class: "notice danger" }, t(data.error)) : null,
        h("div", { class: "row wrap" }, h("label", { class: "field" }, h("span", {}, t("web.px_role")), role),
          data.role !== "client" ? h("label", { class: "field" }, h("span", {}, t("web.px_listen_port")), port) : null,
          h("button", { onclick: async () => {
            const pass = await password();
            if (pass && await attempt(() => api("/api/proxy/role", { method: "PUT", body: { role: role.value, listen_port: Number(port.value), password: pass } }), t("web.saved"))) redraw();
          } }, icon("check"), t("web.save"))),
        h("p", { class: "muted" }, h("small", {}, t("web.px_router_hint")))),
      data.role === "server" ? h("section", { class: "panel stack" }, h("h2", {}, t("web.px_role_server")), serverPart(data, redraw, box)) : null,
      data.role === "client" ? h("section", { class: "panel stack" }, h("h2", {}, t("web.px_role_client")), clientPart(data, redraw)) : null);
    if (data.role !== "off") whileShown(box, redraw, 15000);
  };
  await redraw();
}
