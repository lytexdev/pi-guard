// pi-guard Training: a page on this computer. The key comes from the address the tool printed.
const key = new URLSearchParams(location.hash.slice(1)).get("key") || "";
const $ = (id) => document.getElementById(id);
const VERDICT = { known: "erkannt", uncertain: "unsicher", unknown: "unbekannt" };
const PROBLEM = { "faces.too_small": "zu klein", "faces.unclear": "undeutlich", "faces.turned": "zu stark gedreht", "faces.blurry": "unscharf" };

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { "X-Key": key, ...(options.headers || {}) } });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
    else node.setAttribute(name, value);
  }
  node.append(...children.flat().filter((c) => c != null));
  return node;
}

const percent = (value) => (value == null ? "–" : `${Math.round(value * 1000) / 10} %`);

async function drawPeople() {
  const people = await api("/api/people");
  $("people").replaceChildren(...people.map((p) => el("div", { class: "person" },
    el("strong", {}, `${p.name} (${p.photos.length})`),
    el("div", { class: "grid" }, p.photos.map((photo) => el("figure", {},
      el("img", { src: `/api/people/${encodeURIComponent(p.name)}/${encodeURIComponent(photo)}?key=${encodeURIComponent(key)}`, alt: "" }),
      el("button", { onclick: async () => { await api(`/api/people/${encodeURIComponent(p.name)}/${encodeURIComponent(photo)}`, { method: "DELETE" }); drawPeople(); } }, "Entfernen")))))));
  $("search-name").replaceChildren(...people.map((p) => el("option", { value: p.name }, p.name)));
}

$("add").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData();
  for (const file of $("photos").files) form.append("photos", file, file.name);
  $("add-result").textContent = "Wird geprüft …";
  try {
    const r = await api(`/api/people/${encodeURIComponent($("name").value.trim())}`, { method: "POST", body: form });
    $("add-result").textContent = `${r.added} hinzugefügt.` + (r.refused.length ? ` Nicht genommen: ${r.refused.join("; ")}` : "");
    $("photos").value = "";
    drawPeople();
  } catch (error) { $("add-result").textContent = error.message; }
});

$("check").addEventListener("change", async () => {
  const file = $("check").files[0];
  if (!file) return;
  const out = $("check-result");
  out.textContent = "Wird geprüft …";
  const form = new FormData();
  form.append("photo", file, file.name);
  try {
    const r = await api("/api/whois", { method: "POST", body: form });
    const picture = el("div", { class: "picture" }, el("img", { src: URL.createObjectURL(file), alt: "" }));
    const image = picture.querySelector("img");
    image.addEventListener("load", () => r.faces.forEach((face, i) => {
      const [x, y, w, h] = face.box;
      const box = el("span", { class: "box" }, String(i + 1));
      Object.assign(box.style, { left: `${(x / image.naturalWidth) * 100}%`, top: `${(y / image.naturalHeight) * 100}%`,
        width: `${(w / image.naturalWidth) * 100}%`, height: `${(h / image.naturalHeight) * 100}%` });
      picture.append(box);
    }));
    out.replaceChildren(picture, r.faces.length ? el("ol", {}, r.faces.map((face) => el("li", {},
      face.problem ? `nicht zu bewerten: ${PROBLEM[face.problem] || face.problem}`
        : face.ranked.length ? `${VERDICT[face.verdict]}: ${face.ranked.map(([n, s]) => `${n} ${s.toFixed(2)}`).join(", ")}`
          : "Noch keine Personen angelegt."))) : el("p", {}, "Kein Gesicht gefunden."));
  } catch (error) { out.textContent = error.message; }
});

$("search").addEventListener("submit", async (event) => {
  event.preventDefault();
  const out = $("search-result");
  out.textContent = "Sucht … (große Ordner dauern)";
  try {
    const r = await api("/api/search", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: $("search-name").value, folder: $("folder").value }) });
    out.replaceChildren(...(r.hits.length ? r.hits.map((hit) => el("figure", {},
      el("img", { src: `/api/thumb?path=${encodeURIComponent(hit.path)}&key=${encodeURIComponent(key)}`, alt: "", loading: "lazy" }),
      el("figcaption", {}, `${hit.score.toFixed(2)} · ${VERDICT[hit.verdict]}`))) : [el("p", {}, "Nichts gefunden.")]));
  } catch (error) { out.textContent = error.message; }
});

$("calibrate").addEventListener("click", async () => {
  const out = $("calibrate-result");
  out.textContent = "Rechnet …";
  try {
    const r = await api("/api/calibrate");
    const row = (label, x) => x ? el("tr", {}, el("th", {}, label), el("td", {}, `${x.threshold} ± ${x.margin}`),
      el("td", {}, percent(x.family_known)), el("td", {}, percent(x.family_uncertain)), el("td", {}, percent(x.stranger_taken_for_family))) : null;
    out.replaceChildren(
      el("table", {}, el("tr", {}, el("th"), el("th", {}, "Schwelle"), el("th", {}, "Familie erkannt"), el("th", {}, "Familie unsicher"), el("th", {}, "Fremde als Familie")),
        row("jetzt", r.now), row("vorgeschlagen", r.suggested)),
      r.suggested ? el("pre", {}, `faces:\n  match_threshold: ${r.suggested.threshold}\n  uncertain_margin: ${r.suggested.margin}`)
        : el("p", {}, "Zu wenige Fotos verschiedener Personen für einen Vorschlag."),
      el("p", { class: "muted" }, `Grundlage: ${r.now.pairs.family} Vergleiche derselben Person, ${r.now.pairs.stranger} mit anderen.`));
  } catch (error) { out.textContent = error.message; }
});

if (!key) document.body.prepend(el("p", { class: "warn" }, "Bitte die Adresse mit #key=… öffnen, die pgtrain ui ausgegeben hat."));
else drawPeople().catch((error) => { $("people").textContent = error.message; });
