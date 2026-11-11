// Teaching faces by swiping: one card per face from recent person events.
// Right: yes, it is the suggested person. Left: no (or a stranger). Up: skip. A name below the
// picture says who it is instead. Arrow keys and the buttons do the same, for every hand.
// Only people already enrolled can be learned; enrolling needs their consent.
import { api, attempt, h, icon, t, toast, when } from "../api.js";

const SWIPE = 90;  // pixels to count as a swipe

function question(face) {
  if (face.verdict === "known") return t("web.sw_is", { name: face.name });
  if (face.verdict === "uncertain" && face.name) return t("web.sw_maybe", { name: face.name });
  return t("web.sw_who");
}

export async function openSwipe(onDone = () => {}) {
  const queue = await attempt(() => api("/api/faces/review"));
  if (!queue) return;
  const stage = h("div", { class: "sw-stage", "aria-live": "polite" });
  const counter = h("small", { class: "muted" });
  let done = 0;
  let learned = 0;
  const dialog = h("dialog", { class: "sw-dialog" },
    h("div", { class: "row between" }, h("h2", {}, t("web.sw_title")), counter,
      h("button", { class: "icon-only", title: t("web.close"), onclick: () => dialog.close() }, icon("x"))),
    h("p", { class: "muted" }, h("small", {}, t("web.sw_intro"))), stage);
  dialog.addEventListener("close", () => { dialog.remove(); removeEventListener("keydown", keys); if (learned) onDone(); });
  document.body.append(dialog);
  dialog.showModal();
  const count = () => { counter.textContent = t("web.sw_count", { done, learned }); };
  count();
  if (!queue.people.length) { stage.replaceChildren(h("p", { class: "notice" }, t("web.sw_no_people"))); return; }

  // Cards come one event at a time, so the Pi looks for faces only in pictures someone sees.
  async function* cards() {
    for (const event of queue.events) {
      const data = await api(`/api/events/${event.id}/faces`).catch(() => null);
      for (const [index, face] of (data?.faces || []).entries()) {
        if (!event.seen.includes(index) && !face.learn_problem) yield { event, index, face };
      }
      api(`/api/faces/review/${event.id}/999`, { method: "POST" }).catch(() => {});
    }
  }
  const deck = cards();
  let current = null;
  let busy = false;

  const act = async (kind, name) => {
    if (!current || busy) return;
    busy = true;
    const { event, index, face } = current;
    const card = stage.firstElementChild;
    card?.classList.add(`gone-${kind === "yes" || kind === "name" ? "right" : kind === "skip" ? "up" : "left"}`);
    let ok = true;
    if (kind === "yes" || kind === "name") {
      const result = await attempt(() => api(`/api/events/${event.id}/faces/${index}`, { method: "POST", body: { name: name || face.name, is: true } }));
      ok = Boolean(result);
      if (ok) { learned += 1; toast(t("web.lf_learned", { name: name || face.name, total: result.total })); }
    } else if (kind === "no" && face.name) {
      ok = Boolean(await attempt(() => api(`/api/events/${event.id}/faces/${index}`, { method: "POST", body: { name: face.name, is: false } })));
    } else {
      await api(`/api/faces/review/${event.id}/${index}`, { method: "POST" }).catch(() => {});
    }
    if (!ok) card?.classList.remove("gone-left", "gone-right", "gone-up");
    else { done += 1; count(); }
    busy = false;
    if (ok) setTimeout(next, 180);
  };

  function show({ event, face, index }) {
    const named = face.name && face.verdict !== "unknown";
    const picture = h("img", { src: `/api/events/${event.id}/faces/${index}/crop`, alt: t("web.sw_alt"), draggable: "false" });
    const card = h("div", { class: "sw-card", tabindex: "0" },
      h("div", { class: "sw-picture" }, picture, h("span", { class: "sw-hint yes" }, "✓"), h("span", { class: "sw-hint no" }, "✗")),
      h("div", { class: "sw-info" }, h("strong", {}, question(face)),
        h("small", { class: "muted block" }, [event.camera, when(event.ts), face.score ? `${t("web.sw_score")} ${face.score}` : ""].filter(Boolean).join(" · "))));
    // Dragging with a finger or the mouse; the card follows and tilts.
    let start = null;
    card.addEventListener("pointerdown", (e) => { start = [e.clientX, e.clientY]; card.setPointerCapture(e.pointerId); card.classList.add("dragging"); });
    card.addEventListener("pointermove", (e) => {
      if (!start) return;
      const [dx, dy] = [e.clientX - start[0], e.clientY - start[1]];
      card.style.transform = `translate(${dx}px, ${Math.min(dy, 0)}px) rotate(${dx / 18}deg)`;
      card.dataset.lean = dx > 40 ? "yes" : dx < -40 ? "no" : "";
    });
    const release = (e) => {
      if (!start) return;
      const [dx, dy] = [e.clientX - start[0], e.clientY - start[1]];
      start = null;
      card.classList.remove("dragging");
      card.style.transform = "";
      card.dataset.lean = "";
      if (dx > SWIPE && named) act("yes");
      else if (dx < -SWIPE) act("no");
      else if (dy < -SWIPE) act("skip");
    };
    card.addEventListener("pointerup", release);
    card.addEventListener("pointercancel", release);
    const names = h("div", { class: "row wrap sw-names" }, h("small", { class: "muted" }, t("web.sw_it_is")),
      queue.people.filter((p) => p !== face.name || !named).map((p, i) => h("button", { class: "small", title: i < 9 ? `${i + 1}` : "", onclick: () => act("name", p) }, p)));
    stage.replaceChildren(card,
      h("div", { class: "sw-buttons" },
        h("button", { class: "sw-no", title: `${t(named ? "web.sw_no" : "web.sw_stranger")} (←)`, onclick: () => act("no") }, icon("x"), t(named ? "web.sw_no_short" : "web.sw_stranger")),
        h("button", { class: "sw-skip", title: `${t("web.sw_skip")} (↑)`, onclick: () => act("skip") }, t("web.sw_skip")),
        named ? h("button", { class: "sw-yes primary", title: `${t("web.sw_yes", { name: face.name })} (→)`, onclick: () => act("yes") }, icon("check"), t("web.sw_yes", { name: face.name })) : null),
      names);
    card.focus();
  }

  async function next() {
    stage.replaceChildren(h("p", { class: "muted sw-loading" }, t("web.sw_loading")));
    const item = await deck.next();
    if (item.done) {
      current = null;
      stage.replaceChildren(h("div", { class: "sw-end stack" }, icon("mood-happy", "lg"), h("p", {}, t(done ? "web.sw_all_done" : "web.sw_nothing")),
        h("button", { class: "primary", onclick: () => dialog.close() }, t("web.done"))));
      return;
    }
    current = item.value;
    show(current);
  }

  function keys(e) {
    if (!dialog.open || !current || e.target.matches?.("input, select, textarea")) return;
    const named = current.face.name && current.face.verdict !== "unknown";
    if (e.key === "ArrowRight" && named) { e.preventDefault(); act("yes"); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); act("no"); }
    else if (e.key === "ArrowUp") { e.preventDefault(); act("skip"); }
    else if (/^[1-9]$/.test(e.key)) {
      const options = queue.people.filter((p) => p !== current.face.name || !named);
      const pick = options[Number(e.key) - 1];
      if (pick) act("name", pick);
    }
  }
  addEventListener("keydown", keys);
  next();
}
