"""Search in connected document services for the assistant.

Paperless-ngx has a full-text index, so its search is used directly. Nextcloud finds files
by name; matching text files and PDFs are read and searched here. Only short excerpts around
the search words are passed on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from pi_guard.addons.knowledge import KnowledgeError, extract_text, keywords

log = logging.getLogger(__name__)

TIMEOUT = 15
RESULTS = 3
EXCERPT_CHARS = 700
MAX_DOWNLOAD = 5_000_000


def excerpt(text: str, words: list[str]) -> str:
    """The part of a text around the first search word it contains."""
    lowered = text.casefold()
    hits = [lowered.find(w) for w in words if w in lowered]
    start = max(0, min(hits) - EXCERPT_CHARS // 3) if hits else 0
    return " ".join(text[start : start + EXCERPT_CHARS].split())


@dataclass(frozen=True)
class Paperless:
    url: str
    token: str

    async def search(self, question: str) -> list[dict[str, str]]:
        words = keywords(question)
        if not words:
            return []
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(
                f"{self.url.rstrip('/')}/api/documents/",
                params={"query": " ".join(words), "page_size": RESULTS},
                headers={"Authorization": f"Token {self.token}", "Accept": "application/json"},
            )
        response.raise_for_status()
        return [
            {"source": f"Paperless: {d.get('title', '?')}",
             "text": excerpt(str(d.get("content") or ""), words)}
            for d in response.json().get("results", [])[:RESULTS]
        ]  # fmt: skip


@dataclass(frozen=True)
class Nextcloud:
    url: str
    user: str
    password: str

    async def search(self, question: str) -> list[dict[str, str]]:
        words = keywords(question)
        if not words:
            return []
        base = self.url.rstrip("/")
        found: list[dict[str, str]] = []
        async with httpx.AsyncClient(timeout=TIMEOUT, auth=(self.user, self.password)) as client:
            for word in words[:3]:
                response = await client.get(
                    f"{base}/ocs/v2.php/search/providers/files/search",
                    params={"term": word, "limit": RESULTS, "format": "json"},
                    headers={"OCS-APIRequest": "true"},
                )
                response.raise_for_status()
                entries = response.json().get("ocs", {}).get("data", {}).get("entries", [])
                for entry in entries:
                    item = await self._read(client, base, entry, words)
                    if item:
                        found.append(item)
                if len(found) >= RESULTS:
                    break
        return found[:RESULTS]

    async def _read(
        self, client: httpx.AsyncClient, base: str, entry: dict[str, object], words: list[str]
    ) -> dict[str, str] | None:
        attributes = entry.get("attributes")
        path = attributes.get("path") if isinstance(attributes, dict) else None
        title = str(entry.get("title") or "")
        if not path:
            return {"source": f"Nextcloud: {title}", "text": str(entry.get("subline") or "")}
        response = await client.get(
            f"{base}/remote.php/dav/files/{self.user}/{str(path).lstrip('/')}"
        )
        if response.status_code != 200 or len(response.content) > MAX_DOWNLOAD:
            return None
        try:
            text = extract_text(title, response.content)
        except KnowledgeError:
            return None
        return {"source": f"Nextcloud: {title}", "text": excerpt(text, words)}
