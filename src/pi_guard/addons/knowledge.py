"""Files the assistant may draw on: checked, turned into plain text, stored in small passages.

Only the few passages that match a question are sent along, not whole files, which keeps
token use low. Everything lives in `knowledge.db`, readable by the service user alone.
Answers to repeated questions are kept here too, until files change.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from pi_guard.core.timeutil import utcnow
from pi_guard.security.files import secure_file

MAX_FILE_BYTES = 10_000_000
MAX_FILE_CHARS = 2_000_000
PASSAGE_CHARS = 800
PASSAGES_PER_QUESTION = 3
SUFFIXES = {".txt", ".md", ".json", ".pdf"}
_STOP_WORDS = (
    "der die das den dem des ein eine einer einem eines und oder aber ist sind war wie wo was "
    "wer wann warum ich du mir mich mein meine meiner meinem meinen wir uns unser in im an am "
    "auf aus bei mit nach von vor zu zur zum für über unter suche such finde finden nenne nenn "
    "sag sage bitte kannst können kann mal doch the a an of to in on for is are was what where "
    "who when how my me i you find search tell please"
)
STOP_WORDS = frozenset(_STOP_WORDS.split())


class KnowledgeError(Exception):
    """`key` is a translation key."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


def keywords(question: str) -> list[str]:
    words = re.findall(r"[^\W_]{2,}", question.casefold())
    return [w for w in dict.fromkeys(words) if w not in STOP_WORDS][:8]


def _flatten(value: Any, path: str = "") -> list[str]:
    """JSON as "a.b: value" lines, which stay readable when cut into passages."""
    if isinstance(value, dict):
        return [line for k, v in value.items() for line in _flatten(v, f"{path}.{k}".strip("."))]
    if isinstance(value, list):
        return [line for i, v in enumerate(value) for line in _flatten(v, f"{path}[{i}]")]
    return [f"{path}: {value}"] if path else [str(value)]


def extract_text(name: str, data: bytes) -> str:
    """Plain text of a file, or KnowledgeError if it is not what it claims to be."""
    suffix = Path(name).suffix.casefold()
    if suffix not in SUFFIXES:
        raise KnowledgeError("knowledge.unsupported")
    if len(data) > MAX_FILE_BYTES:
        raise KnowledgeError("knowledge.too_large")
    if suffix == ".pdf":
        return _pdf_text(data)
    if b"\0" in data[:4096]:
        raise KnowledgeError("knowledge.invalid")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    if suffix == ".json":
        try:
            return "\n".join(_flatten(json.loads(text)))
        except ValueError:
            raise KnowledgeError("knowledge.bad_json") from None
    return text


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    if not data.startswith(b"%PDF"):
        raise KnowledgeError("knowledge.invalid")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise KnowledgeError("knowledge.encrypted")
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    except (PyPdfError, ValueError, OSError, KeyError):
        raise KnowledgeError("knowledge.invalid") from None


def passages(text: str) -> list[str]:
    """Normalised text in pieces that end at paragraph or sentence borders where possible."""
    text = re.sub(r"[ \t\r\f\v]+", " ", text[:MAX_FILE_CHARS])
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    result: list[str] = []
    current = ""
    for block in re.split(r"(?<=[.!?])\s+|\n\n", text):
        block = block.strip()
        while len(block) > PASSAGE_CHARS:
            result.append(block[:PASSAGE_CHARS])
            block = block[PASSAGE_CHARS:]
        if current and len(current) + len(block) + 1 > PASSAGE_CHARS:
            result.append(current)
            current = ""
        current = f"{current} {block}".strip()
    if current:
        result.append(current)
    return [p for p in result if p]


class Knowledge:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        secure_file(path)
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE,
                size INTEGER NOT NULL, chunks INTEGER NOT NULL, created TEXT NOT NULL);
            CREATE VIRTUAL TABLE IF NOT EXISTS passages USING fts5(text, file_id UNINDEXED);
            CREATE TABLE IF NOT EXISTS answers (
                key TEXT PRIMARY KEY, answer TEXT NOT NULL, sources TEXT NOT NULL,
                hits INTEGER NOT NULL, created TEXT NOT NULL);
        """)

    def add(self, name: str, data: bytes) -> dict[str, Any]:
        text = extract_text(name, data)
        pieces = passages(text)
        if not pieces:
            raise KnowledgeError("knowledge.empty")
        digest = hashlib.sha256(data).hexdigest()
        if self._db.execute("SELECT 1 FROM files WHERE sha256 = ?", (digest,)).fetchone():
            raise KnowledgeError("knowledge.duplicate")
        cursor = self._db.execute(
            "INSERT INTO files (name, sha256, size, chunks, created) VALUES (?, ?, ?, ?, ?)",
            (Path(name).name[:120], digest, len(data), len(pieces), utcnow().isoformat()),
        )
        self._db.executemany("INSERT INTO passages (text, file_id) VALUES (?, ?)",
                             [(p, cursor.lastrowid) for p in pieces])  # fmt: skip
        self.forget_answers()
        return {"id": cursor.lastrowid, "name": name, "passages": len(pieces), "chars": len(text)}

    def files(self) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT id, name, size, chunks AS passages, created FROM files ORDER BY created DESC"
        )
        return [dict(r) for r in rows]

    def delete(self, file_id: int) -> bool:
        self._db.execute("DELETE FROM passages WHERE file_id = ?", (file_id,))
        deleted = self._db.execute("DELETE FROM files WHERE id = ?", (file_id,)).rowcount > 0
        self.forget_answers()
        return deleted

    def search(self, question: str, limit: int = PASSAGES_PER_QUESTION) -> list[dict[str, str]]:
        words = keywords(question)
        if not words:
            return []
        query = " OR ".join('"' + w.replace('"', "") + '"*' for w in words)
        rows = self._db.execute(
            "SELECT passages.text, files.name FROM passages "
            "JOIN files ON files.id = passages.file_id "
            "WHERE passages MATCH ? ORDER BY bm25(passages) LIMIT ?", (query, limit),
        ).fetchall()  # fmt: skip
        return [{"source": r["name"], "text": r["text"]} for r in rows]

    # Answers to frequent questions.

    def cached(self, key: str) -> tuple[str, list[str]] | None:
        row = self._db.execute("SELECT * FROM answers WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        self._db.execute("UPDATE answers SET hits = hits + 1 WHERE key = ?", (key,))
        return row["answer"], json.loads(row["sources"])

    def remember(self, key: str, answer: str, sources: list[str]) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO answers (key, answer, sources, hits, created) "
            "VALUES (?, ?, ?, 0, ?)", (key, answer, json.dumps(sources), utcnow().isoformat()),
        )  # fmt: skip

    def forget_answers(self) -> int:
        return self._db.execute("DELETE FROM answers").rowcount

    @property
    def answers_stored(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM answers").fetchone()[0])
