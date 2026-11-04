// A person's own login: password and second factor. All that viewers can change.
import { api, attempt, fill, h, icon, t, toast } from "../api.js";

export async function render(main) {
  const draw = async () => {
    const account = await api("/api/account");
    const current = h("input", { id: "ocur", type: "password", autocomplete: "current-password" });
    const next = h("input", { id: "onew", type: "password", autocomplete: "new-password", minlength: 10 });
    const totp = h("section", { class: "panel stack" }, h("h2", {}, t("web.totp_title")));
    if (account.totp) {
      totp.append(h("p", {}, h("span", { class: "badge ok" }, icon("shield-check"), t("web.totp_on"))),
        h("button", { class: "danger", onclick: async () => {
          const password = prompt(t("web.confirm_password"));
          if (password && await attempt(() => api("/api/account/totp/disable", { method: "POST", body: { password } }), t("web.saved"))) draw();
        } }, t("web.totp_disable")));
    } else {
      totp.append(h("p", { class: "muted" }, t("web.totp_explain")), h("button", { class: "primary", onclick: async () => {
        const setup = await attempt(() => api("/api/account/totp/begin", { method: "POST" }));
        if (!setup) return;
        const code = h("input", { id: "ocode", inputmode: "numeric", autocomplete: "one-time-code", maxlength: 6 });
        fill(totp, h("h2", {}, t("web.totp_title")), h("p", {}, t("web.totp_scan")), h("img", { class: "qr", src: setup.qr, alt: "QR" }),
          h("p", { class: "muted" }, t("web.totp_manual"), " ", h("code", {}, setup.secret)),
          h("div", { class: "field" }, h("label", { for: "ocode" }, t("web.totp_code")), code),
          h("button", { class: "primary", onclick: async () => {
            if (await attempt(() => api("/api/account/totp/confirm", { method: "POST", body: { code: code.value } }), t("web.totp_enabled"))) draw();
          } }, icon("check"), t("web.totp_confirm")));
      } }, icon("shield-lock"), t("web.totp_setup")));
    }
    fill(main, h("div", { class: "topbar" }, h("h1", {}, t("web.nav_account"))),
      h("section", { class: "panel stack" }, h("h2", {}, account.username),
        h("p", { class: "muted" }, t("web.account_viewer_hint")),
        h("div", { class: "field" }, h("label", { for: "ocur" }, t("web.current_password")), current),
        h("div", { class: "field" }, h("label", { for: "onew" }, t("web.new_password")), next),
        h("button", { class: "primary", onclick: async () => {
          if (await attempt(() => api("/api/me/password", { method: "POST", body: { current: current.value, password: next.value } }))) {
            toast(t("web.saved")); current.value = ""; next.value = "";
          }
        } }, icon("check"), t("web.save"))), totp);
  };
  await draw();
}
