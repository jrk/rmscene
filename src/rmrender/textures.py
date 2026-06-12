"""Procedural stipple textures for pencil-family pens.

The device renders pencil, mechanical pencil and paintbrush as *pure
black* pixels at varying density (binary dithering, e-ink heritage) --
measured from official PNG exports where all ink pixels are exactly 0.
We reproduce this by stamping small disk sprites whose alpha is a random
stipple at a chosen coverage level. Sprites are procedural (no assets
copied from GPL/AGPL projects).
"""

import numpy as np
import skia

N_LEVELS = 16
TILE = 64  # sprite size in px; stamps are scaled to the nib width


class StippleBank:
    """Disk-shaped stipple sprites at N_LEVELS coverage levels.

    `grain` is the size of noise clumps in sprite pixels; the official
    pencil grain is coarser than single pixels.
    """

    def __init__(
        self,
        n_levels: int = N_LEVELS,
        tile: int = TILE,
        seed: int = 7,
        grain: int = 2,
    ):
        rng = np.random.default_rng(seed)
        yy, xx = np.mgrid[0:tile, 0:tile]
        r2 = (xx - tile / 2 + 0.5) ** 2 + (yy - tile / 2 + 0.5) ** 2
        disk = r2 <= (tile / 2) ** 2
        # Soften the rim so stamps don't leave hard circular edges.
        rim = np.clip((tile / 2 - np.sqrt(r2)) / (tile * 0.15), 0, 1)
        self.images: list[skia.Image] = []
        n = -(-tile // grain)
        for k in range(n_levels):
            coverage = k / (n_levels - 1)
            noise = rng.random((n, n))
            if grain > 1:
                noise = np.kron(noise, np.ones((grain, grain)))[:tile, :tile]
            grain_mask = noise < coverage * rim
            rgba = np.zeros((tile, tile, 4), np.uint8)
            rgba[..., 3] = np.where(grain_mask & disk, 255, 0)
            self.images.append(
                skia.Image.fromarray(rgba, skia.ColorType.kRGBA_8888_ColorType)
            )

    def get(self, coverage: float) -> skia.Image:
        k = round(np.clip(coverage, 0, 1) * (len(self.images) - 1))
        return self.images[k]


_bank: StippleBank | None = None


def stipple_bank() -> StippleBank:
    global _bank
    if _bank is None:
        _bank = StippleBank()
    return _bank
