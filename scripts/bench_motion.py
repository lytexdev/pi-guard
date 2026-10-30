"""Measure motion-detection cost per frame on this machine.

    python scripts/bench_motion.py [width] [frames]

Used to decide whether a native (Rust) implementation is worth it; see
docs/performance.md for results.
"""

from __future__ import annotations

import sys
import time
from zoneinfo import ZoneInfo

import numpy as np

from pi_guard.config.files import CameraConfig
from pi_guard.detection.motion import MotionDetector


def main() -> None:
    width = int(sys.argv[1]) if len(sys.argv) > 1 else 320
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 2000
    height = width * 9 // 16
    camera = CameraConfig.model_validate(
        {
            "id": "bench",
            "name": "Bench",
            "device": "/dev/null",
            "motion": {"analysis_width": width},
        }
    )
    detector = MotionDetector(camera, 50, lambda change: None, ZoneInfo("UTC"))
    rng = np.random.default_rng(0)
    frames = [rng.integers(80, 100, (height, width), dtype=np.uint8) for _ in range(50)]
    for i in range(50):
        frames[i][40:100, i * 4 % (width - 40) : i * 4 % (width - 40) + 40] = 220
    started = time.perf_counter()
    for i in range(count):
        detector.process(frames[i % len(frames)], i / 4)
    elapsed = time.perf_counter() - started
    per_frame = elapsed / count * 1000
    print(
        f"{width}x{height}: {per_frame:.3f} ms/frame, "
        f"{per_frame * 4 / 10:.2f}% of one core at 4 FPS"
    )


if __name__ == "__main__":
    main()
