const de = navigator.language.startsWith("de");
const $ = (id) => document.getElementById(id);
$("title").textContent = de ? "pi-guard ist nicht erreichbar" : "pi-guard cannot be reached";
$("text").textContent = de ? "Bist du im Heimnetz oder im VPN? Wenn sich das Zertifikat geändert hat, verbinde dich neu." : "Are you at home or in the VPN? If the certificate changed, connect again.";
$("retry").textContent = de ? "Noch einmal versuchen" : "Try again";
$("change").textContent = de ? "Adresse ändern" : "Change address";
window.piguard.address().then((address) => { $("where").textContent = address; });
$("retry").addEventListener("click", () => window.piguard.retry());
$("change").addEventListener("click", () => window.piguard.change());
