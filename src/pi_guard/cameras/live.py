"""Live picture and sound from the capture pipeline for the dashboard.

FFmpeg writes a multipart MJPEG stream (`-f mpjpeg`), where every frame comes
with a Content-Length header, so frames are read exactly instead of being
searched for. Viewers wait for the next frame on a condition; nothing queues
up if a viewer is slow, they simply skip frames.

Live audio is MP3, which decoders can join at any frame, so chunks are fanned
out to listeners as they arrive.
"""

from __future__ import annotations

import asyncio
import contextlib
import os

HEADER_LIMIT = 16


class LiveFrames:
    def __init__(self) -> None:
        self.frame: bytes | None = None
        self.number = 0
        self._changed = asyncio.Condition()

    async def publish(self, frame: bytes) -> None:
        async with self._changed:
            self.frame = frame
            self.number += 1
            self._changed.notify_all()

    async def next(self, after: int, wait: float = 10.0) -> tuple[int, bytes]:
        async with self._changed:
            await asyncio.wait_for(
                self._changed.wait_for(lambda: self.number > after and self.frame is not None),
                wait,
            )
            assert self.frame is not None
            return self.number, self.frame


class AudioFanout:
    def __init__(self) -> None:
        self._listeners: set[asyncio.Queue[bytes]] = set()

    @property
    def listeners(self) -> int:
        return len(self._listeners)

    def subscribe(self) -> asyncio.Queue[bytes]:
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=64)
        self._listeners.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[bytes]) -> None:
        self._listeners.discard(queue)

    def publish(self, chunk: bytes) -> None:
        for queue in self._listeners:
            if queue.full():
                queue.get_nowait()  # a slow listener loses old audio, not new
            queue.put_nowait(chunk)


async def open_reader(fd: int) -> asyncio.StreamReader:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=8 * 1024 * 1024)
    await loop.connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), os.fdopen(fd, "rb", buffering=0)
    )
    return reader


async def read_mpjpeg(reader: asyncio.StreamReader, frames: LiveFrames) -> None:
    """Parse FFmpeg's mpjpeg output until EOF."""
    while True:
        length = None
        for _ in range(HEADER_LIMIT):
            line = await reader.readline()
            if not line:
                return
            text = line.strip().lower()
            if text.startswith(b"content-length:"):
                length = int(text.split(b":", 1)[1])
            elif not text and length is not None:
                break
        if length is None:
            continue
        frame = await reader.readexactly(length)
        await frames.publish(frame)


async def read_chunks(reader: asyncio.StreamReader, fanout: AudioFanout) -> None:
    with contextlib.suppress(asyncio.IncompleteReadError):
        while chunk := await reader.read(4096):
            fanout.publish(chunk)
