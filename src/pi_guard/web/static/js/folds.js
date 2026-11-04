// Fold panels in the settings by their headline. Remembered in this browser, also across redraws.
const KEY = "folds";
const HEADS = ".panel > h2:first-child, .panel > .row.between:first-child > h2";

const load = () => JSON.parse(localStorage.getItem(KEY) || "{}");
const remember = (name, folded) => localStorage.setItem(KEY, JSON.stringify({ ...load(), [name]: folded }));

function apply(root) {
  const saved = load();
  root.querySelectorAll(HEADS).forEach((head) => {
    const panel = head.closest(".panel");
    const name = head.textContent.trim();
    const folded = saved[name] ?? panel.dataset.foldDefault === "closed";
    panel.classList.add("foldable");
    panel.classList.toggle("folded", folded);
    head.tabIndex = 0;
    head.setAttribute("role", "button");
    head.setAttribute("aria-expanded", String(!folded));
  });
}

export function installFolds(root) {
  const toggle = (event) => {
    const head = event.target.closest(HEADS);
    if (!head) return;
    event.preventDefault();
    const folded = head.closest(".panel").classList.toggle("folded");
    head.setAttribute("aria-expanded", String(!folded));
    remember(head.textContent.trim(), folded);
  };
  root.addEventListener("click", toggle);
  root.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") toggle(event); });
  apply(root);
  let waiting = false;
  new MutationObserver(() => {
    if (waiting) return;
    waiting = true;
    requestAnimationFrame(() => { waiting = false; apply(root); });
  }).observe(root, { childList: true, subtree: true });
}

export function unfold(element) {
  element.closest(".panel")?.classList.remove("folded");
}
