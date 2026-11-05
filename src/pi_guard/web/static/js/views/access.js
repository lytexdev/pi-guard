// Who can use pi-guard: chat users, invitations, key files, sessions, bans.
import { askDialog, api, attempt, confirmDialog, h, icon, t, toast, when, fill } from "../api.js";
import { brandIcon } from "../widgets.js";
import { createCredential, explainKeyError } from "../webauthn.js";

function showCodes(codes) {
  const dialog = h("dialog", {}, h("h2", {}, t("web.recovery_title")), h("p", {}, t("web.recovery_save")),
    h("pre", { class: "mono codes" }, codes.join("\n")),
    h("div", { class: "row end" },
      h("button", { onclick: () => navigator.clipboard?.writeText(codes.join("\n")).then(() => toast(t("web.copied"))) }, t("web.copy")),
      h("button", { class: "primary", onclick: () => dialog.close() }, t("web.done"))));
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
}

function keyGuide(keys) {
  // Browsers want a name instead of an IP address, and a certificate they trust.
  const urls = keys.names.map((name) => `https://${name}:${keys.port}`);
  return h("details", { class: "stack", open: !keys.usable_here }, h("summary", {}, t("web.key_guide")),
    h("ol", {},
      h("li", {}, t("web.key_guide_name"), urls.length ? [" ", urls.map((url) => h("a", { href: url }, url))] : null,
        h("pre", { class: "mono wrap" }, "sudo ./scripts/dashboard-name.sh pi-guard.lan")),
      h("li", {}, t("web.key_guide_ca"), " ", h("a", { href: "/ca.crt", download: "pi-guard-ca.crt" }, t("web.key_guide_ca_link")),
        h("pre", { class: "mono wrap" }, "sudo trust anchor --store pi-guard-ca.crt")),
      h("li", {}, t("web.key_guide_touch"))),
    h("small", { class: "muted" }, t("web.key_guide_here", { host: location.host })));
}

async function peoplePanel(draw) {
  // Who may open the dashboard: admins change everything, viewers only watch.
  const data = await api("/api/dashboard-users");
  const name = h("input", { placeholder: t("web.username"), maxlength: 32, autocomplete: "off", "aria-label": t("web.username") });
  const password = h("input", { type: "password", placeholder: t("web.password"), autocomplete: "new-password", "aria-label": t("web.password") });
  const roles = (selected) => ["viewer", "admin"].map((r) => h("option", { value: r, selected: r === selected }, t(`web.role_${r}`)));
  const role = h("select", { "aria-label": t("web.role") }, roles("viewer"));
  return h("section", { class: "panel stack" }, h("h2", {}, icon("users"), " ", t("web.users_title")),
    h("p", { class: "muted" }, t("web.users_hint")),
    h("ul", { class: "list" },
      h("li", {}, icon("shield-lock"), h("div", { class: "grow" }, data.owner), h("span", { class: "badge accent" }, t("web.role_owner"))),
      data.users.map((u) => {
        const select = h("select", { "aria-label": t("web.role") }, roles(u.role));
        select.addEventListener("change", () => attempt(() => api(`/api/dashboard-users/${u.id}`, { method: "PATCH", body: { role: select.value } }), t("web.saved")));
        return h("li", {}, icon("user"), h("div", { class: "grow" }, u.username, u.totp ? h("small", { class: "block" }, t("web.totp_on")) : null), select,
          h("button", { class: "icon-only danger", title: t("web.delete"), onclick: async () => {
            if (await confirmDialog(t("web.users_delete", { name: u.username }), t("web.delete"), true)
                && await attempt(() => api(`/api/dashboard-users/${u.id}`, { method: "DELETE" }), t("web.deleted"))) draw();
          } }, icon("trash")));
      })),
    h("div", { class: "row" }, name, password, role, h("button", { class: "primary", onclick: async () => {
      if (await attempt(() => api("/api/dashboard-users", { method: "POST", body: { username: name.value, password: password.value, role: role.value } }), t("web.saved"))) draw();
    } }, icon("plus"), t("web.users_add"))));
}

