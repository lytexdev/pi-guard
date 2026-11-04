# Writing plugins

A plugin adds chat commands, dashboard pages, API routes, background work and reactions to
events. Start with:

```bash
pi-guard plugin new my_addon      # folder with a working example
pi-guard plugin check my_addon    # checks the manifest and the code
pi-guard plugin pack my_addon     # ZIP for the dashboard
```

## plugin.json

`plugins.json` works as well.

```json
{
  "id": "doorbell",
  "name": "Doorbell",
  "version": "1.0.0",
  "api": 1,
  "entry": "plugin.py",
  "icon": "bell",
  "authors": ["Your Name"],
  "source": "https://github.com/you/pi-guard-doorbell",
  "license": "MIT",
  "description": "Rings the speaker",
  "permissions": ["audio"],
  "settings": [
    {"key": "volume", "label": "Volume", "type": "number", "default": 40},
    {"key": "token", "label": "API token", "type": "secret"}
  ]
}
```

`id` and `name` are required. `icon` is a name from the dashboard's icon set, `settings` become
a form on the add-on's page and arrive in `self.settings`, with types `text`, `number`, `bool`
and `secret`. `permissions` are shown to the person installing it. They are a declaration, not
a barrier: a plugin can do everything pi-guard can.

## The Plugin class

| Override | Purpose |
| --- | --- |
| `commands()` | Chat commands, for every messenger |
| `routes()` | API below `/api/plugins/<id>/`, after login or with an API token |
| `run()` | Background work; restarted if it fails |
| `events()`, `on_event(event)` | React to events such as `motion_detected` |
| `panel()` | Widgets on the add-on's page: `text`, `stat`, `list`, `button` |
| `action(name)` | A panel button was pressed |

| Helper | Purpose |
| --- | --- |
| `self.app` | Cameras, audio, database, everything else |
| `self.settings` | The plugin's settings |
| `self.load(key)`, `self.save(key, value)` | Values that survive restarts |
| `await self.notify(text)` | Message to all admins |
| `self.log(text)` | Audit log entry |
| `self.reply(text, ("Label", "command"))` | Answer with buttons |

Panels are data, not HTML, on purpose: a plugin cannot put scripts into the dashboard.

## Example

```python
from aiohttp import web
from pi_guard.addons.plugin import Plugin


class Doorbell(Plugin):
    id = "doorbell"
    title = "Doorbell"
    description = "Rings the speaker"

    def commands(self):
        return [self.command("ring", self.ring, aliases=("klingel",))]

    def routes(self):
        return [web.get("/state", self.state)]   # /api/plugins/doorbell/state

    async def run(self):                         # optional background work
        ...

    async def ring(self, ctx, args):
        await self.app.audio.play(self.app.audio.sounds.path("doorbell"))
        return self.reply("Ding dong", ("Again", "ring"))

    async def state(self, request):
        return web.json_response({"ready": True})
```

Chat commands work in every messenger; buttons turn into a numbered menu where needed.
`self.command(...)` is for admins unless you pass `admin_only=False`. Dashboard routes are
only reachable after login. `self.app` gives access to cameras, audio, events and the database,
and `self.settings` holds your section of the configuration.

## Installing

In the dashboard under **Add-ons › Own add-ons**: upload a ZIP, or create one and edit its code
there. The ZIP needs a `plugin.json`:

```json
{"id": "doorbell", "name": "Doorbell", "version": "1.0", "entry": "plugin.py",
 "author": "You", "description": "Rings the speaker"}
```

The dashboard asks for your password first, because this installs program code. Paths in the
ZIP are checked before anything is written. A public Git repository with a `plugin.json` at its top can be
installed by link. There is no public add-on directory yet; the search covers what is installed.

Or as a Python package with an entry point:

```toml
[project.entry-points."pi_guard.plugins"]
doorbell = "my_package:Doorbell"
```

or as a file in `/etc/pi-guard/plugins/`, which must belong to root and be writable by nobody
else; other files are ignored.

Then switch it on in the configuration and restart:

```yaml
addons:
  plugins:
    doorbell:
      enabled: true
      volume: 40
```

A plugin that fails to load is skipped and logged; pi-guard keeps running. **Plugins run with
the rights of pi-guard and can see your cameras and data. Only install code you trust.**
