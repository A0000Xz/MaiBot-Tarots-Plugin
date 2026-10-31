"""塔罗工坊 · 核心数据与纯逻辑。

牌阵 / 牌组加载与校验、关键词解析、结果文本、人设材料、桌布风布局图。
不包含插件生命周期与发送流程（那些在 plugin.py）。
"""

import io
import json
import math
import random
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any


__all__ = [
    "PLUGIN_DIR",
    "TAROT_JSON_DIR",
    "FORMATION_FILE",
    "DEFAULT_FORMATION",
    "DEFAULT_CARD_TYPE",
    "CARD_TYPES",
    "MAJOR_CARD_IDS",
    "MINOR_CARD_IDS",
    "ALL_CARD_IDS",
    "FALLBACK_FORMATION_NAMES",
    "NUMERAL_EMOJI",
    "ORIENTATION_UP",
    "ORIENTATION_REVERSED",
    "TAROT_ICON",
    "IMAGE_DOWNLOAD_RETRIES",
    "IMAGE_DOWNLOAD_TIMEOUT",
    "IMAGE_MAX_BYTES",
    "PERSONA_INSTRUCTION",
    "FOLLOWUP_INSTRUCTION",
    "DAILY_INSTRUCTION",
    "FOLLOWUP_WORDS",
    "DAILY_WORDS",
    "LAST_DRAW_TTL",
    "load_json",
    "load_formations",
    "available_deck_ids",
    "load_deck",
    "deck_card_range",
    "deck_display_name",
    "looks_like_tarot_request",
    "parse_formation_from_text",
    "parse_card_type_from_text",
    "is_followup_text",
    "is_daily_request",
    "rotate_image_180",
    "build_result_text",
    "build_persona_material",
    "load_custom_formations",
    "compose_spread_image",
]


PLUGIN_DIR = Path(__file__).parent
TAROT_JSON_DIR = PLUGIN_DIR / "tarot_jsons"
FORMATION_FILE = TAROT_JSON_DIR / "formation.json"

DEFAULT_FORMATION = "单张"
DEFAULT_CARD_TYPE = "全部"
CARD_TYPES = ("全部", "大阿卡纳", "小阿卡纳")
MAJOR_CARD_IDS = tuple(str(i) for i in range(22))
MINOR_CARD_IDS = tuple(str(i) for i in range(22, 78))
ALL_CARD_IDS = MAJOR_CARD_IDS + MINOR_CARD_IDS

# 牌阵名用于 Tool 参数的 enum；formation.json 加载失败时退回这份内置列表。
FALLBACK_FORMATION_NAMES = (
    "单张",
    "圣三角",
    "时间之流",
    "四要素",
    "五牌阵",
    "吉普赛十字",
    "马蹄",
    "六芒星",
    "凯尔特十字",
    "二选一",
    "身心灵",
)

# 牌阵别名：让懂塔罗的群友用自己的说法也能触发
FORMATION_ALIASES: dict[str, tuple[str, ...]] = {
    "单张": ("单牌", "一张", "抽一张", "单卡", "抽1张"),
    "圣三角": ("三角牌阵", "三角阵", "圣三角牌阵", "三角"),
    "时间之流": ("三张", "三牌", "三张牌", "过去现在未来", "时间流", "时间之流牌阵", "抽3张"),
    "四要素": ("四元素", "风火水土", "四要素牌阵", "四张"),
    "五牌阵": ("五张", "五牌", "五张牌", "五牌阵牌阵"),
    "吉普赛十字": ("十字", "十字牌阵", "吉普赛"),
    "马蹄": ("马蹄形", "马蹄阵", "六张", "六牌"),
    "六芒星": ("六芒星阵", "六角星", "六芒", "七张", "七牌"),
    "凯尔特十字": ("凯尔特", "凯尔特阵", "十张", "十牌", "凯尔特十牌"),
    "二选一": ("二择一", "两个选择", "AB选择", "二选一牌阵"),
    "身心灵": ("身心灵牌阵", "身心", "精神牌阵"),
}

