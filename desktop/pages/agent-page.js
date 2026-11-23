// Connect this computer to pi-guard with the agent: for the Server add-on (status, upkeep,
// shutdown) and Computer-Connect (media, files, ring, text). Linux only.
const de = navigator.language.startsWith("de");
const T = de ? {
  on: "Dieser Computer ist verbunden", installed: "Agent installiert, aber nicht aktiv", off: "Noch nicht verbunden",
  onText: "Agent {version}. Im Dashboard unter Add-ons › Server und Computer-Connect.", offText: "Installiert den signierten Agenten von deinem Pi und richtet ihn ein. Danach siehst du diesen Computer im Dashboard.",
  name: "Name im Dashboard", hConnect: "Computer-Connect", tConnect: "Was pi-guard in deiner Sitzung ({user}) tun darf:",
  media: "Musik und Lautstärke steuern", files: "Dateien empfangen (in ~/Downloads/pi-guard)", ring: "Klingeln lassen, um ihn zu finden", text: "Text empfangen (Meldung, Zwischenablage)",
  hServer: "Server", power: "Herunterfahren, Neustart und Standby (mit doppelter Bestätigung)", docker: "Docker-Container steuern",
  admin: "Das System fragt gleich nach deinem Passwort: Der Agent läuft als Dienst. Dein Dashboard-Konto muss Admin sein.",
  install: "Installieren und verbinden", again: "Neu einrichten", remove: "Agent entfernen", back: "Zum Dashboard", working: "Einen Moment …",
  done: "Fertig: Agent {version} läuft.", cancelled: "Abgebrochen, es wurde nichts installiert.", removed: "Der Agent ist entfernt.",
  noAgent: "Dein Pi hat noch keinen Agenten für diesen Computer. Er holt ihn mit dem nächsten App-Release, oder du baust ihn selbst:",
  unsupported: "Den Agenten gibt es für Linux. Auf diesem System bitte nicht über die App einrichten.",
  taken: "Diesen Namen gibt es im Dashboard schon. Wähle einen anderen oder lösche den alten Eintrag unter Add-ons › Server.",
} : {
  on: "This computer is connected", installed: "Agent installed but not running", off: "Not connected yet",
  onText: "Agent {version}. In the dashboard under Add-ons › Server and Computer-Connect.", offText: "Installs the signed agent from your Pi and sets it up. Then this computer shows in the dashboard.",
  name: "Name in the dashboard", hConnect: "Computer-Connect", tConnect: "What pi-guard may do in your session ({user}):",
  media: "Control music and volume", files: "Receive files (into ~/Downloads/pi-guard)", ring: "Ring it to find it", text: "Receive text (notification, clipboard)",
  hServer: "Server", power: "Shut down, restart and sleep (with two confirmations)", docker: "Control Docker containers",
  admin: "The system asks for your password next: the agent runs as a service. Your dashboard account must be an admin.",
  install: "Install and connect", again: "Set up again", remove: "Remove the agent", back: "To the dashboard", working: "One moment…",
  done: "Done: agent {version} is running.", cancelled: "Cancelled, nothing was installed.", removed: "The agent is removed.",
  noAgent: "Your Pi has no agent for this computer yet. It fetches one with the next app release, or build it yourself:",
  unsupported: "The agent is for Linux. Please do not set it up through the app on this system.",
  taken: "This name exists in the dashboard already. Choose another or delete the old entry under Add-ons › Server.",
};
const $ = (id) => document.getElementById(id);
const fill = (text, values = {}) => text.replace(/\{(\w+)\}/g, (_, k) => values[k] ?? "");
const PARTS = ["media", "files", "ring", "text"];
Object.entries({ l_name: T.name, h_connect: T.hConnect, l_media: T.media, l_files: T.files, l_ring: T.ring, l_text: T.text,
  h_server: T.hServer, l_power: T.power, l_docker: T.docker, t_admin: T.admin, remove: T.remove, back: T.back }).forEach(([id, text]) => { $(id).textContent = text; });

let status = null;

function show(s, message = "") {
  status = s;
  $("problem").textContent = "";
  if (s.unsupported) {
    $("dot").className = "state";
    $("title").textContent = T.off;
    $("detail").textContent = T.unsupported;
    ["choose", "go", "remove"].forEach((id) => $(id).classList.add("hidden"));
    return;
  }
  const on = Boolean(s.version && s.active);
  $("dot").className = `state ${on ? "on" : s.version ? "connecting" : ""}`;
  $("title").textContent = on ? T.on : s.version ? T.installed : T.off;
  $("detail").textContent = message || (on ? fill(T.onText, s) : T.offText);
  $("t_connect").textContent = fill(T.tConnect, s);
  if (!$("name").value) $("name").value = s.name || "";
  $("choose").classList.toggle("hidden", on);
  $("go").classList.remove("hidden");
  $("go").textContent = on ? T.again : T.install;
  $("remove").classList.toggle("hidden", !s.version);
}

async function refresh(message) {
  try { show(await window.piguard.agent("status"), message); } catch (error) { $("problem").textContent = error.message; }
}

function busy(on) {
  ["go", "remove"].forEach((id) => { $(id).disabled = on; });
  if (on) $("detail").textContent = T.working;
}

$("go").addEventListener("click", async () => {
  if (status?.version && status.active && $("choose").classList.contains("hidden")) { $("choose").classList.remove("hidden"); $("go").textContent = T.install; return; }
  busy(true);
  try {
    const result = await window.piguard.agent("install", {
      name: $("name").value, power: $("power").checked, docker: $("docker").checked,
      connect: PARTS.filter((id) => $(id).checked),
    });
    $("manual").classList.toggle("hidden", !result.noAgent);
    if (result.noAgent) { $("t_manual").textContent = T.noAgent; busy(false); return refresh(); }
    await refresh(result.cancelled ? T.cancelled : fill(T.done, result));
  } catch (error) {
    $("problem").textContent = /ag_name_taken/.test(error.message) ? T.taken : error.message.replace(/^Error invoking remote method '\w+': (Error: )?/, "");
  } finally {
    busy(false);
  }
});

$("remove").addEventListener("click", async () => {
  busy(true);
  try {
    const result = await window.piguard.agent("remove");
    await refresh(result.cancelled ? T.cancelled : T.removed);
  } catch (error) {
    $("problem").textContent = error.message;
  } finally {
    busy(false);
  }
});

$("back").addEventListener("click", () => window.piguard.retry());
refresh();
