"""Talking to the Pi: a wake phrase, a hardware button or the dashboard starts listening.

Everything that listens runs here, offline, with Vosk: the wake phrase is matched against a
small fixed grammar, and the question is turned into text on the Pi. Nothing is recorded or
stored; audio only passes through memory. It is off by default, a chime says when the Pi
listens for a question, and the dashboard shows the state.

Spoken requests only get answers. They never switch anything: anyone near the microphone can
speak, so changing the mode, the alarm or settings needs the dashboard, the app or a chat.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import itertools
import json
import logging
import shutil
import tempfile
import time
import zipfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from pi_guard.i18n import t

if TYPE_CHECKING:
    from pi_guard.app import App

log = logging.getLogger(__name__)

RATE = 16000
CHUNK = RATE // 4 * 2  # a quarter second of 16-bit mono
QUESTION_SECONDS = 10
MIN_PRESS_GAP = 2.0
# Made-up words are not in the model's vocabulary; these sound alike and are.
SOUNDS_LIKE = {
    "lytex": ("lei tex", "lai tex", "light tex", "lytex"),
    "piguard": ("pi guard", "pie guard", "piguard"),
    "pi-guard": ("pi guard", "pie guard"),
}


@dataclass(frozen=True)
class SpeechModel:
    language: str
    folder: str
    url: str
    sha256: str
    size_mb: float


SPEECH_MODELS = {
    "de": SpeechModel("de", "vosk-model-small-de-0.15",
                      "https://alphacephei.com/vosk/models/vosk-model-small-de-0.15.zip",
                      "b7e53c90b1f0a38456f4cd62b366ecd58803cd97cd42b06438e2c131713d5e43", 46.5),
    "en": SpeechModel("en", "vosk-model-small-en-us-0.15",
                      "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
                      "30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498", 41.2),
}  # fmt: skip


class SpeechModelError(Exception):
    pass


class VoiceError(Exception):
    """A key for the dashboard, such as voice.no_microphone."""


RETRY_SECONDS = 30


def model_dir(models: Path, language: str) -> Path:
    return models / SPEECH_MODELS.get(language, SPEECH_MODELS["en"]).folder


def download_model(language: str, models: Path, timeout: float = 600) -> Path:
    """Fetch, check and unpack one speech model. Nothing outside its folder is written."""
    spec = SPEECH_MODELS[language]
    models.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with tempfile.TemporaryDirectory(dir=models) as work:
        archive = Path(work) / "model.zip"
        with archive.open("wb") as out, httpx.stream("GET", spec.url, follow_redirects=True,
                                                     timeout=timeout) as response:  # fmt: skip
            response.raise_for_status()
            for chunk in response.iter_bytes():
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != spec.sha256:
            raise SpeechModelError(f"{spec.folder}: checksum mismatch, file discarded")
        unpacked = Path(work) / "unpacked"
        with zipfile.ZipFile(archive) as zipped:
            for member in zipped.infolist():
                target = (unpacked / member.filename).resolve()
                if not target.is_relative_to(unpacked.resolve()):
                    raise SpeechModelError(f"{member.filename}: outside the model folder")
            zipped.extractall(unpacked)
        source = unpacked / spec.folder
        if not source.is_dir():
            raise SpeechModelError(f"{spec.folder}: not found in the archive")
        final = models / spec.folder
        if final.exists():
            shutil.rmtree(final)
        source.rename(final)
    return final


@contextlib.asynccontextmanager
async def spoken(app: Any, text: str, lang: str) -> AsyncIterator[Path | None]:
    """Text as sound: the assistant's voice if it has one, else espeak-ng on the Pi. The file
    is gone when the block ends. None if neither is available."""
    assistant = getattr(getattr(app, "addons", None), "assistant", None)
    path = None
    with contextlib.suppress(Exception):
        path = await assistant.speak(text) if assistant is not None else None
    if path is not None:
        try:
            yield path
        finally:
            path.unlink(missing_ok=True)
        return
    if not shutil.which("espeak-ng"):
        yield None
        return
    with tempfile.TemporaryDirectory(prefix="pg-voice-") as work:
        wav = Path(work) / "speech.wav"
        process = await asyncio.create_subprocess_exec(
            "espeak-ng", "-v", lang[:2], "-w", str(wav), "--stdin",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )  # fmt: skip
        # On stdin, so a text starting with a dash is never read as an option.
        await process.communicate(text[:600].encode())
        yield wav if wav.is_file() else None


def wake_phrases(configured: list[str], known: Any) -> list[str]:
    """Every spelling of the configured phrases whose words the model knows."""
    found: list[str] = []
    for phrase in configured:
        options = [SOUNDS_LIKE.get(word, (word,)) for word in phrase.casefold().split()]
        for parts in itertools.product(*options):
            words = " ".join(parts).split()
            candidate = " ".join(words)
            if words and all(known(w) for w in words) and candidate not in found:
                found.append(candidate)
    return found


class Voice:
    def __init__(self, app: App) -> None:
        self._app = app
        self.state = "off"
        self.problem: str | None = None
        self.last: dict[str, Any] | None = None  # in memory only: gone after a restart
        self._wanted = asyncio.Event()
        self._busy = asyncio.Lock()
        self._model: Any = None
        self._phrases: list[str] = []
        self._source = ""
        self._pressed = float("-inf")

    @property
    def settings(self) -> Any:
        return self._app.settings

    @property
    def enabled(self) -> bool:
        return bool(self.settings.voice_wake_enabled or self.settings.voice_button_gpio)

    def status(self) -> dict[str, Any]:
        s = self.settings
        return {"state": self.state, "wake": s.voice_wake_enabled, "button": s.voice_button_gpio,
                "phrases": self._phrases, "problem": self.problem, "last": self.last,
                "model": model_dir(self._models(), s.language).is_dir()}  # fmt: skip

    def _models(self) -> Path:
        return Path(self.settings.data_dir) / "models"

    # --- starting --------------------------------------------------------------------------

    def request(self, source: str) -> bool:
        """The dashboard or the button: listen for one question now."""
        if self._busy.locked() or self._model is None:
            return False
        self._source = source
        self._wanted.set()
        return True

    async def run(self) -> None:
        if not self.enabled:
            return
        try:
            from vosk import KaldiRecognizer, Model, SetLogLevel
        except ImportError:
            self.problem = "voice.no_vosk"
            log.warning("voice needs the vosk package: pip install 'pi-guard[voice]'")
            return
        folder = model_dir(self._models(), self.settings.language)
        if not folder.is_dir():
            self.problem = "voice.no_model"
            log.warning("voice needs a speech model: pi-guard voice download")
            return
        SetLogLevel(-1)
        self._model = await asyncio.to_thread(Model, str(folder))
        self._recognizer = KaldiRecognizer
        self._phrases = wake_phrases(self.settings.voice_wake_phrases,
                                     lambda w: self._model.find_word(w) >= 0)  # fmt: skip
        if self.settings.voice_wake_enabled and not self._phrases:
            self.problem = "voice.no_phrase"
        tasks = [asyncio.create_task(self._loop())]
        if self.settings.voice_button_gpio:
            tasks.append(asyncio.create_task(self._button(self.settings.voice_button_gpio)))
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            self.state = "off"

    async def _loop(self) -> None:
        wake = bool(self.settings.voice_wake_enabled and self._phrases)
        while True:
            self.state = "waiting" if wake else "idle"
            try:
                if wake and not await self._wait_for_phrase():
                    # The microphone stopped: wait a little instead of spinning.
                    self.problem = "voice.microphone_failed"
                    await asyncio.sleep(RETRY_SECONDS)
                    continue
                if not wake:
                    await self._wanted.wait()
                self._wanted.clear()
                async with self._busy:
                    await self.converse(self._source or "wake")
                self.problem = None if self.problem == "voice.microphone_failed" else self.problem
            except VoiceError as exc:
                self.problem = str(exc)
                await asyncio.sleep(RETRY_SECONDS)
            finally:
                self._source = ""

    async def _wait_for_phrase(self) -> bool:
        """True once the phrase was heard or someone asked; False if the sound stopped."""
        grammar = json.dumps([*self._phrases, "[unk]"])
        recognizer = self._recognizer(self._model, RATE, grammar)
        async with self._audio() as chunks:
            async for chunk in chunks:
                if self._wanted.is_set():
                    return True
                if await asyncio.to_thread(recognizer.AcceptWaveform, chunk):
                    heard = json.loads(recognizer.Result()).get("text", "")
                    if heard in self._phrases:
                        self._source = "wake"
                        return True
        return False

    async def _button(self, line: int) -> None:
        """A push button between the GPIO line and ground, read with libgpiod's gpiomon."""
        command = ["gpiomon", "-e", "falling", "-b", "pull-up", "-c", "gpiochip0", str(line)]
        while True:
            process = await asyncio.create_subprocess_exec(
                *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                stdin=asyncio.subprocess.DEVNULL,
            )  # fmt: skip
            assert process.stdout is not None
            try:
                while await process.stdout.readline():
                    now = time.monotonic()
                    if now - self._pressed >= MIN_PRESS_GAP:  # contacts bounce
                        self._pressed = now
                        self.request("button")
            finally:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
            self.problem = "voice.button_failed"
            await asyncio.sleep(30)

    # --- one question ----------------------------------------------------------------------

    async def converse(self, source: str) -> None:
        app = self._app
        lang = self.settings.language
        self.state = "hearing"
        await self._chime()
        question = await self._hear()
        if not question:
            return
        self.state = "thinking"
        app.db.audit(f"voice:{source}", "voice.question")
        answer = await self._answer(question, lang)
        self.last = {"time": time.time(), "question": question, "answer": answer, "source": source}
        self.state = "speaking"
        with contextlib.suppress(Exception):
            await self._say(answer, lang)

    async def _hear(self) -> str:
        recognizer = self._recognizer(self._model, RATE)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + QUESTION_SECONDS
        async with self._audio() as chunks:
            async for chunk in chunks:
                if await asyncio.to_thread(recognizer.AcceptWaveform, chunk):
                    text = str(json.loads(recognizer.Result()).get("text", "")).strip()
                    if text:
                        return text
                if loop.time() > deadline:
                    break
        return str(json.loads(recognizer.FinalResult()).get("text", "")).strip()

    async def _answer(self, question: str, lang: str) -> str:
        from pi_guard.addons.assistant import AssistantError

        assistant = getattr(self._app.addons, "assistant", None)
        if assistant is None:
            return t("voice.no_assistant", lang)
        try:
            # Not private: whoever speaks here is not known, so no documents.
            return str((await assistant.ask(question, lang, "voice", private=False)).text)
        except AssistantError as exc:
            return t(exc.key, lang)

    async def _chime(self) -> None:
        audio = self._app.audio
        chime = audio.sounds.path("chime") if audio is not None else None
        if audio is not None and audio.playback_enabled and chime:
            with contextlib.suppress(Exception):
                await audio.play(chime, ignore_quiet_hours=True)

    async def _say(self, text: str, lang: str) -> None:
        audio = self._app.audio
        if audio is None or not audio.playback_enabled:
            return
        async with spoken(self._app, text, lang) as path:
            if path is not None:
                await audio.play(path, ignore_quiet_hours=True, speech=True)

    # --- audio -----------------------------------------------------------------------------

    @contextlib.asynccontextmanager
    async def _audio(self) -> AsyncIterator[AsyncIterator[bytes]]:
        """16 kHz mono PCM from the microphone. A camera that records sound shares its stream,
        so the microphone is not opened twice."""
        pipeline = next((p for p in self._app.cameras.values()
                         if p.microphone and p.segment_dir and p.live_fps > 0), None)  # fmt: skip
        audio = self._app.audio
        device = audio.settings.microphone if audio is not None else None
        pcm = ["-ac", "1", "-ar", str(RATE), "-f", "s16le", "pipe:1"]
        base = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
        if pipeline is not None:
            command = [*base, "-f", "mp3", "-i", "pipe:0", *pcm]
            stdin = asyncio.subprocess.PIPE
        elif device:
            command = [*base, "-nostdin", "-f", "alsa", "-i", device, *pcm]
            stdin = asyncio.subprocess.DEVNULL
        else:
            raise VoiceError("voice.no_microphone")
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=stdin,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        feeder: asyncio.Task[None] | None = None
        queue = pipeline.live_audio.subscribe() if pipeline is not None else None
        if queue is not None:
            pipe = process.stdin
            assert pipe is not None

            async def feed() -> None:
                while True:
                    pipe.write(await queue.get())
                    await pipe.drain()

            feeder = asyncio.create_task(feed())

        async def chunks() -> AsyncIterator[bytes]:
            assert process.stdout is not None
            while data := await process.stdout.read(CHUNK):
                yield data

        try:
            yield chunks()
        finally:
            if feeder is not None:
                feeder.cancel()
            if queue is not None and pipeline is not None:
                pipeline.live_audio.unsubscribe(queue)
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
