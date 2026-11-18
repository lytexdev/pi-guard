// The first step into a linked household: install the worker for /fed/<id>/, then open it.
const base = location.pathname.match(/^\/fed\/\d+\//)?.[0];
const note = document.querySelector("[data-fed-boot]");
const say = (text) => { if (note) note.textContent = text; };

async function start() {
  if (!base || !("serviceWorker" in navigator) || !window.isSecureContext) {
    say("Dieser Browser kann den Haushalt nicht öffnen (HTTPS und Service Worker nötig). / This browser cannot open the household (needs HTTPS and service workers).");
    return;
  }
  say("Verbinde … / Connecting …");
  const registration = await navigator.serviceWorker.register(`${base}fed-sw.js`, { scope: base });
  const worker = registration.installing || registration.waiting || registration.active;
  if (worker && worker.state !== "activated") {
    await new Promise((done) => worker.addEventListener("statechange", () => { if (worker.state === "activated") done(); }));
  }
  location.replace(`${base}app/`);
}

start().catch(() => say("Verbindung fehlgeschlagen. / Could not connect."));