async function loginPanel(account, draw) {
  // Second factors and passkeys.
  const keys = await api("/api/account/webauthn");
  const name = h("input", { placeholder: t("web.webauthn_name"), maxlength: 40, "aria-label": t("web.webauthn_name") });
  const problem = h("div", { "aria-live": "polite" });
  return h("section", { class: "panel stack" }, h("h2", {}, icon("shield-lock"), " ", t("web.login_security")),
    h("h3", {}, t("web.webauthn_title")), h("p", { class: "muted" }, t("web.webauthn_hint")),
    keys.usable_here ? null : h("p", { class: "notice" }, t("web.webauthn_needs_name")),
    h("ul", { class: "list" }, keys.keys.length ? keys.keys.map((k) => h("li", {}, icon("key"),
      h("div", { class: "grow" }, h("div", {}, k.name), h("small", {}, k.last_used ? t("web.last_used", { time: when(k.last_used) }) : t("web.never_used"))),
      h("button", { class: "icon-only danger", title: t("web.delete"), onclick: async () => {
        if (await confirmDialog(t("web.webauthn_delete", { name: k.name }), t("web.delete"), true)
            && await attempt(() => api(`/api/account/webauthn/${k.id}`, { method: "DELETE" }))) draw();
      } }, icon("trash")))) : h("li", { class: "empty" }, t("web.nothing"))),
    keys.usable_here ? h("div", { class: "row" }, name, h("button", { onclick: async () => {
      fill(problem);
      const start = await attempt(() => api("/api/account/webauthn/begin", { method: "POST", body: {} }));
      if (!start) return;
      try {
        const credential = await createCredential(start.options);
        if (await attempt(() => api("/api/account/webauthn/finish", { method: "POST", body: { flow: start.flow, credential, name: name.value } }), t("web.saved"))) draw();
      } catch (error) { fill(problem, h("p", { class: "notice danger" }, explainKeyError(error))); }
    } }, icon("plus"), t("web.webauthn_add"))) : null,
    problem, keyGuide(keys),
    h("h3", {}, t("web.recovery_title")),
    account.totp ? h("p", { class: "muted" }, t("web.recovery_left", { count: account.recovery_left })) : h("p", { class: "muted" }, t("web.recovery_needs_totp")),
    account.totp ? h("button", { onclick: async () => {
      const password = await askDialog(t("web.confirm_password"), { type: "password" });
      if (!password) return;
      const result = await attempt(() => api("/api/account/recovery", { method: "POST", body: { password } }));
      if (result) { showCodes(result.codes); draw(); }
    } }, icon("refresh"), t("web.recovery_new")) : null,
    h("h3", {}, t("web.codeword")), h("p", { class: "muted" }, t("web.codeword_hint")),
    h("div", { class: "row" }, h("button", { onclick: async () => {
      const codeword = await askDialog(t("web.codeword_new"), { type: "password" });
      if (!codeword) return;
      const password = await askDialog(t("web.confirm_password"), { type: "password" });
      if (password && await attempt(() => api("/api/account/codeword", { method: "POST", body: { password, codeword } }), t("web.saved"))) draw();
    } }, icon("key"), account.codeword ? t("web.codeword_change") : t("web.codeword_set")),
    account.codeword ? h("button", { class: "danger", onclick: async () => {
      const password = await askDialog(t("web.confirm_password"), { type: "password" });
      if (password && await attempt(() => api("/api/account/codeword", { method: "POST", body: { password, codeword: "" } }), t("web.saved"))) draw();
    } }, t("web.codeword_remove")) : null));
}

