"""The page people see instead of a blocked website.

Blocked domains can point to a second address of the Pi, whose ports 80 and 443 are redirected
here by `scripts/blockpage.sh`, so Pi-hole keeps its own ports. For HTTPS a certificate for the
requested name is issued on the spot by pi-guard's local certificate authority. Browsers only
trust it on devices where that authority is installed; everywhere else they show their own
warning, which no software on the network side can avoid.
"""

from __future__ import annotations

import html
import logging
import re
import ssl
import subprocess
import tempfile
from pathlib import Path

from aiohttp import web

log = logging.getLogger(__name__)

HTTP_PORT = 8088
HTTPS_PORT = 8444
_NAME_RE = re.compile(r"^[a-z0-9.-]{1,253}$")
PAGE = """<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#121418;
color:#e7e9ec;font:17px/1.5 system-ui,sans-serif;text-align:center;padding:24px}}
main{{max-width:30rem}}h1{{font-size:1.6rem;margin:.6em 0 .3em}}p{{color:#98a1ad;margin:.4em 0}}
code{{color:#5cb3ff;word-break:break-all}}svg{{width:84px;height:84px}}</style></head><body><main>
<svg viewBox="0 0 24 24" fill="none" stroke="#5cb3ff" stroke-width="1.6" stroke-linecap="round"
stroke-linejoin="round"><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/>
<path d="M9 9l6 6M15 9l-6 6"/></svg><h1>{title}</h1><p><code>{domain}</code></p><p>{text}</p>
<p><small>Pi-Guard</small></p></main></body></html>"""
TEXTS = {
    "de": ("Diese Seite ist gesperrt", "Sie wurde in diesem Netzwerk gesperrt. "
           "Wenn das ein Fehler ist, frag bitte die Person, die das Netzwerk verwaltet."),
    "en": ("This site is blocked", "It was blocked in this network. "
           "If that is a mistake, please ask the person who manages the network."),
}  # fmt: skip


def render(host: str, lang: str) -> str:
    title, text = TEXTS.get(lang, TEXTS["en"])
    name = host.split(":")[0][:120]
    return PAGE.format(lang=lang, title=title, text=text, domain=html.escape(name))


class BlockPage:
    def __init__(self, tls_dir: Path, language: str) -> None:
        self._dir = tls_dir
        self._language = language
        self._certs = tls_dir / "blockpage"
        self._contexts: dict[str, ssl.SSLContext] = {}

    async def _page(self, request: web.Request) -> web.Response:
        return web.Response(text=render(request.host, self._language), content_type="text/html",
                            status=403, headers={"Cache-Control": "no-store"})  # fmt: skip

    def _context_for(self, name: str) -> ssl.SSLContext:
        if name in self._contexts:
            return self._contexts[name]
        self._certs.mkdir(parents=True, exist_ok=True)
        cert, key = self._certs / f"{name}.crt", self._certs / f"{name}.key"
        if not cert.exists():
            self._issue(name, cert, key)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        if len(self._contexts) > 200:
            self._contexts.clear()
        self._contexts[name] = context
        return context

    def _issue(self, name: str, cert: Path, key: Path) -> None:
        ca, ca_key = self._dir / "ca.crt", self._dir / "ca.key"
        with tempfile.TemporaryDirectory() as work:
            request = Path(work) / "r.csr"
            extra = Path(work) / "x.cnf"
            extra.write_text(f"subjectAltName=DNS:{name}\nextendedKeyUsage=serverAuth\n")
            subprocess.run(
                ["openssl", "req", "-new", "-newkey", "ec", "-pkeyopt",
                 "ec_paramgen_curve:prime256v1", "-nodes", "-keyout", str(key), "-out",
                 str(request), "-subj", f"/CN={name}"],
                check=True, capture_output=True,
            )  # fmt: skip
            subprocess.run(
                ["openssl", "x509", "-req", "-in", str(request), "-CA", str(ca), "-CAkey",
                 str(ca_key), "-CAcreateserial", "-days", "90", "-out", str(cert), "-extfile",
                 str(extra)], check=True, capture_output=True)  # fmt: skip
        key.chmod(0o600)

    def _sni(self, sock: ssl.SSLObject, name: str | None, context: ssl.SSLContext) -> None:
        wanted = (name or "").casefold()
        if not _NAME_RE.match(wanted):
            return
        try:
            sock.context = self._context_for(wanted)
        except (OSError, subprocess.CalledProcessError, ssl.SSLError) as exc:
            log.warning("no block page certificate: %s", type(exc).__name__)

    async def run(self) -> None:
        import asyncio

        application = web.Application()
        application.router.add_route("*", "/{tail:.*}", self._page)
        runner = web.AppRunner(application, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", HTTP_PORT).start()  # noqa: S104 - see module docstring
        default = self._context_for("pi-guard.blocked")
        default.sni_callback = self._sni
        await web.TCPSite(runner, "0.0.0.0", HTTPS_PORT, ssl_context=default).start()  # noqa: S104
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()
