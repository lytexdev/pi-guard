from __future__ import annotations

import numpy as np
import numpy.typing as npt

from pi_guard.config.files import Zone

Mask = npt.NDArray[np.uint8]


def build_mask(zones: list[Zone], width: int, height: int) -> Mask:
    """255 where motion counts. No include zones means the whole frame."""
    import cv2

    def polygon(zone: Zone) -> npt.NDArray[np.int32]:
        return np.array(
            [(round(x * (width - 1)), round(y * (height - 1))) for x, y in zone.points],
            dtype=np.int32,
        )

    includes = [polygon(z) for z in zones if z.kind == "include"]
    excludes = [polygon(z) for z in zones if z.kind == "exclude"]
    if includes:
        mask = np.zeros((height, width), dtype=np.uint8)
        cv2.fillPoly(mask, includes, 255)
    else:
        mask = np.full((height, width), 255, dtype=np.uint8)
    if excludes:
        cv2.fillPoly(mask, excludes, 0)
    return mask
