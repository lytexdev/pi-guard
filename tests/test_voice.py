"""Talking to the Pi: wake phrases, the speech model download, answers and typed speech."""

from __future__ import annotations

import hashlib
import io
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from pi_guard.audio import voice
from pi_guard.web.settings_schema import BY_KEY, to_env

from .test_web import _client, _login


def test_made_up_words_become_words_the_model_knows() -> None:
    vocabulary = {"hallo", "lei", "tex", "hi", "pi", "guard"}
    found = voice.wake_phrases(["Hallo lytex", "hi piguard", "hey xyzzy"], vocabulary.__contains__)
    assert found == ["hallo lei tex", "hi pi guard"]


def _zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _serve(monkeypatch: pytest.MonkeyPatch, data: bytes) -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self) -> Any:
            yield data

    class Stream:
        def __enter__(self) -> Response:
            return Response()

        def __exit__(self, *_: Any) -> None:
            return None

    monkeypatch.setattr(voice.httpx, "stream", lambda *_, **__: Stream())


def test_the_speech_model_is_checked_and_stays_in_its_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    good = _zip({"vosk-model-small-de-0.15/am/final.mdl": b"model"})
    spec = voice.SPEECH_MODELS["de"]
    monkeypatch.setitem(voice.SPEECH_MODELS, "de", voice.SpeechModel(
        "de", spec.folder, spec.url, hashlib.sha256(good).hexdigest(), 1.0))  # fmt: skip
    _serve(monkeypatch, good)
    path = voice.download_model("de", tmp_path)
    assert (path / "am" / "final.mdl").read_bytes() == b"model"

    _serve(monkeypatch, good + b"changed")
    with pytest.raises(voice.SpeechModelError):
        voice.download_model("de", tmp_path / "other")
    assert not (tmp_path / "other" / spec.folder).exists()

    evil = _zip({"../../escaped.txt": b"x"})
    monkeypatch.setitem(voice.SPEECH_MODELS, "de", voice.SpeechModel(
        "de", spec.folder, spec.url, hashlib.sha256(evil).hexdigest(), 1.0))  # fmt: skip
    _serve(monkeypatch, evil)
    with pytest.raises(voice.SpeechModelError):
        voice.download_model("de", tmp_path / "evil")
    assert not (tmp_path / "escaped.txt").exists()


class FakeAssistant:
    def __init__(self) -> None:
        self.asked: list[tuple[str, bool]] = []

    async def ask(self, question: str, lang: str, conversation: str, private: bool = False) -> Any:
        self.asked.append((question, private))
        return SimpleNamespace(text="Es ist sonnig.")

    async def speak(self, text: str) -> Path | None:
        return None


async def test_a_spoken_question_gets_an_answer_but_no_documents(tmp_path: Path) -> None:
    played: list[Path] = []
    assistant = FakeAssistant()
    audit: list[tuple[Any, ...]] = []
    audio = SimpleNamespace(playback_enabled=False, sounds=SimpleNamespace(path=lambda _: None))
    app = SimpleNamespace(
        settings=SimpleNamespace(language="de", voice_wake_enabled=True, voice_button_gpio=0,
                                 voice_wake_phrases=["hallo lytex"], data_dir=tmp_path),
        addons=SimpleNamespace(assistant=assistant), audio=audio, cameras={},
        db=SimpleNamespace(audit=lambda *a: audit.append(a)),
    )  # fmt: skip
    talk = voice.Voice(app)  # type: ignore[arg-type]

    async def hear() -> str:
        return "wie wird das wetter"

    talk._hear = hear  # type: ignore[method-assign]
    await talk.converse("wake")
    assert assistant.asked == [("wie wird das wetter", False)]  # not private: no documents
    assert talk.last and talk.last["answer"] == "Es ist sonnig."
    assert audit == [("voice:wake", "voice.question")]  # never the words
    assert played == []


def test_a_list_setting_cannot_start_another_line() -> None:
    with pytest.raises(ValueError):
        to_env(BY_KEY["voice_wake_phrases"], ["hallo\nPI_GUARD_DASHBOARD_AUTH=none"])
    assert to_env(BY_KEY["voice_wake_phrases"], ["hallo lytex", " hi piguard "]) == (
        "hallo lytex,hi piguard"
    )


async def test_typed_words_are_spoken_at_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, app = await _client(tmp_path)
    headers = {"X-CSRF-Token": await _login(client)}
    played: list[tuple[Path, bool]] = []

    async def play(path: Path, **kwargs: Any) -> None:
        played.append((path, path.exists()))

    @asynccontextmanager
    async def spoken(_: Any, text: str, lang: str) -> Any:
        path = tmp_path / "speech.wav"
        path.write_bytes(text.encode())
        yield path

    app.audio = SimpleNamespace(playback_enabled=True, play=play)
    monkeypatch.setattr(voice, "spoken", spoken)
    try:
        response = await client.post("/api/say", json={"text": "  Bin gleich da  "},
                                      headers=headers)  # fmt: skip
        assert response.status == 200
        assert played == [(tmp_path / "speech.wav", True)]
        assert (tmp_path / "speech.wav").read_text() == "Bin gleich da"
        empty = await client.post("/api/say", json={"text": " "}, headers=headers)
        assert empty.status == 400
        status = await (await client.get("/api/voice", headers=headers)).json()
        assert status["enabled"] is False and status["state"] == "off"
        listen = await client.post("/api/voice/listen", headers=headers)
        assert listen.status == 409
    finally:
        await client.close()
        app.db.close()
