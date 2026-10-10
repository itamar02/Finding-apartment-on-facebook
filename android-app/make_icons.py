"""Write the launcher icons (a house on dark blue) into android/app/src/main/res.

Standard library only. Run by build_apk.py after the Android project exists; the adaptive-icon
XML is removed so every Android version uses these PNGs.
"""
import struct
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
RES = HERE / "android" / "app" / "src" / "main" / "res"
SIZES = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
BG, FG = (15, 23, 42), (56, 189, 248)


def png(size, round_mask=False):
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            u, v = x / size, y / size
            c = BG
            if 0.22 <= v <= 0.48 and abs(u - 0.5) <= (v - 0.22) / 0.26 * 0.32:
                c = FG
            if 0.46 <= v <= 0.78 and 0.27 <= u <= 0.73:
                c = FG
            if 0.58 <= v <= 0.78 and 0.44 <= u <= 0.56:
                c = BG
            alpha = 255
            if round_mask and (u - 0.5) ** 2 + (v - 0.5) ** 2 > 0.25:
                alpha = 0
            row += bytes(c) + bytes([alpha])
        rows.append(bytes(row))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def main():
    if not RES.exists():
        sys.exit("android/ does not exist yet: run `npx cap add android` first")
    for density, size in SIZES.items():
        folder = RES / f"mipmap-{density}"
        folder.mkdir(exist_ok=True)
        (folder / "ic_launcher.png").write_bytes(png(size))
        (folder / "ic_launcher_round.png").write_bytes(png(size, round_mask=True))
        (folder / "ic_launcher_foreground.png").unlink(missing_ok=True)
    for f in (RES / "mipmap-anydpi-v26").glob("ic_launcher*.xml"):
        f.unlink()
    print("launcher icons written")


if __name__ == "__main__":
    main()
