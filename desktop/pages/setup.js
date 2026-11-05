const TEXTS = {
  de: { title: "Mit pi-guard verbinden", intro: "Gib die Adresse deines pi-guard ein. Du findest sie im Browser, wenn du das Dashboard öffnest.", address: "Adresse", connect: "Verbinden",
    check: "Vertraust du diesem pi-guard? Das ist der Fingerabdruck seines Zertifikats. Wenn du sichergehen willst, vergleiche ihn im Dashboard unter Einstellungen, Dashboard.", trust: "Vertrauen", cancel: "Abbrechen",
    failed: "Unter dieser Adresse wurde kein pi-guard gefunden." },
  en: { title: "Connect to pi-guard", intro: "Enter the address of your pi-guard. You find it in the browser when you open the dashboard.", address: "Address", connect: "Connect",
    check: "Do you trust this pi-guard? This is the fingerprint of its certificate. To be sure, compare it in the dashboard under Settings, Dashboard.", trust: "Trust", cancel: "Cancel",
    failed: "No pi-guard found at this address." },
};
const texts = navigator.language.startsWith("de") ? TEXTS.de : TEXTS.en;
document.documentElement.lang = navigator.language.slice(0, 2);
document.querySelectorAll("[data-t]").forEach((el) => { el.textContent = texts[el.dataset.t]; });

const $ = (id) => document.getElementById(id);
window.piguard.address().then((known) => { $("address").value = known; });
$("find").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("problem").textContent = "";
  try {
    const found = await window.piguard.look($("address").value);
    $("print").textContent = found.fingerprint;
    $("find").classList.add("hidden");
    $("confirm").classList.remove("hidden");
  } catch {
    $("problem").textContent = texts.failed;
  }
});
$("trust").addEventListener("click", () => window.piguard.trust());
$("back").addEventListener("click", () => {
  $("confirm").classList.add("hidden");
  $("find").classList.remove("hidden");
});
