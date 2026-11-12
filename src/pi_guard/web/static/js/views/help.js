// Help and tutorial: every part of pi-guard in a few sentences, with a way to it, and a guided
// tour. The same chapters as tutorial.py, which the chats (/tutorial) and the assistant use.
// The apps show this page too: the desktop app under PiGuard › Tutorial, the phone app as a
// quick action. "#/help/tour" starts the tour at once.
import { h, icon, t } from "../api.js";
import { state } from "../app.js";

export const CHAPTERS = [
  ["overview", "#/overview", "home"], ["live", "#/live", "video"], ["events", "#/events", "bell"],
  ["people", "#/people", "users"], ["network", "#/network/overview", "network"], ["vpn", "#/network/vpn", "brand-wireguard"],
  ["kids", "#/addons/kids", "mood-happy"], ["emergency", "#/settings/emergency", "alert-triangle"], ["medical", "#/addons/medical", "user-check"],
  ["apps", "#/addons/mobile", "device-mobile"], ["assistant", "#/addons/assistant", "speakerphone"], ["move", "#/settings/data/move", "map-pin"],
  ["settings", "#/settings", "settings"],
];
// What viewers can open; the rest of the tour is for admins.
const VIEWER = ["overview", "live", "events"];

const chapters = () => (state.session?.role === "viewer" ? CHAPTERS.filter(([id]) => VIEWER.includes(id)) : CHAPTERS);

export function startTour(step = 0) {
  const list = chapters();
  const [id, route, ic] = list[step];
  const dialog = h("dialog", { class: "tour" },
    h("small", { class: "muted" }, t("web.tour_step", { n: step + 1, total: list.length })),
    h("div", { class: "tour-progress", "aria-hidden": "true" }, h("span", { style: `width:${((step + 1) / list.length) * 100}%` })),
    h("h2", {}, icon(ic), " ", t(`web.tut_${id}`)), h("p", {}, t(`web.tut_${id}_text`)),
    h("div", { class: "row end" },
      step ? h("button", { class: "ghost", onclick: () => { dialog.close(); startTour(step - 1); } }, t("web.back")) : null,
      h("button", { onclick: () => dialog.close() }, t("web.tour_end")),
      h("button", { class: "primary", onclick: () => {
        dialog.close();
        if (step + 1 < list.length) startTour(step + 1); else location.hash = "#/help";
      } }, step + 1 < list.length ? t("web.next") : t("web.done"))));
  location.hash = route;
  dialog.addEventListener("close", () => dialog.remove());
  // The page behind changes; the dialog waits a moment so it opens over the new page.
  setTimeout(() => { document.body.append(dialog); dialog.showModal(); }, 250);
}

export async function render(main, params = []) {
  main.replaceChildren(
    h("div", { class: "topbar" }, h("h1", {}, t("web.tut_title"))),
    h("section", { class: "panel stack help-hero" }, h("p", {}, t("web.tut_intro")),
      h("div", { class: "row wrap" }, h("button", { class: "primary big", onclick: () => startTour() }, icon("player-play"), t("web.tut_start")),
        h("button", { onclick: () => document.querySelector(".a11y-open")?.click() }, icon("accessible"), t("web.tut_a11y"))),
      h("p", { class: "muted" }, h("small", {}, t("web.tut_ask")))),
    h("div", { class: "help-grid" }, chapters().map(([id, route, ic]) => h("article", { class: "help-card" },
      h("h2", {}, icon(ic), " ", t(`web.tut_${id}`)), h("p", { class: "muted" }, t(`web.tut_${id}_text`)),
      h("a", { class: "button small", href: route }, t("web.tut_open"), icon("chevron-right"))))),
    h("section", { class: "panel stack" }, h("h2", {}, t("web.tut_more_title")), h("p", { class: "muted" }, t("web.tut_more")),
      h("p", {}, h("a", { href: "https://github.com/lytexdev/pi-guard/tree/main/docs", target: "_blank", rel: "noopener" }, t("web.tut_docs")))));
  if (params[0] === "tour") startTour();
}
