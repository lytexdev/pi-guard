"""A page on this computer for photos: who is this, similar photos, thresholds.

Bound to 127.0.0.1. A random key in the address (as Jupyter does) and a check of the Host
header keep other websites in the same browser out, also through DNS rebinding. Photos
added to a person are kept in PGTRAIN_HOME/people/<name>/, nowhere else; a photo to
check is judged in memory and not kept.
"""

from __future__ import annotations

import asyncio
import re
import secrets
import tempfile
import uuid
from importlib import resources
from pathlib import Path
from typing import Any

from aiohttp import web

from pgtrain import weights
from pgtrain.faces import IMAGE_SUFFIXES, FaceTool, images, leave_one_out, rates, recommend, who

NAME = re.compile(r"^[\w .'-]{1,64}$")
MAX_UPLOAD = 30 * 1024 * 1024
STATIC = Path(str(resources.files("pgtrain").joinpath("static")))


class Page:
    def __init__(self, root: Path, port: int) -> None:
        self.root, self.port = root, port
        self.people_dir = root / "people"
        self.people_dir.mkdir(parents=True, exist_ok=True)
        self.key = secrets.token_urlsafe(24)
        self.tool = FaceTool(weights.ensure_faces(root / "models"))
        self.lock = asyncio.Lock()  # one model run at a time
        self.allowed_folders: set[Path] = set()

    # --- guards ---

    @web.middleware
    async def guard(self, request: web.Request, handler: Any) -> web.StreamResponse:
        if request.host not in (f"127.0.0.1:{self.port}", f"localhost:{self.port}"):
            raise web.HTTPForbidden(text="wrong host")
        if request.path.startswith("/api/"):
            given = request.headers.get("X-Key") or request.query.get("key", "")
            if not secrets.compare_digest(given, self.key):
                raise web.HTTPForbidden(text="key missing")
        response = await handler(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' blob:; style-src 'self'; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def _person(self, name: str) -> Path:
        name = name.strip()
        if not NAME.match(name) or name in (".", ".."):
            raise web.HTTPBadRequest(text="invalid name")
        return self.people_dir / name

    async def _run(self, fn: Any, *args: Any) -> Any:
        async with self.lock:
            return await asyncio.to_thread(fn, *args)

    # --- people ---

    async def people(self, request: web.Request) -> web.Response:
        rows = [{"name": p.name, "photos": [f.name for f in images(p)]}
                for p in sorted(self.people_dir.iterdir()) if p.is_dir()]  # fmt: skip
        return web.json_response(rows)

    async def add_photos(self, request: web.Request) -> web.Response:
        folder = self._person(request.match_info["name"])
        folder.mkdir(exist_ok=True)
        added, refused = 0, []
        total = 0
        async for part in await request.multipart():
            filename = getattr(part, "filename", None) or ""
            suffix = Path(filename).suffix.lower()
            if suffix not in IMAGE_SUFFIXES:
                refused.append(filename)
                continue
            data = await part.read()  # type: ignore[union-attr]
            total += len(data)
            if total > MAX_UPLOAD:
                raise web.HTTPRequestEntityTooLarge(max_size=MAX_UPLOAD, actual_size=total)
            path = folder / f"{uuid.uuid4().hex[:12]}{suffix}"
            path.write_bytes(bytes(data))
            face = await self._run(self.tool.main_face, path)
            if face is None or face.embedding is None:
                path.unlink()
                refused.append(f"{filename}: {face.problem if face else 'no face'}")
                continue
            added += 1
        return web.json_response({"added": added, "refused": refused})

    async def delete_photo(self, request: web.Request) -> web.Response:
        folder = self._person(request.match_info["name"])
        photo = folder / Path(request.match_info["photo"]).name
        if not photo.is_file():
            raise web.HTTPNotFound()
        photo.unlink()
        if not any(folder.iterdir()):
            folder.rmdir()
        return web.json_response({"ok": True})

    async def photo(self, request: web.Request) -> web.FileResponse:
        folder = self._person(request.match_info["name"])
        photo = folder / Path(request.match_info["photo"]).name
        if not photo.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(photo)

    # --- checks ---

    async def whois(self, request: web.Request) -> web.Response:
        """Each face of an uploaded photo, ranked against the people here. Not kept."""
        reader = await request.multipart()
        part = await reader.next()
        if part is None:
            raise web.HTTPBadRequest(text="no photo")
        data = await part.read()  # type: ignore[union-attr]
        if len(data) > MAX_UPLOAD:
            raise web.HTTPRequestEntityTooLarge(max_size=MAX_UPLOAD, actual_size=len(data))
        with tempfile.TemporaryDirectory(prefix="pgtrain-check-") as work:
            path = Path(work) / "photo.jpg"
            path.write_bytes(bytes(data))
            known = await self._run(self.tool.people, self.people_dir)
            faces = await self._run(self.tool.faces, path)
        result = []
        for face in faces:
            ranked = who(face.embedding, known)[:3] if face.embedding is not None else []
            verdict = self.tool.verdict(ranked[0][1]) if ranked else None
            if not ranked and face.embedding is not None:
                verdict = "unknown"  # nobody added yet
            result.append({"box": face.box, "problem": face.problem, "verdict": verdict,
                           "ranked": [[n, round(s, 2)] for n, s in ranked]})  # fmt: skip
        return web.json_response({"faces": result})

    async def search(self, request: web.Request) -> web.Response:
        data = await request.json()
        folder = Path(str(data.get("folder", ""))).expanduser().resolve()
        person = self._person(str(data.get("name", "")))
        if not folder.is_dir():
            raise web.HTTPBadRequest(text="folder not found")
        if not person.is_dir():
            raise web.HTTPBadRequest(text="unknown person")
        self.allowed_folders.add(folder)

        def run() -> list[dict[str, Any]]:
            own = [f.embedding for p in images(person)
                   if (f := self.tool.main_face(p)) and f.embedding is not None]  # fmt: skip
            hits: list[dict[str, Any]] = []
            for photo in images(folder)[:2000]:
                scores = [who(f.embedding, {person.name: own})[0][1]
                          for f in self.tool.faces(photo) if f.embedding is not None]  # fmt: skip
                if scores:
                    best = max(scores)
                    hits.append({"path": str(photo), "score": round(best, 2),
                                 "verdict": self.tool.verdict(best)})  # fmt: skip
            return sorted(hits, key=lambda h: float(h["score"]), reverse=True)[:40]

        return web.json_response({"hits": await self._run(run)})

    async def thumb(self, request: web.Request) -> web.FileResponse:
        """Only pictures inside a folder that was searched."""
        path = Path(request.query.get("path", "")).resolve()
        if path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file():
            raise web.HTTPNotFound()
        if not any(path.is_relative_to(folder) for folder in self.allowed_folders):
            raise web.HTTPForbidden()
        return web.FileResponse(path)

    async def calibrate(self, request: web.Request) -> web.Response:
        known = await self._run(self.tool.people, self.people_dir)
        genuine, impostor = await asyncio.to_thread(leave_one_out, known)
        s = self.tool.settings
        best = recommend(genuine, impostor, s.uncertain_margin)
        return web.json_response({
            "people": {n: len(e) for n, e in known.items()},
            "now": rates(genuine, impostor, s.match_threshold, s.uncertain_margin),
            "suggested": rates(genuine, impostor, best, s.uncertain_margin) if best else None,
        })  # fmt: skip

    async def index(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(STATIC / "index.html")

    def app(self) -> web.Application:
        app = web.Application(middlewares=[self.guard], client_max_size=MAX_UPLOAD)
        app.router.add_get("/", self.index)
        app.router.add_static("/static/", STATIC)
        app.router.add_get("/api/people", self.people)
        app.router.add_post("/api/people/{name}", self.add_photos)
        app.router.add_get("/api/people/{name}/{photo}", self.photo)
        app.router.add_delete("/api/people/{name}/{photo}", self.delete_photo)
        app.router.add_post("/api/whois", self.whois)
        app.router.add_post("/api/search", self.search)
        app.router.add_get("/api/thumb", self.thumb)
        app.router.add_get("/api/calibrate", self.calibrate)
        return app


def serve(root: Path, port: int) -> None:
    page = Page(root, port)
    print(f"Open http://127.0.0.1:{port}/#key={page.key}", flush=True)
    print(f"Photos of people are kept in {page.people_dir}", flush=True)
    web.run_app(page.app(), host="127.0.0.1", port=port, access_log=None, print=None)
