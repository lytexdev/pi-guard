const de = navigator.language.startsWith("de");
const $ = (id) => document.getElementById(id);
$("title").textContent = de ? "pi-guard ist nicht erreichbar" : "pi-guard cannot be reached";
$("text").textContent = de
  ? "Bist du im Heimnetz oder im VPN? Wenn sich das Zertifikat geändert hat, verbinde dich neu. Die App versucht es von selbst erneut."
  : "Are you at home or in the VPN? If the certificate changed, connect again. The app retries on its own.";
$("retry").textContent = de ? "Noch einmal versuchen" : "Try again";
$("change").textContent = de ? "Adresse ändern" : "Change address";
window.piguard.address().then((address) => { $("where").textContent = address; });

let wait = 2000;
let timer = null;
const MAX_WAIT = 30000;

function schedule() {
  clearTimeout(timer);
  timer = setTimeout(() => {
    wait = Math.min(wait * 1.5, MAX_WAIT);
    window.piguard.retry();
  }, wait);
}

$("retry").addEventListener("click", () => {
  clearTimeout(timer);
  wait = 2000;
  window.piguard.retry();
});
$("change").addEventListener("click", () => {
  clearTimeout(timer);
  window.piguard.change();
});
schedule();
