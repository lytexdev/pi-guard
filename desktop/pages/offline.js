const de = navigator.language.startsWith("de");
const $ = (id) => document.getElementById(id);
$("title").textContent = de ? "pi-guard ist nicht erreichbar" : "pi-guard cannot be reached";
$("text").textContent = de
  ? "Bist du im Heimnetz oder im VPN? Wenn sich das Zertifikat geändert hat, verbinde dich neu. Die App versucht es von selbst erneut."
  : "Are you at home or in the VPN? If the certificate changed, connect again. The app retries on its own.";
$("retry").textContent = de ? "Noch einmal versuchen" : "Try again";
$("change").textContent = de ? "Adresse ändern" : "Change address";
window.piguard.address().then((address) => { $("where").textContent = address; });

// Each failed retry loads this page again, so the growing wait is kept in sessionStorage:
// otherwise it would start at 2 s every time and knock every 2 s for good.
const FIRST_WAIT = 2000;
const MAX_WAIT = 30000;
const stored = (() => { try { return JSON.parse(sessionStorage.getItem("pg-wait") || "{}"); } catch { return {}; } })();
// A wait from an outage long ago does not count: a new outage starts quick again.
const recent = Date.now() - Number(stored.at || 0) < 2 * MAX_WAIT;
const wait = recent && stored.wait >= FIRST_WAIT ? Math.min(stored.wait, MAX_WAIT) : FIRST_WAIT;
let timer = null;
const remember = (value) => {
  try { sessionStorage.setItem("pg-wait", JSON.stringify({ wait: value, at: Date.now() })); } catch { /* storage off */ }
};

function schedule() {
  clearTimeout(timer);
  timer = setTimeout(() => {
    remember(Math.min(Math.round(wait * 1.5), MAX_WAIT));
    window.piguard.retry();
  }, wait);
}

$("retry").addEventListener("click", () => {
  clearTimeout(timer);
  remember(FIRST_WAIT);
  window.piguard.retry();
});
$("change").addEventListener("click", () => {
  clearTimeout(timer);
  window.piguard.change();
});
schedule();
