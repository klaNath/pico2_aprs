from PIL import Image, ImageDraw
import math, os

SS = 8  # supersampling
BG = (0, 0, 0)
BLUE = (47, 143, 255)
GREEN = (60, 208, 112)
GREY = (110, 110, 110)
WHITE = (240, 240, 240)
RED = (235, 60, 60)
OUT = os.path.dirname(os.path.abspath(__file__))


def draw_icon(size, arcs, arc_col, mast_col, dot_col, cross=False, wf=1.0, step=4.5):
    S = size * SS
    u = S / 32.0  # design unit (32-grid)
    im = Image.new("RGB", (S, S), BG)
    d = ImageDraw.Draw(im)
    cx, cy = 16 * u, 12 * u
    w = 2.2 * u * wf
    # mast (antenna)
    d.polygon([(cx, cy), (cx - 5 * u, 29 * u), (cx + 5 * u, 29 * u)], fill=None,
              outline=None)
    d.line([(cx, cy), (cx - 4.5 * u, 29 * u)], fill=mast_col, width=int(w))
    d.line([(cx, cy), (cx + 4.5 * u, 29 * u)], fill=mast_col, width=int(w))
    d.line([(cx - 2.6 * u, 22 * u), (cx + 2.6 * u, 22 * u)], fill=mast_col, width=int(w * 0.8))
    # arcs ((•))
    for i in range(arcs):
        r = (6 + step * i) * u
        box = [cx - r, cy - r, cx + r, cy + r]
        d.arc(box, 180 - 38, 180 + 38, fill=arc_col, width=int(w))
        d.arc(box, -38, 38, fill=arc_col, width=int(w))
    # top dot
    rd = 2.8 * u
    d.ellipse([cx - rd, cy - rd, cx + rd, cy + rd], fill=dot_col)
    if cross:
        x0, y0, x1, y1 = 20 * u, 18 * u, 30 * u, 28 * u
        d.line([(x0, y0), (x1, y1)], fill=RED, width=int(w * 1.3))
        d.line([(x0, y1), (x1, y0)], fill=RED, width=int(w * 1.3))
    return im.resize((size, size), Image.LANCZOS)


def states(size):
    if size == 20:
        k = dict(wf=1.45, step=6.0)
        return {
            "disconnected": draw_icon(size, 0, GREY, GREY, GREY, cross=True, **k),
            "adv_0": draw_icon(size, 0, BLUE, BLUE, BLUE, **k),
            "adv_1": draw_icon(size, 1, BLUE, BLUE, BLUE, **k),
            "adv_2": draw_icon(size, 2, BLUE, BLUE, BLUE, **k),
            "connected": draw_icon(size, 2, GREEN, WHITE, WHITE, **k),
        }
    return {
        "disconnected": draw_icon(size, 0, GREY, GREY, GREY, cross=True),
        "adv_0": draw_icon(size, 0, BLUE, BLUE, BLUE),
        "adv_1": draw_icon(size, 1, BLUE, BLUE, BLUE),
        "adv_2": draw_icon(size, 2, BLUE, BLUE, BLUE),
        "adv_3": draw_icon(size, 3, BLUE, BLUE, BLUE),
        "connected": draw_icon(size, 3, GREEN, WHITE, WHITE),
    }


def rgb565(im):
    out = []
    for r, g, b in im.get_flattened_data():
        out.append(((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3))
    return out


header = ["// BLE status icons, RGB565 (big-endian value per pixel), background black",
          "#pragma once", "#include <stdint.h>", ""]
all_icons = {}
for size in (32, 24, 20):
    icons = states(size)
    all_icons[size] = icons
    for name, im in icons.items():
        im.save(f"{OUT}/ble_{name}_{size}.png")
        data = rgb565(im)
        header.append(f"static const uint16_t ble_{name}_{size}[{size}*{size}] = {{")
        for k in range(0, len(data), 12):
            header.append("  " + ", ".join(f"0x{v:04X}" for v in data[k:k + 12]) + ",")
        header.append("};\n")
open(f"{OUT}/ble_icons.h", "w").write("\n".join(header))

# animated GIF for advertising (enlarged)
frames = [all_icons[32][f"adv_{i}"].resize((192, 192), Image.NEAREST) for i in (0, 1, 2, 3)]
frames[0].save(f"{OUT}/adv_anim.gif", save_all=True, append_images=frames[1:], duration=300, loop=0)

# preview sheet: enlarged icons + simulated 320x240 screen at 1:1 scaled x2
from PIL import ImageFont
sheet = Image.new("RGB", (900, 700), (30, 30, 30))
dd = ImageDraw.Draw(sheet)
order = ["disconnected", "adv_1", "adv_2", "adv_3", "connected"]
labels = ["切断", "ADV 1", "ADV 2", "ADV 3", "接続"]
try:
    font = ImageFont.truetype("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 22)
except Exception:
    font = ImageFont.load_default()
for i, (n, lab) in enumerate(zip(order, labels)):
    big = all_icons[32][n].resize((160, 160), Image.NEAREST)
    sheet.paste(big, (20 + i * 175, 20))
    dd.text((20 + i * 175 + 50, 185), lab, fill=(220, 220, 220), font=font)
# mock screen 320x240 shown at 2x
scr = Image.new("RGB", (320, 240), (0, 0, 0))
sd = ImageDraw.Draw(scr)
sd.rectangle([0, 0, 319, 27], fill=(0, 0, 0))
scr.paste(all_icons[24]["connected"], (292, 2))
scr.paste(all_icons[24]["adv_3"], (264, 2))
scr.paste(all_icons[24]["disconnected"], (236, 2))
scr.paste(all_icons[32]["connected"], (144, 104))
sheet.paste(scr.resize((640, 480), Image.NEAREST), (130, 220))
sheet.save(f"{OUT}/preview.png")
print("ok")
