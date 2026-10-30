"""生成插件自带的「黑猫」原创牌面（无第三方素材）。

用途：为 tarot_jsons/blackcat 生成 22 张大阿卡纳卡面。
输出：tarot_jsons/blackcat/images/m00.png ~ m21.png（600x1000 PNG）

运行：python tools/generate_blackcat_deck.py
"""

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PLUGIN_DIR = Path(__file__).resolve().parents[1]
OUT_DIR = PLUGIN_DIR / "tarot_jsons" / "blackcat" / "images"

WIDTH, HEIGHT = 600, 1000
BG = (13, 11, 20)
FRAME = (201, 164, 92)
FRAME_DARK = (120, 96, 48)
TEXT = (240, 232, 214)
GOLD = (214, 178, 108)
AMBER = (255, 196, 92)

CARD_NAMES = [
    ("愚者", "THE FOOL"),
    ("魔术师", "THE MAGICIAN"),
    ("女祭司", "THE HIGH PRIESTESS"),
    ("女皇", "THE EMPRESS"),
    ("皇帝", "THE EMPEROR"),
    ("教皇", "THE HIEROPHANT"),
    ("恋人", "THE LOVERS"),
    ("战车", "THE CHARIOT"),
    ("力量", "STRENGTH"),
    ("隐士", "THE HERMIT"),
    ("命运之轮", "WHEEL OF FORTUNE"),
    ("正义", "JUSTICE"),
    ("倒吊人", "THE HANGED MAN"),
    ("死神", "DEATH"),
    ("节制", "TEMPERANCE"),
    ("恶魔", "THE DEVIL"),
    ("高塔", "THE TOWER"),
    ("星星", "THE STAR"),
    ("月亮", "THE MOON"),
    ("太阳", "THE SUN"),
    ("审判", "JUDGEMENT"),
    ("世界", "THE WORLD"),
]

ROMAN = [
    "0", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
    "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX", "XXI",
]


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_background(draw: ImageDraw.ImageDraw, rng: random.Random) -> None:
    """深色星空背景。"""

    draw.rectangle([0, 0, WIDTH, HEIGHT], fill=BG)
    for _ in range(140):
        x = rng.randint(20, WIDTH - 20)
        y = rng.randint(20, HEIGHT - 20)
        radius = rng.choice((1, 1, 1, 2))
        alpha = rng.randint(40, 130)
        draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=(alpha, alpha, alpha + 20))
    # 右上角月牙
    draw.ellipse([430, 70, 540, 180], fill=(226, 214, 176))
    draw.ellipse([450, 56, 560, 166], fill=BG)


def draw_frame(draw: ImageDraw.ImageDraw) -> None:
    """双层金色边框 + 四角装饰。"""

    draw.rectangle([18, 18, WIDTH - 18, HEIGHT - 18], outline=FRAME, width=3)
    draw.rectangle([30, 30, WIDTH - 30, HEIGHT - 30], outline=FRAME_DARK, width=1)
    for x, y, sx, sy in (
        (44, 44, 1, 1),
        (WIDTH - 44, 44, -1, 1),
        (44, HEIGHT - 44, 1, -1),
        (WIDTH - 44, HEIGHT - 44, -1, -1),
    ):
        draw.line([x, y, x + sx * 26, y], fill=FRAME, width=2)
        draw.line([x, y, x, y + sy * 26], fill=FRAME, width=2)
        draw.ellipse([x + sx * 22 - 3, y - 3, x + sx * 22 + 3, y + 3], fill=FRAME)


def draw_cat(draw: ImageDraw.ImageDraw) -> None:
    """居中的黑猫剪影（金色描边）。"""

    cx, cy = WIDTH // 2, 420
    head_r = 92
    # 身体（椭圆）
    draw.ellipse([cx - 130, cy + 40, cx + 130, cy + 400], fill=(22, 19, 30), outline=GOLD, width=3)
    # 尾巴
    draw.arc([cx + 90, cy + 120, cx + 260, cy + 420], start=290, end=90, fill=GOLD, width=8)
    # 头部
    draw.ellipse([cx - head_r, cy - head_r, cx + head_r, cy + head_r], fill=(22, 19, 30), outline=GOLD, width=3)
    # 耳朵
    draw.polygon([(cx - head_r + 6, cy - 60), (cx - 96, cy - 150), (cx - 26, cy - 84)], fill=(22, 19, 30), outline=GOLD)
    draw.polygon([(cx + head_r - 6, cy - 60), (cx + 96, cy - 150), (cx + 26, cy - 84)], fill=(22, 19, 30), outline=GOLD)
    # 眼睛
    draw.ellipse([cx - 52, cy - 16, cx - 18, cy + 18], fill=AMBER)
    draw.ellipse([cx + 18, cy - 16, cx + 52, cy + 18], fill=AMBER)
    draw.ellipse([cx - 40, cy - 6, cx - 34, cy + 2], fill=(20, 16, 10))
    draw.ellipse([cx + 34, cy - 6, cx + 40, cy + 2], fill=(20, 16, 10))
    # 鼻子与胡须
    draw.polygon([(cx - 9, cy + 34), (cx + 9, cy + 34), (cx, cy + 46)], fill=GOLD)
    for offset in (-1, 1):
        draw.line([cx + offset * 20, cy + 40, cx + offset * 105, cy + 26], fill=FRAME_DARK, width=2)
        draw.line([cx + offset * 20, cy + 48, cx + offset * 112, cy + 48], fill=FRAME_DARK, width=2)
        draw.line([cx + offset * 20, cy + 56, cx + offset * 105, cy + 70], fill=FRAME_DARK, width=2)
    # 前爪
    draw.ellipse([cx - 96, cy + 330, cx - 30, cy + 392], fill=(22, 19, 30), outline=GOLD, width=2)
    draw.ellipse([cx + 30, cy + 330, cx + 96, cy + 392], fill=(22, 19, 30), outline=GOLD, width=2)


def centered_text(draw: ImageDraw.ImageDraw, y: int, text: str, font: ImageFont.FreeTypeFont, fill) -> None:
    box = draw.textbbox((0, 0), text, font=font)
    draw.text(((WIDTH - (box[2] - box[0])) / 2, y), text, font=font, fill=fill)


def generate_card(index: int, name: str, english: str) -> None:
    rng = random.Random(20261030 + index)
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)
    draw_background(draw, rng)
    draw_frame(draw)

    numeral_font = load_font(64, bold=True)
    name_font = load_font(58, bold=True)
    english_font = load_font(26)
    small_font = load_font(22)

    centered_text(draw, 60, ROMAN[index], numeral_font, GOLD)
    draw.line([WIDTH // 2 - 70, 150, WIDTH // 2 + 70, 150], fill=FRAME_DARK, width=2)
    draw_cat(draw)
    centered_text(draw, 840, name, name_font, TEXT)
    centered_text(draw, 912, english, english_font, GOLD)
    centered_text(draw, 946, "BLACK CAT  ·  原创卡面", small_font, FRAME_DARK)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    image.save(OUT_DIR / f"m{index:02d}.png", format="PNG", optimize=True)


def main() -> None:
    for index, (name, english) in enumerate(CARD_NAMES):
        generate_card(index, name, english)
    total = sum(p.stat().st_size for p in OUT_DIR.glob("*.png"))
    print(f"生成完成：{len(CARD_NAMES)} 张，共 {total / 1024:.0f} KB -> {OUT_DIR}")


if __name__ == "__main__":
    main()
