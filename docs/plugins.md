# Writing plugins

A plugin adds chat commands, dashboard API routes and background work.

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

Either as a Python package with an entry point:

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
