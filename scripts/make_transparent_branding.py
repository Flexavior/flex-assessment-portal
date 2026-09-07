"""Strip baked-in backgrounds and drop shadows from branding PNG/ICO assets."""
from __future__ import annotations

from collections import deque
from pathlib import Path

from PIL import Image

BRANDING = Path(__file__).resolve().parents[1] / "static" / "assets" / "branding"


def flood_transparent(img: Image.Image, tolerance: int = 38) -> Image.Image:
    img = img.copy().convert("RGBA")
    w, h = img.size
    px = img.load()
    refs = [px[0, 0][:3], px[w - 1, 0][:3], px[0, h - 1][:3], px[w - 1, h - 1][:3]]

    def is_bg(rgb: tuple[int, int, int]) -> bool:
        return any(all(abs(rgb[i] - ref[i]) <= tolerance for i in range(3)) for ref in refs)

    seen: set[tuple[int, int]] = set()
    q: deque[tuple[int, int]] = deque()
    for x in range(w):
        for y in (0, h - 1):
            q.append((x, y))
            seen.add((x, y))
    for y in range(h):
        for x in (0, w - 1):
            if (x, y) not in seen:
                q.append((x, y))
                seen.add((x, y))

    while q:
        x, y = q.popleft()
        rgb = px[x, y][:3]
        if not is_bg(rgb):
            continue
        px[x, y] = (rgb[0], rgb[1], rgb[2], 0)
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in seen:
                seen.add((nx, ny))
                q.append((nx, ny))
    return img


def remove_grey_shadow(img: Image.Image, bottom_fraction: float = 0.22, sat_max: int = 28) -> Image.Image:
    img = img.copy()
    w, h = img.size
    px = img.load()
    y0 = int(h * (1 - bottom_fraction))
    for y in range(y0, h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            mx, mn = max(r, g, b), min(r, g, b)
            if mx - mn <= sat_max and 70 <= (r + g + b) / 3 <= 210:
                px[x, y] = (r, g, b, 0)
    return img


def remove_teal_plate(img: Image.Image, sat_max: int = 22, lum_min: int = 150) -> Image.Image:
    """Remove disconnected light teal/grey plate pixels (low saturation)."""
    img = img.copy()
    w, h = img.size
    px = img.load()
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            sat = max(r, g, b) - min(r, g, b)
            lum = (r + g + b) / 3
            if sat <= sat_max and lum >= lum_min and g >= r - 8 and b >= r - 8:
                px[x, y] = (r, g, b, 0)
    return img


def process_logo(path: Path, tolerance: int = 40) -> None:
    img = Image.open(path)
    out = flood_transparent(img, tolerance=tolerance)
    out = remove_teal_plate(out)
    out = remove_grey_shadow(out)
    out.save(path, optimize=True)
    px = out.load()
    w, h = out.size
    transparent = sum(1 for y in range(h) for x in range(w) if px[x, y][3] == 0)
    print(f"{path.name}: {w}x{h}, transparent {100 * transparent / (w * h):.1f}%")


def process_favicon(path: Path) -> None:
    ico = Image.open(path)
    if getattr(ico, "n_frames", 1) > 1:
        sizes = []
        for i in range(ico.n_frames):
            ico.seek(i)
            sizes.append((ico.size[0] * ico.size[1], i))
        ico.seek(max(sizes)[1])
        frame = ico.copy()
    else:
        frame = ico.copy()

    out = flood_transparent(frame, tolerance=32)
    out = remove_teal_plate(out, sat_max=26, lum_min=140)
    out = remove_grey_shadow(out, bottom_fraction=0.35, sat_max=35)
    out.save(path.with_suffix(".png"), optimize=True)
    out.save(path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48)])
    print(f"favicon: wrote transparent {path.name} and favicon.png")


def main() -> None:
    process_logo(BRANDING / "flexavior-logo.png")
    process_favicon(BRANDING / "favicon.ico")


if __name__ == "__main__":
    main()
