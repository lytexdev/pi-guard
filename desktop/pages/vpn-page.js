const de = navigator.language.startsWith("de");
const T = de ? {
  on: "Verbunden", off: "Nicht verbunden", connecting: "Verbinde …", connect: "Verbinden", disconnect: "Trennen", back: "Zum Dashboard",
  onText: "Dein Verkehr läuft verschlüsselt über dein Zuhause.", offText: "Verbinde dich, um von überall dein Zuhause zu erreichen.",
  blocked: "Lockdown ist an: Ohne VPN geht nichts ins Internet.", quantum: "Quantenresistent: Schlüssel zuletzt erneuert um {time}.", classic: "Noch ohne quantenresistenten Schlüssel.",
  missing: "Für das VPN braucht dieser Computer einen kleinen Dienst. Führe das einmal aus:", unsupported: "Das VPN gibt es in der Desktop-App bisher nur für Linux.",
  kill: "Kill Switch: Fällt das VPN aus, geht nichts am Tunnel vorbei", lock: "Lockdown: Ohne VPN geht nie etwas ins Internet, auch nach dem Neustart", auto: "Beim Start automatisch verbinden", lan: "Geräte im lokalen Netz bleiben erreichbar", pq: "Quantenresistenter Tunnel (Rosenpass)",
  splitTitle: "Am VPN vorbei (Split-Tunneling)", splitText: "Dieses Programm startet außerhalb des VPN, alles andere bleibt drin.", splitGo: "Starten", login: "Melde dich zuerst im Dashboard an.",
  way: { ipv4: "direkt über IPv4", ipv6: "direkt über IPv6", relay: "über deinen Mietserver" },
} : {
  on: "Connected", off: "Not connected", connecting: "Connecting…", connect: "Connect", disconnect: "Disconnect", back: "To the dashboard",
  onText: "Your traffic goes encrypted through your home.", offText: "Connect to reach your home from anywhere.",
  blocked: "Lockdown is on: nothing reaches the internet without the VPN.", quantum: "Quantum-resistant: key last renewed at {time}.", classic: "No quantum-resistant key yet.",
  missing: "The VPN needs a small service on this computer. Run this once:", unsupported: "In the desktop app, the VPN is only available on Linux so far.",
  kill: "Kill switch: if the VPN fails, nothing goes past the tunnel", lock: "Lockdown: nothing ever reaches the internet without the VPN, also after a restart", auto: "Connect automatically at start", lan: "Devices in the local network stay reachable", pq: "Quantum-resistant tunnel (Rosenpass)",
  splitTitle: "Past the VPN (split tunneling)", splitText: "This program starts outside the VPN, everything else stays inside.", splitGo: "Start", login: "Log in to the dashboard first.",
  way: { ipv4: "directly over IPv4", ipv6: "directly over IPv6", relay: "through your rented server" },
};
const $ = (id) => document.getElementById(id);
const OPTIONS = ["kill_switch", "lockdown", "auto_connect", "allow_lan", "quantum"];
Object.entries({ l_kill: T.kill, l_lock: T.lock, l_auto: T.auto, l_lan: T.lan, l_pq: T.pq, "split-title": T.splitTitle, "split-text": T.splitText, "split-go": T.splitGo, back: T.back }).forEach(([id, text]) => { $(id).textContent = text; });

let status = null;

function show(s) {
  status = s;
  const on = s.state === "on";
  $("dot").className = `state ${s.state}`;
  $("title").textContent = T[s.state] || T.off;
  const lines = [on ? T.onText : s.options.lockdown && s.state === "off" ? T.blocked : T.offText];
  if (on && s.way) lines.push(T.way[s.way] || "");
  if (on) lines.push(s.quantum.active ? T.quantum.replace("{time}", new Date(s.quantum.last * 1000).toLocaleTimeString()) : T.classic);
  $("detail").textContent = lines.filter(Boolean).join(" ");
  $("toggle").textContent = s.state === "off" ? T.connect : T.disconnect;
  $("problem").textContent = s.error || "";
  OPTIONS.forEach((id) => { $(id).checked = Boolean(s.options[id]); });
}

async function refresh() {
  const s = await window.piguard.vpn("status");
  if (s.missing || s.unsupported) {
    $("title").textContent = T.off;
    $("toggle").classList.add("hidden");
    $("missing").classList.remove("hidden");
    $("missing-text").textContent = s.unsupported ? T.unsupported : T.missing;
    if (s.unsupported) document.querySelector("#missing code").classList.add("hidden");
    return;
  }
  $("settings").classList.remove("hidden");
  show(s);
}

$("toggle").addEventListener("click", async () => {
  $("toggle").disabled = true;
  try {
    const off = status?.state === "off";
    if (off) await window.piguard.vpn("link");
    show(await window.piguard.vpn(off ? "connect" : "disconnect"));
  } catch (error) {
    $("problem").textContent = String(error.message).includes("login") ? T.login : error.message;
  }
  $("toggle").disabled = false;
});
OPTIONS.forEach((id) => $(id).addEventListener("change", async () => {
  const options = Object.fromEntries(OPTIONS.map((key) => [key, $(key).checked]));
  show(await window.piguard.vpn("options", options));
}));
$("split").addEventListener("submit", (event) => {
  event.preventDefault();
  window.piguard.vpn("split", $("program").value);
  $("program").value = "";
});
$("back").addEventListener("click", () => window.piguard.retry());
refresh();
setInterval(refresh, 2000);