export async function render(main) {
  const draw = async () => {
    const [users, access, account] = await Promise.all([api("/api/users"), api("/api/access"), api("/api/account")]);
    const security = account.owner ? await loginPanel(account, draw) : null;
    const people = await peoplePanel(draw);
    const keyName = h("input", { id: "kname", placeholder: "laptop", pattern: "[A-Za-z0-9_-]{1,40}" });
    const inviteOut = h("div", { class: "stack" });
    fill(main,
      h("div", { class: "topbar" }, h("h1", {}, t("web.nav_access"))),
      people,
      h("div", { class: "grid wide" },
        h("section", { class: "panel" }, h("h2", {}, t("web.account")),
          h("p", {}, icon("user"), " ", account.username, " ",
            account.totp ? h("span", { class: "badge ok" }, t("web.totp_on")) : null),
          h("div", { class: "row" },
            h("a", { class: "button", href: "#/setup/account" }, icon("key"), t("web.account_change")),
            account.totp ? h("button", { class: "danger", onclick: async () => {
              const password = await askDialog(t("web.totp_disable_confirm"), { type: "password" });
              if (password && await attempt(() => api("/api/account/totp/disable", { method: "POST", body: { password } }), t("web.saved"))) draw();
            } }, t("web.totp_disable")) : null)),
        security,
        h("section", { class: "panel" }, h("h2", {}, t("web.chat_users")),
          h("ul", { class: "list" }, users.map((u) => h("li", {},
            brandIcon(u.provider),
            h("div", { class: "grow" }, h("div", {}, u.name || u.user_id), h("small", {}, `${u.provider} · ${u.role}${u.configured ? ` · ${t("web.configured")}` : ""}`)),
            u.configured ? null : h("button", { class: "icon-only danger", title: t("web.delete"), onclick: async () => {
              if (await confirmDialog(t("web.remove_user_confirm"), t("web.delete"), true)) {
                await attempt(() => api(`/api/users/${u.provider}/${encodeURIComponent(u.user_id)}`, { method: "DELETE" })); draw();
              } } }, icon("user-x"))))),
          h("div", { class: "row" },
            h("button", { onclick: async () => {
              const r = await attempt(() => api("/api/invite", { method: "POST", body: { role: "viewer" } }));
              if (r) fill(inviteOut, h("p", {}, t("web.invite_code"), " ", h("code", {}, r.code)),
                r.link ? h("p", {}, h("a", { href: r.link, target: "_blank", rel: "noopener" }, r.link)) : null,
                h("small", { class: "muted" }, t("web.invite_expires", { time: when(r.expires) })));
            } }, icon("user-plus"), t("web.invite"))),
          inviteOut),
        h("section", { class: "panel" }, h("h2", {}, t("web.keyfiles")),
          h("p", { class: "muted" }, t("web.keyfiles_hint")),
          h("ul", { class: "list" }, access.keyfiles.length ? access.keyfiles.map((k) => h("li", {}, icon("key"),
            h("div", { class: "grow" }, h("div", {}, k.name), h("small", {}, k.last_used ? t("web.last_used", { time: when(k.last_used) }) : t("web.never_used"))),
            h("button", { class: "icon-only danger", title: t("web.delete"), onclick: async () => {
              await attempt(() => api(`/api/keyfiles/${encodeURIComponent(k.name)}`, { method: "DELETE" })); draw(); } }, icon("trash"))))
            : h("li", { class: "empty" }, t("web.nothing"))),
          h("div", { class: "row" }, keyName, h("button", { onclick: async () => {
            const response = await attempt(() => api("/api/keyfiles", { method: "POST", body: { name: keyName.value }, raw: true }));
            if (!response) return;
            const url = URL.createObjectURL(await response.blob());
            h("a", { href: url, download: `pi-guard-${keyName.value}.key` }).click();
            URL.revokeObjectURL(url); draw();
          } }, icon("download"), t("web.create_keyfile")))),
        h("section", { class: "panel" }, h("h2", {}, t("web.sessions")),
          h("p", {}, t("web.login_methods"), ": ", access.methods.join(", "), access.totp ? " + TOTP" : ""),
          h("ul", { class: "list" }, access.sessions.map((s) => h("li", {}, icon("device-mobile"),
            h("div", { class: "grow" }, h("div", {}, `${s.username} · ${s.ip}`), h("small", {}, `${when(s.last_seen)} · ${s.agent.slice(0, 60)}`))))),
          h("button", { class: "danger", onclick: async () => {
            if (await confirmDialog(t("web.logout_all_confirm"), t("web.logout_all"), true)) {
              await attempt(() => api("/api/sessions/logout-all", { method: "POST" })); location.hash = "#/login";
            } } }, icon("logout"), t("web.logout_all"))),
        h("section", { class: "panel" }, h("h2", {}, t("web.bans")),
          h("ul", { class: "list" }, Object.keys(access.bans).length ? Object.entries(access.bans).map(([ip, seconds]) => h("li", {}, icon("ban"),
            h("span", { class: "grow mono" }, ip), h("small", {}, `${Math.ceil(seconds / 60)} min`),
            h("button", { onclick: async () => { await attempt(() => api(`/api/bans/${encodeURIComponent(ip)}`, { method: "DELETE" })); draw(); } }, t("web.unban"))))
            : h("li", { class: "empty" }, t("web.nothing"))),
          h("p", {}, h("a", { href: "/ca.crt" }, icon("download"), " ", t("web.download_ca"))),
          h("p", { class: "muted" }, h("small", {}, t("web.ca_hint"))))));
  };
  await draw();
}