NUMERAL_EMOJI = ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟")
ORIENTATION_UP = "☀️ 正位"
ORIENTATION_REVERSED = "🌗 逆位"
TAROT_ICON = "🔮"

IMAGE_DOWNLOAD_RETRIES = 3
IMAGE_DOWNLOAD_TIMEOUT = 20
IMAGE_MAX_BYTES = 8 * 1024 * 1024
SEND_RETRY_DELAY = 1.0

_TAROT_WORDS = ("塔罗", "占卜")
_DRAW_INTENT_WORDS = ("抽", "占卜", "卜", "算", "测", "看", "问", "求", "来", "帮")
_MENTION_PATTERN = re.compile(r"@[^\s@]+")
PERSONA_INSTRUCTION = (
    "请用你平时说话的语气，为用户解读下面的塔罗牌结果："
    "只依据材料里的牌名、正逆位和牌义，不要编造其他牌；"
    "不要输出标题或列表格式，像聊天一样自然地说，控制在 5 句以内。"
)

FOLLOWUP_INSTRUCTION = (
    "用户正在追问上一次塔罗占卜。请用你平时说话的语气接着回答，"
    "只根据上一次的牌面材料回答，不要重新抽牌、不要编造其他牌；像聊天一样自然，控制在 4 句以内。"
)

DAILY_INSTRUCTION = (
    "这是给用户的「今日一牌」。请用你平时说话的语气，结合牌面和这句话给一句今日运势解读，"
    "温暖一点、具体一点，控制在 3 句以内。"
)

FOLLOWUP_WORDS = ("刚才", "上一张", "那张牌", "这个牌阵", "继续", "再解释", "什么意思", "怎么说")
DAILY_WORDS = ("每日一牌", "今日一牌", "每天一牌", "今日塔罗", "今天塔罗", "今日运势", "今天运势")
LAST_DRAW_TTL = 600  # 追问上下文保留 10 分钟

# 各牌阵的卡牌中心位置（单位：一张牌宽/高），未列出的牌阵按网格排布
SPREAD_LAYOUTS: dict[str, list[tuple[float, float]]] = {
    "单张": [(0.0, 0.0)],
    "时间之流": [(-0.95, 0.0), (0.0, 0.0), (0.95, 0.0)],
    "圣三角": [(-0.9, 0.55), (0.0, -0.55), (0.9, 0.55)],
    "四要素": [(-0.6, -0.6), (0.6, -0.6), (-0.6, 0.6), (0.6, 0.6)],
    "吉普赛十字": [(0.0, 0.0), (-0.95, 0.0), (0.95, 0.0), (0.0, -0.95), (0.0, 0.95)],
    "五牌阵": [(0.0, 0.0), (-0.95, 0.35), (0.95, 0.35), (0.0, -0.8), (0.0, 0.95)],
    "马蹄": [(-1.25, 0.55), (-0.9, -0.2), (-0.35, -0.7), (0.35, -0.7), (0.9, -0.2), (1.25, 0.55)],
    "六芒星": [
        (-1.2, 0.0),
        (-0.6, -0.95),
        (0.6, -0.95),
        (1.2, 0.0),
        (0.6, 0.95),
        (-0.6, 0.95),
        (0.0, 0.0),
    ],
    "凯尔特十字": [
        (-1.9, -0.6),
        (-0.95, -0.6),
        (0.0, -0.6),
        (0.95, -0.6),
        (1.9, -0.6),
        (-1.9, 0.6),
        (-0.95, 0.6),
        (0.0, 0.6),
        (0.95, 0.6),
        (1.9, 0.6),
    ],
    "二选一": [(-0.95, -0.55), (-0.95, 0.55), (0.95, -0.55), (0.95, 0.55), (0.0, 0.0)],
    "身心灵": [(-0.95, 0.0), (0.0, 0.0), (0.95, 0.0)],
}

CARD_W, CARD_H = 260, 390
CARD_GAP = 30
LABEL_H = 46




