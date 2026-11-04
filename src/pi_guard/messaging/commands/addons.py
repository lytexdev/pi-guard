"""Chat commands of the add-ons. They work the same in every messenger: buttons become a
numbered menu where a platform has no buttons."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pi_guard.addons.assistant import AssistantError
from pi_guard.addons.remote import ConfirmationError
from pi_guard.addons.smarthome import SmartHomeError
from pi_guard.core.permissions import Role
from pi_guard.messaging.base import Button, Reply
from pi_guard.messaging.commands.context import CommandContext
from pi_guard.messaging.commands.core import menu_row
from pi_guard.messaging.commands.registry import Command, Registry, TextArg
from pi_guard.network.pihole import PiholeError

MAX_BUTTONS = 12


def _off(ctx: CommandContext, name: str) -> Reply:
    return Reply(ctx.t("addons.off", name=ctx.t(f"addons.{name}")), buttons=(menu_row(ctx),))


# --- heartbeat ---------------------------------------------------------------------------


async def services(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    watcher = ctx.app.addons.heartbeat
    if watcher is None:
        return _off(ctx, "heartbeat")
    rows = watcher.overview()
    if not rows:
        return Reply(ctx.t("heartbeat.none"), buttons=(menu_row(ctx),))
    lines = [ctx.t("heartbeat.title")]
    for row in rows:
        if row["up"] is None:
            lines.append(ctx.t("heartbeat.waiting", name=row["name"]))
        elif row["up"]:
            lines.append(ctx.t("heartbeat.up", name=row["name"], ms=row["latency_ms"] or 0))
        else:
            lines.append(ctx.t("heartbeat.down", name=row["name"], reason=row["reason"] or "?"))
    return Reply("\n".join(lines), buttons=((ctx.button("button.refresh", "services"),),
                                            menu_row(ctx)))  # fmt: skip


# --- network analysis --------------------------------------------------------------------


def known_names(app: Any) -> dict[str, str]:
    """Names the dashboard knows for addresses: VPN devices of the WireGuard add-on."""
    store = getattr(app.dashboard, "wg_peers", None)
    return {p["address"]: p["name"] for p in store.list()} if store else {}


async def netstats(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    analytics = ctx.app.addons.analytics
    if analytics is None:
        return _off(ctx, "analytics")
    ip = args.get("text", "").strip()
    try:
        if ip:
            data = await analytics.device(ip)
            lines = [ctx.t("analytics.device_title", ip=ip, queries=data["queries"])]
            lines += [f"• {d['domain']}: {d['count']}" for d in data["top_domains"][:10]]
            if data["top_blocked"]:
                lines += ["", ctx.t("analytics.blocked")]
                lines += [f"• {d['domain']}: {d['count']}" for d in data["top_blocked"][:5]]
            return Reply("\n".join(lines), buttons=((ctx.button("button.back", "netstats"),),
                                                    menu_row(ctx)))  # fmt: skip
        data = await analytics.overview(known_names(ctx.app))
    except PiholeError:
        return Reply(ctx.t("analytics.unavailable"), buttons=(menu_row(ctx),))
    except ValueError:
        return Reply(ctx.t("analytics.bad_ip"), buttons=(menu_row(ctx),))
    summary = data["summary"]
    lines = [ctx.t("analytics.summary", queries=summary["queries"],
                   percent=summary["percent_blocked"], clients=summary["clients"]), "",
             ctx.t("analytics.top_domains")]  # fmt: skip
    lines += [f"• {d['domain']}: {d['count']}" for d in data["top_domains"][:8]]
    rows = [
        (Button(f"{d['name'] or d['ip']} ({d['count']})", f"netstats {d['ip']}"),)
        for d in data["devices"][:6]
    ]
    return Reply("\n".join(lines), buttons=(*rows, menu_row(ctx)))


# --- smart home --------------------------------------------------------------------------


def _smart_line(ctx: CommandContext, row: dict[str, Any]) -> str:
    if row["kind"] == "sensor":
        value = f"{row['value']} {row['unit']}".strip() if row["value"] is not None else "–"
    elif row["on"] is None:
        value = ctx.t("smarthome.unknown")
    else:
        value = ctx.t("smarthome.on" if row["on"] else "smarthome.off")
    return f"• {row['name']}: {value}"


async def smarthome(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    home = ctx.app.addons.smarthome
    if home is None:
        return _off(ctx, "smarthome")
    words = args.get("text", "").split()
    if words:
        wanted = {"an": True, "ein": True, "on": True, "aus": False, "off": False}.get(
            words[-1].casefold()
        )
        name = " ".join(words[:-1] if words[-1].casefold() in
                        ("an", "ein", "on", "aus", "off", "toggle") else words)  # fmt: skip
        item = home.find(name)
        if item is None:
            return Reply(ctx.t("smarthome.not_found", name=name), buttons=(menu_row(ctx),))
        try:
            await home.switch(item.device.id, wanted)
        except SmartHomeError as exc:
            return Reply(ctx.t(exc.key), buttons=(menu_row(ctx),))
        ctx.app.db.audit(ctx.user.key, "smarthome.switch", item.device.id)
        return Reply(ctx.t("smarthome.sent", name=item.device.name), buttons=(
            (ctx.button("smarthome.list_button", "smarthome"),), menu_row(ctx)))  # fmt: skip
    rows = home.overview()
    if not rows:
        return Reply(ctx.t("smarthome.none"), buttons=(menu_row(ctx),))
    text = "\n".join([ctx.t("smarthome.title"), *(_smart_line(ctx, r) for r in rows[:30])])
    buttons = [
        (Button(ctx.t("smarthome.toggle", name=r["name"]), f"smarthome {r['id']} toggle"),)
        for r in rows if r["switchable"]
    ][:MAX_BUTTONS]  # fmt: skip
    if not home.connected:
        text += "\n\n" + ctx.t("smarthome.offline")
    return Reply(text, buttons=(*buttons, menu_row(ctx)))


# --- remote actions ----------------------------------------------------------------------


async def action(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    """Two confirmations with changing button positions, for the same person, within a minute."""
    remote = ctx.app.addons.remote
    if remote is None:
        return _off(ctx, "remote")
    if ctx.is_group:
        return Reply(ctx.t("remote.private_only"))
    words = args.get("text", "").split()
    if not words:
        if not remote.actions:
            return Reply(ctx.t("remote.none"), buttons=(menu_row(ctx),))
        rows = [(Button(a.name, f"action start {a.id}"),) for a in remote.actions.values()]
        return Reply(ctx.t("remote.title"), buttons=(*rows, menu_row(ctx)))
    step, value = words[0], words[1] if len(words) > 1 else ""
    try:
        if step == "start":
            token = remote.begin(value, ctx.user.key)
            name = remote.actions[value].name
            return Reply(ctx.t("remote.confirm_1", name=name), buttons=(
                (Button(ctx.t("remote.yes_1"), f"action confirm {token}"),),
                (ctx.button("remote.no", f"action cancel {token}"),),
            ))  # fmt: skip
        if step == "cancel":
            remote.cancel(value)
            return Reply(ctx.t("remote.cancelled"), buttons=(menu_row(ctx),))
        if step == "confirm":
            target, following = remote.confirm(value, ctx.user.key)
            if following is not None:
                # The safe answer moves to where "yes" was, against a hasty second tap.
                question = ctx.t("remote.confirm_2", name=target.name, host=target.host)
                yes = ctx.t("remote.yes_2", name=target.name)
                return Reply(question, buttons=(
                    (ctx.button("remote.no", f"action cancel {following}"),),
                    (Button(yes, f"action confirm {following}"),),
                ))  # fmt: skip
            ctx.app.db.audit(ctx.user.key, "remote.run", target.id)
            await ctx.progress(ctx.t("remote.running", name=target.name))
            result = await remote.run(target)
            key = "remote.done" if result.ok else "remote.failed"
            ctx.app.db.audit(ctx.user.key, "remote.result", f"{target.id}: {result.exit_code}")
            return Reply(ctx.t(key, name=target.name, output=result.output or "–"),
                         buttons=(menu_row(ctx),))  # fmt: skip
    except ConfirmationError as exc:
        return Reply(ctx.t(exc.key), buttons=(menu_row(ctx),))
    return Reply(ctx.t("remote.unknown"), buttons=(menu_row(ctx),))


# --- assistant ---------------------------------------------------------------------------


def _outcome(ctx: CommandContext, outcome: Any) -> Reply:
    buttons: tuple[tuple[Button, ...], ...] = ()
    if outcome.pending:
        buttons = ((Button(ctx.t("cmd.yes"), f"proposal yes {outcome.pending}"),),
                   (Button(ctx.t("cmd.no"), f"proposal no {outcome.pending}"),))  # fmt: skip
    return Reply(outcome.text, photo=outcome.photo, video=outcome.video, buttons=buttons)


async def confirm(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    """Answer to "Soll ich ...?"."""
    commander = ctx.app.addons.commander
    choice, _, token = args.get("text", "").partition(" ")
    if commander is None or choice not in ("yes", "no"):
        return Reply(ctx.t("cmd.expired"), buttons=(menu_row(ctx),))

    async def deliver(path: str) -> None:
        await ctx.send(Reply(ctx.t("cmd.video_ready"), video=Path(path)))

    chat = {"provider": ctx.user.provider, "chat": ctx.chat_id}
    outcome = await commander.answer(token, choice == "yes", ctx.lang, ctx.user.key, deliver, chat)
    return _outcome(ctx, outcome)


async def answer(ctx: CommandContext, question: str) -> Reply:
    assistant = ctx.app.addons.assistant
    if assistant is None:
        return _off(ctx, "assistant")
    commander = ctx.app.addons.commander
    if commander is not None and ctx.user.role is Role.ADMIN and not ctx.is_group:
        try:
            outcome = await commander.propose(question, ctx.lang, ctx.user.key)
        except AssistantError:
            outcome = None
        if outcome is not None:
            return _outcome(ctx, outcome)
    try:
        private = ctx.user.role is Role.ADMIN and not ctx.is_group
        result = await assistant.ask(question, ctx.lang, ctx.chat_key, private)
    except AssistantError as exc:
        return Reply(ctx.t(exc.key), buttons=(menu_row(ctx),))
    if result.documents:
        return Reply(f"{result.text}\n\n📄 {', '.join(result.documents)}")
    return Reply(result.text)


async def ask(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    question = args.get("text", "").strip()
    if not question:
        return Reply(ctx.t("assistant.ask_what"), expect="ask")
    return await answer(ctx, question)


async def weather(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    place = args.get("text", "").strip()
    return await answer(ctx, f"wetter in {place}" if place else "wetter")


def more_rows(ctx: CommandContext) -> list[tuple[Button, ...]]:
    """Entries for the "More" menu."""
    addons = ctx.app.addons
    entries = [
        (addons.heartbeat, "more.services", "services"),
        (addons.analytics, "more.netstats", "netstats"),
        (addons.smarthome, "more.smarthome", "smarthome"),
        (addons.remote, "more.actions", "action"),
    ]
    buttons = [ctx.button(key, command) for addon, key, command in entries if addon is not None]
    return [tuple(buttons[i : i + 2]) for i in range(0, len(buttons), 2)]


def register(registry: Registry) -> None:
    text = (TextArg("text"),)
    registry.add(Command("services", services, ("dienste", "heartbeat", "server"),
                         role=Role.ADMIN, help_key="help.services", menu=False))  # fmt: skip
    registry.add(Command("netstats", netstats, ("analyse", "netzwerk", "network"), text,
                         role=Role.ADMIN, help_key="help.netstats", menu=False))  # fmt: skip
    registry.add(Command("smarthome", smarthome, ("smart", "geraete", "geräte", "schalte"), text,
                         role=Role.ADMIN, help_key="help.smarthome", menu=False))  # fmt: skip
    registry.add(Command("action", action, ("aktion", "befehl", "remote"), text,
                         role=Role.ADMIN, help_key="help.action", menu=False))  # fmt: skip
    registry.add(Command("ask", ask, ("frage", "ki", "ai", "assistent"), text,
                         help_key="help.ask", menu=False))  # fmt: skip
    registry.add(Command("proposal", confirm, (), text, role=Role.ADMIN, menu=False))
    registry.add(Command("weather", weather, ("wetter",), text,
                         help_key="help.weather", menu=False))  # fmt: skip
