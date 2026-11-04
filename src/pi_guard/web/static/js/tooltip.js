// Tooltips for everything with a title. The browser's own appear late and not on keyboard focus.
const DELAY = 350;
const GAP = 8;
let box = null, timer = null, current = null;

function place(target) {
  const rect = target.getBoundingClientRect();
  const size = box.getBoundingClientRect();
  const left = Math.min(Math.max(GAP, rect.left + rect.width / 2 - size.width / 2), innerWidth - size.width - GAP);
  const below = rect.bottom + GAP + size.height < innerHeight;
  box.style.left = `${left}px`;
  box.style.top = `${below ? rect.bottom + GAP : rect.top - size.height - GAP}px`;
}

function hide() {
  clearTimeout(timer);
  current = null;
  box?.remove();
}

function show(target) {
  if (target === current) return;
  hide();
  current = target;
  if (target.title) {  // keep the text for screen readers, without the second browser tooltip
    target.dataset.tip = target.title;
    if (!target.hasAttribute("aria-label")) target.setAttribute("aria-label", target.title);
    target.removeAttribute("title");
  }
  timer = setTimeout(() => {
    box = document.createElement("div");
    box.className = "tip";
    box.setAttribute("role", "tooltip");
    box.textContent = target.dataset.tip;
    (target.closest("dialog") || document.body).append(box);
    place(target);
  }, DELAY);
}

const find = (node) => node.closest?.("[title], [data-tip]");

export function installTooltips() {
  document.addEventListener("pointerover", (event) => {
    if (event.pointerType === "touch") return;
    const target = find(event.target);
    if (target) show(target); else hide();
  });
  document.addEventListener("focusin", (event) => {
    const target = find(event.target);
    if (target && event.target.matches(":focus-visible")) show(target);
  });
  document.addEventListener("focusout", hide);
  for (const name of ["pointerdown", "keydown", "hashchange"]) addEventListener(name, hide, true);
  addEventListener("scroll", hide, { capture: true, passive: true });
}