def load_json(path: Path) -> dict[str, Any] | None:
    """读取 UTF-8 JSON；失败返回 None。"""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def load_formations() -> dict[str, dict[str, Any]]:
    """读取牌阵配置。"""

    data = load_json(FORMATION_FILE)
    if not data:
        return {}
    formations: dict[str, dict[str, Any]] = {}
    for name, value in data.items():
        if not isinstance(value, dict):
            continue
        cards_num = value.get("cards_num")
        represent = value.get("represent")
        if not isinstance(cards_num, int) or cards_num <= 0 or not isinstance(represent, list) or not represent:
            continue
        formations[str(name)] = {
            "cards_num": cards_num,
            "is_cut": bool(value.get("is_cut", False)),
            "represent": [str(item) for item in represent[0]],
        }
    return formations


def available_deck_ids() -> list[str]:
    """扫描 tarot_jsons 下带 tarots.json 的牌组目录。"""

    if not TAROT_JSON_DIR.is_dir():
        return []
    return sorted(
        child.name
        for child in TAROT_JSON_DIR.iterdir()
        if child.is_dir() and (child / "tarots.json").is_file()
    )


def load_deck(deck_id: str) -> dict[str, Any] | None:
    """读取一套牌组，返回 {"meta": {...}, "cards": {id: {...}}}。"""

    path = TAROT_JSON_DIR / deck_id / "tarots.json"
    raw = load_json(path)
    if not raw:
        return None
    meta = raw.get("_meta") if isinstance(raw.get("_meta"), dict) else {}
    cards = {key: value for key, value in raw.items() if key != "_meta" and isinstance(value, dict)}
    if not cards:
        return None
    return {"meta": meta, "cards": cards}


def deck_card_range(deck: dict[str, Any], card_type: str) -> list[str]:
    """按牌组实际包含的牌，返回请求范围内的卡牌 ID。"""

    cards = deck["cards"]
    major = [card_id for card_id in MAJOR_CARD_IDS if card_id in cards]
    minor = [card_id for card_id in MINOR_CARD_IDS if card_id in cards]
    if card_type == "大阿卡纳":
        return major
    if card_type == "小阿卡纳":
        return minor
    return major + minor


def deck_display_name(deck_id: str, deck: dict[str, Any]) -> str:
    """牌组展示名。"""

    description = str(deck["meta"].get("description") or "").strip()
    if description:
        return f"{deck_id}（{description}）"
    total = len(deck["cards"])
    return f"{deck_id}（{total} 张）"


def looks_like_tarot_request(text: str) -> bool:
    """粗判是否是「想抽塔罗/占卜」的请求（关键词兜底）。"""

    normalized = re.sub(r"\s+", " ", _MENTION_PATTERN.sub(" ", text)).strip()
    if not normalized or normalized.startswith("/"):
        return False
    if not any(word in normalized for word in _TAROT_WORDS):
        return False
    return any(word in normalized for word in _DRAW_INTENT_WORDS)


def parse_formation_from_text(text: str, formations: dict[str, dict[str, Any]]) -> str:
    """从自然语言里识别牌阵名（支持内置别名与自定义牌阵）；没说就用单张。"""

    normalized = _MENTION_PATTERN.sub(" ", text)
    normalized = normalized.replace(" ", "")
    # 先匹配完整牌阵名，再匹配别名，避免「十字」抢走「凯尔特十字」
    for name in formations:
        if name in normalized:
            return name
    for name, aliases in FORMATION_ALIASES.items():
        if name not in formations:
            continue
        for alias in aliases:
            if alias in normalized:
                return name
    return DEFAULT_FORMATION


def _compact_message_text(text: str) -> str:
    """去掉 @提及和空白，便于做短文本匹配。"""

    return re.sub(r"\s+", "", _MENTION_PATTERN.sub("", text))


def is_followup_text(text: str, max_length: int = 40) -> bool:
    """判断一句话是否像在追问上一次抽牌。"""

    compact = _compact_message_text(text)
    if not compact or len(compact) > max_length:
        return False
    return any(word in compact for word in FOLLOWUP_WORDS)


