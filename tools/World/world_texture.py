"""Color-correct sampling for offline terrain and road texture bakes."""
from functools import lru_cache

import numpy as np
from PIL import Image


def srgb_to_linear(value):
    value = np.asarray(value, dtype=np.float32)
    return np.where(value <= 0.04045, value / 12.92, ((value + 0.055) / 1.055) ** 2.4)


def linear_to_rgb8(value):
    value = np.clip(value, 0, 1)
    encoded = np.where(value <= 0.0031308, value * 12.92, 1.055 * value ** (1 / 2.4) - 0.055)
    return np.rint(encoded * 255).astype(np.uint8)


def sample_bilinear(image, x, y, repeat=False):
    """Sample texel coordinates; integer coordinates are texel centers."""
    if not repeat:
        x = np.clip(x, 0, image.shape[1] - 1)
        y = np.clip(y, 0, image.shape[0] - 1)
    ix, iy = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = np.asarray(x - ix, dtype=np.float32), np.asarray(y - iy, dtype=np.float32)
    if image.ndim == 3:
        fx, fy = fx[..., None], fy[..., None]
    if repeat:
        x0, x1 = ix % image.shape[1], (ix + 1) % image.shape[1]
        y0, y1 = iy % image.shape[0], (iy + 1) % image.shape[0]
    else:
        x0, x1 = ix, np.minimum(ix + 1, image.shape[1] - 1)
        y0, y1 = iy, np.minimum(iy + 1, image.shape[0] - 1)
    return (image[y0, x0] * (1 - fx) + image[y0, x1] * fx) * (1 - fy) + (
        image[y1, x0] * (1 - fx) + image[y1, x1] * fx) * fy


@lru_cache(maxsize=48)
def linear_mip(path, stamp, level, srgb=True):
    # Cache bounded, filtered source maps across the terrain worker threads.
    with Image.open(path) as source:
        image = np.asarray(source.convert("RGB"), dtype=np.float32) / 255
        if srgb:
            image = srgb_to_linear(image)
    for _ in range(level):
        if min(image.shape[:2]) <= 1:
            break
        if image.shape[0] % 2 or image.shape[1] % 2:
            h, w = max(1, image.shape[0] // 2), max(1, image.shape[1] // 2)
            image = np.stack([np.asarray(Image.fromarray(image[:, :, c]).resize((w, h), Image.Resampling.BOX))
                              for c in range(3)], axis=-1)
        else:
            image = (image[::2, ::2] + image[1::2, ::2] + image[::2, 1::2] + image[1::2, 1::2]) * 0.25
    image.flags.writeable = False
    return image


def sample_world_color(path, world_x, world_y, tile_cm=400, footprint_cm=0, srgb=True):
    with Image.open(path) as source:
        width, height = source.size
    footprint = max(width, height) * footprint_cm / tile_cm
    level = max(0, int(np.floor(np.log2(max(1, footprint)))))
    level = min(level, int(np.log2(min(width, height))))
    stamp = (path.stat().st_size, path.stat().st_mtime_ns)
    image = linear_mip(path, stamp, level, srgb)
    x = np.mod(world_x / tile_cm, 1) * image.shape[1] - 0.5
    y = np.mod(world_y / tile_cm, 1) * image.shape[0] - 0.5
    return sample_bilinear(image, x, y, repeat=True)