def is_daily_request(text: str) -> bool:
    """判断是否是「每日一牌 / 今日运势」请求。"""

    compact = _compact_message_text(text)
    if not compact or compact.startswith("/"):
        return False
    if not any(word in compact for word in DAILY_WORDS):
        return False
    return any(word in compact for word in ("塔罗", "占卜", "牌"))


def parse_card_type_from_text(text: str) -> str:
    """从自然语言里识别抽牌范围。"""

    normalized = _MENTION_PATTERN.sub(" ", text)
    if "大阿卡纳" in normalized or "大牌" in normalized:
        return "大阿卡纳"
    if "小阿卡纳" in normalized or "小牌" in normalized:
        return "小阿卡纳"
    return DEFAULT_CARD_TYPE


def rotate_image_180(image_bytes: bytes) -> bytes | None:
    """把正位卡面旋转 180° 生成逆位图；Pillow 不可用时返回 None。"""

    try:
        from PIL import Image  # 延迟导入，避免无 Pillow 环境直接崩
    except ImportError:
        return None
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            rotated = image.rotate(180)
            buffer = io.BytesIO()
            rotated.save(buffer, format="PNG")
            return buffer.getvalue()
    except Exception:
        return None


def build_result_text(
    formation_name: str,
    deck_id: str,
    cards: Sequence[tuple[str, bool]],
    formation: dict[str, Any],
    deck: dict[str, Any],
) -> str:
    """构造带 emoji 的抽牌结果文本。"""

    lines = [f"{TAROT_ICON} 塔罗占卜 · {formation_name}牌阵（{deck_id} 牌组）", ""]
    positions = formation.get("represent") or []
    for index, (card_id, is_reversed) in enumerate(cards):
        card = deck["cards"][card_id]
        name = str(card.get("name") or card_id)
        info = card.get("info") if isinstance(card.get("info"), dict) else {}
        description = str(
            (info.get("reverseDescription") if is_reversed else info.get("description")) or "（暂无牌义）"
        )
        position = positions[index] if index < len(positions) else f"位置{index + 1}"
        numeral = NUMERAL_EMOJI[index] if index < len(NUMERAL_EMOJI) else f"{index + 1}."
        orientation = ORIENTATION_REVERSED if is_reversed else ORIENTATION_UP
        lines.append(f"{numeral} {position} · {name} · {orientation}")
        lines.append(f"   {description}")
    reversed_count = sum(1 for _, is_reversed in cards if is_reversed)
    lines.append("")
    lines.append(f"🃏 共 {len(cards)} 张牌，其中 {reversed_count} 张逆位")
    return "\n".join(lines)


def build_persona_material(
    formation_name: str,
    cards: Sequence[tuple[str, bool]],
    formation: dict[str, Any],
    deck: dict[str, Any],
    question: str,
) -> str:
    """构造交给宿主生成人设解读的材料。"""

    lines = [f"抽牌场景：塔罗{formation_name}牌阵"]
    positions = formation.get("represent") or []
    for index, (card_id, is_reversed) in enumerate(cards):
        card = deck["cards"][card_id]
        info = card.get("info") if isinstance(card.get("info"), dict) else {}
        description = str(
            (info.get("reverseDescription") if is_reversed else info.get("description")) or ""
        )
        position = positions[index] if index < len(positions) else f"位置{index + 1}"
        orientation = "逆位" if is_reversed else "正位"
        lines.append(f"「{position}」{card.get('name') or card_id}（{orientation}）：{description}")
    lines.append(f"用户的问题：{question.strip() or '（没有特别说明）'}")
    return "\n".join(lines)


def load_custom_formations(data_dir: Path) -> dict[str, dict[str, Any]]:
    """从 data 目录读取用户自定义牌阵（formations.json），校验后返回。"""

    raw = load_json(data_dir / "formations.json")
    if not raw:
        return {}
    formations: dict[str, dict[str, Any]] = {}
    for name, value in raw.items():
        if not isinstance(value, dict):
            continue
        cards_num = value.get("cards_num")
        represent = value.get("represent")
        if not isinstance(cards_num, int) or not 1 <= cards_num <= 10:
            continue
        if not isinstance(represent, list) or not represent or not isinstance(represent[0], list):
            continue
        positions = [str(item) for item in represent[0]]
        while len(positions) < cards_num:
            positions.append(f"位置{len(positions) + 1}")
        formations[str(name).strip()] = {
            "cards_num": cards_num,
            "is_cut": bool(value.get("is_cut", False)),
            "represent": positions[:cards_num],
            "custom": True,
        }
    return {name: value for name, value in formations.items() if name}


def _load_font(size: int) -> Any:
    """尽量找一个能显示中文的字体；找不到就退回默认字体。"""

    try:
        from PIL import ImageFont
    except ImportError:
        return None
    for path in (
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _gradient_color(ratio: float) -> tuple[int, int, int]:
    """桌布背景的竖向渐变颜色（深靛蓝 -> 紫罗兰）。"""

    ratio = max(0.0, min(1.0, ratio))
    top = (26, 17, 48)
    bottom = (58, 34, 86)
    return tuple(int(top[index] + (bottom[index] - top[index]) * ratio) for index in range(3))


def _draw_mystic_tablecloth(draw: Any, width: int, height: int, rng: random.Random) -> None:
    """塔罗桌布风背景：渐变紫布 + 星点 + 星座 + 日月符号 + 星形点缀。"""

    for y in range(height):
        draw.line([(0, y), (width, y)], fill=_gradient_color(y / max(1, height - 1)))
    star_count = max(90, width * height // 4200)
    for _ in range(star_count):
        x = rng.randint(0, width)
        y = rng.randint(0, height)
        radius = rng.choice((1, 1, 2))
        glow = rng.randint(70, 180)
        draw.ellipse(
            [x - radius, y - radius, x + radius, y + radius],
            fill=(min(255, 200 + glow // 3), min(255, 190 + glow // 4), 255),
        )
    points = [(rng.randint(30, width - 30), rng.randint(30, height - 70)) for _ in range(16)]
    for index in range(len(points) - 1):
        if rng.random() < 0.65:
            draw.line([points[index], points[index + 1]], fill=(128, 116, 178), width=1)
    # 右上月亮
    moon_x, moon_y, moon_r = width - 96, 96, 40
    draw.ellipse([moon_x - moon_r, moon_y - moon_r, moon_x + moon_r, moon_y + moon_r], fill=(236, 224, 184))
    shade = _gradient_color(moon_y / max(1, height - 1))
    draw.ellipse(
        [moon_x - moon_r + 20, moon_y - moon_r - 14, moon_x + moon_r + 20, moon_y + moon_r - 14],
        fill=shade,
    )
    # 左上太阳
    sun_x, sun_y, sun_r = 92, 92, 26
    draw.ellipse([sun_x - sun_r, sun_y - sun_r, sun_x + sun_r, sun_y + sun_r], fill=(244, 200, 120))
    for angle in range(0, 360, 45):
        radians = math.radians(angle)
        start = (sun_x + math.cos(radians) * (sun_r + 8), sun_y + math.sin(radians) * (sun_r + 8))
        end = (sun_x + math.cos(radians) * (sun_r + 22), sun_y + math.sin(radians) * (sun_r + 22))
        draw.line([start, end], fill=(244, 200, 120), width=3)
    # 金色星形符号
    for _ in range(6):
        cx = rng.randint(60, max(61, width - 60))
        cy = rng.randint(170, max(171, height - 120))
        size = rng.choice((7, 9, 12))
        draw.polygon(
            [
                (cx, cy - size),
                (cx + size * 0.35, cy - size * 0.2),
                (cx + size, cy - size * 0.2),
                (cx + size * 0.45, cy + size * 0.35),
                (cx + size * 0.7, cy + size),
                (cx, cy + size * 0.6),
                (cx - size * 0.7, cy + size),
                (cx - size * 0.45, cy + size * 0.35),
                (cx - size, cy - size * 0.2),
                (cx - size * 0.35, cy - size * 0.2),
            ],
            fill=(214, 178, 108),
        )


def _apply_cloth_texture(image: Any) -> Any:
    """叠加斜向织纹，做出布料质感。"""

    from PIL import Image as _Image, ImageDraw as _ImageDraw

    overlay = _Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = _ImageDraw.Draw(overlay)
    width, height = image.size
    for offset in range(-height, width, 46):
        draw.line([(offset, 0), (offset + height, height)], fill=(255, 255, 255, 7), width=2)
    for offset in range(-height, width, 92):
        draw.line([(offset, 0), (offset + height, height)], fill=(255, 255, 255, 4), width=10)
    return _Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")


def compose_spread_image(
    formation_name: str,
    entries: Sequence[tuple[bytes, str, str]],
) -> bytes | None:
    """把整副牌按牌阵位置拼成一张布局图（塔罗桌布背景）。"""

    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    if not entries:
        return None
    try:
        count = len(entries)
        layout = SPREAD_LAYOUTS.get(formation_name)
        if layout is None or len(layout) != count:
            columns = 1 if count == 1 else min(3, count)
            rows = (count + columns - 1) // columns
            layout = [
                ((index % columns) - (columns - 1) / 2, (index // columns) - (rows - 1) / 2)
                for index in range(count)
            ]
        cell_w = CARD_W + CARD_GAP
        cell_h = CARD_H + LABEL_H + CARD_GAP
        xs = [x for x, _ in layout]
        ys = [y for _, y in layout]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        margin = 46
        title_h = 86
        width = int((max_x - min_x) * cell_w + CARD_W + 2 * margin)
        height = int((max_y - min_y) * cell_h + CARD_H + LABEL_H + 2 * margin + title_h)
        rng = random.Random(formation_name)
        canvas = Image.new("RGB", (width, height), (16, 14, 24))
        draw = ImageDraw.Draw(canvas)
        _draw_mystic_tablecloth(draw, width, height, rng)
        canvas = _apply_cloth_texture(canvas)
        draw = ImageDraw.Draw(canvas)

        title_font = _load_font(34)
        label_font = _load_font(24)
        title = f"TAROT · {formation_name}牌阵"
        if title_font is not None:
            box = draw.textbbox((0, 0), title, font=title_font)
            draw.text(((width - (box[2] - box[0])) / 2, 28), title, font=title_font, fill=(214, 178, 108))
        draw.line([(width * 0.28, 74), (width * 0.72, 74)], fill=(150, 122, 64), width=2)

        for index, (image_bytes, card_name, position) in enumerate(entries):
            x_unit, y_unit = layout[index]
            left = margin + (x_unit - min_x) * cell_w
            top = margin + title_h + (y_unit - min_y) * cell_h
            draw.rounded_rectangle(
                [left - 6, top - 6, left + CARD_W + 14, top + CARD_H + 14],
                radius=16,
                fill=(18, 12, 28),
            )
            with Image.open(io.BytesIO(image_bytes)) as card:
                card = card.convert("RGB")
                card.thumbnail((CARD_W, CARD_H))
                canvas.paste(card, (int(left), int(top)))
            draw.rounded_rectangle(
                [left - 3, top - 3, left + CARD_W + 3, top + CARD_H + 3],
                radius=12,
                outline=(201, 164, 92),
                width=2,
            )
            label = f"{position} · {card_name}"
            if label_font is not None:
                box = draw.textbbox((0, 0), label, font=label_font)
                text_x = left + (CARD_W - (box[2] - box[0])) / 2
                text_y = top + CARD_H + 10
                draw.rounded_rectangle(
                    [text_x - 10, text_y - 2, text_x + (box[2] - box[0]) + 10, text_y + (box[3] - box[1]) + 4],
                    radius=8,
                    fill=(22, 15, 34),
                )
                draw.text((text_x, text_y), label, font=label_font, fill=(240, 232, 214))
        buffer = io.BytesIO()
        canvas.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()
    except Exception:
        return None


# ──── WebUI 配置页布局（只重组展示，不改配置模型 / 默认值 / TOML 路径）────

