"""塔罗牌插件 v2 — 为 MaiBot 1.3.x / 插件 SDK 2.x 重写。

继承自 A0000Xz/MaiBot-Tarots-Plugin 的玩法（8 种牌阵、大/小阿卡纳、多牌组、
卡面下载与缓存、/tarots cache 与 /tarots switch、管理员白名单、人设风格解牌），
并把旧版 Action/Command 迁移到新 SDK 的 Tool/Command，新增关键词兜底与友好排版。

卡面图片在运行时从各牌组 JSON 里配置的 base_url 下载并缓存，仓库本身不存图片。
"""

import asyncio
import base64
import io
import json
import random
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from maibot_sdk import Command, Field, HookHandler, MaiBotPlugin, PluginConfigBase, Tool
from maibot_sdk.types import HookMode, HookOrder, ToolParameterInfo, ToolParamType


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
)

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
_TAROTS_COMMAND_PATTERN = r"/tarots\s+(?P<action>cache|switch|list|allow|lock)(?:\s+(?P<value>\S+))?"

PERSONA_INSTRUCTION = (
    "请用你平时说话的语气，为用户解读下面的塔罗牌结果："
    "只依据材料里的牌名、正逆位和牌义，不要编造其他牌；"
    "不要输出标题或列表格式，像聊天一样自然地说，控制在 5 句以内。"
)


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
    """从自然语言里识别牌阵名；没说就用单张。"""

    normalized = _MENTION_PATTERN.sub(" ", text)
    for name in formations:
        if name in normalized:
            return name
    if "三张" in normalized and "时间之流" in formations:
        return "时间之流"
    if "十字" in normalized and "吉普赛十字" in formations:
        return "吉普赛十字"
    return DEFAULT_FORMATION


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


# ──── WebUI 配置页布局（只重组展示，不改配置模型 / 默认值 / TOML 路径）────


@dataclass(frozen=True)
class _UiSection:
    """配置页里的一个展示块。"""

    source: str
    title: str
    description: str
    fields: dict[str, str]
    collapsed: bool = False


_UI_SECTIONS: dict[str, _UiSection] = {
    "cards": _UiSection(
        source="cards",
        title="牌组设置",
        description="默认用哪套牌组、以及允许使用哪些牌组。带锁的牌组涉及第三方版权，默认不启用。",
        fields={"using_cards": "默认牌组", "use_cards": "允许启用的牌组（逐项添加牌组名）"},
    ),
    "permissions": _UiSection(
        source="permissions",
        title="管理员",
        description="只有名单里的 QQ 号能用 /tarots 管理指令；普通群友抽牌不受限制。",
        fields={"admin_users": "管理员 QQ 号"},
    ),
    "components": _UiSection(
        source="components",
        title="功能开关",
        description="抽牌工具 / 关键词兜底、以及管理指令，可以分别关掉。",
        fields={"enable_tarots": "启用抽牌工具", "enable_commands": "启用 /tarots 指令"},
    ),
    "draw_experience": _UiSection(
        source="adjustment",
        title="抽牌体验",
        description="决定抽牌结果怎么呈现：要不要关键词兜底、要不要额外发原始牌面文本。",
        fields={
            "enable_keyword_fallback": "关键词兜底抽牌",
            "enable_original_text": "额外发送原始牌面文本",
        },
    ),
    "draw_advanced": _UiSection(
        source="adjustment",
        title="呈现细节",
        description="emoji 与图片缺失时的处理方式，一般保持默认即可。",
        fields={"emoji_enabled": "结果使用 emoji", "strict_images": "卡面缺失时中止占卜"},
        collapsed=True,
    ),
    "proxy": _UiSection(
        source="proxy",
        title="卡面下载代理",
        description="部署机访问不了图源时，在这里填代理；缓存过的卡面不再联网。",
        fields={"enable_proxy": "启用代理", "proxy_url": "代理地址"},
        collapsed=True,
    ),
    "plugin_info": _UiSection(
        source="plugin",
        title="插件信息",
        description="用于版本识别，保持现状即可。",
        fields={"enabled": "启用插件", "config_version": "配置版本"},
        collapsed=True,
    ),
}

_UI_TABS: dict[str, tuple[str, tuple[str, ...]]] = {
    "basic": ("常用", ("cards", "permissions")),
    "draw": ("抽牌", ("components", "draw_experience")),
    "advanced": ("高级", ("proxy", "draw_advanced", "plugin_info")),
}

_UI_HINTS: dict[str, str] = {
    "cards.using_cards": "默认牌组名，例如 classical（公版韦特）。运行中可用 /tarots switch 切换，切换结果会记住。",
    "cards.use_cards": "允许使用的牌组清单。classical / elaine 为公版韦特；bilibili、east 默认不启用，确认使用权后可用 /tarots allow 临时解锁。",
    "permissions.admin_users": "填纯数字 QQ 号；留空则没人能用 /tarots 指令（抽牌不受影响）。",
    "components.enable_tarots": "关掉后自然语言/关键词都不会抽牌，只剩管理指令。",
    "components.enable_commands": "关掉后 /tarots 指令不可用。",
    "adjustment.enable_keyword_fallback": "消息里出现「塔罗/占卜 + 抽/看/问…」时直接抽牌，避免规划器漏调工具后自己编牌。",
    "adjustment.enable_original_text": "在人设解读之外，额外发一份未经过滤的牌面文本，方便对照。",
    "adjustment.emoji_enabled": "结果标题与正逆位前的 emoji。",
    "adjustment.strict_images": "开：任一张卡面下载失败就中止占卜；关：降级为纯文字解读。",
    "proxy.enable_proxy": "只有在部署机访问不了卡面图源时才需要开。",
    "proxy.proxy_url": "例如 http://127.0.0.1:7890；只用于下载卡面。",
    "plugin.config_version": "由插件维护，不要手动改。",
}


def build_config_ui_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """在 SDK 生成的 Schema 上补中文页签 / 分组 / 提示，字段默认值与类型保持不变。"""

    source_sections = schema.get("sections") or {}
    sections: dict[str, Any] = {}
    tabs: list[dict[str, Any]] = []
    for tab_id, (tab_title, section_ids) in _UI_TABS.items():
        tabs.append({"id": tab_id, "title": tab_title, "sections": list(section_ids)})
        for section_id in section_ids:
            spec = _UI_SECTIONS[section_id]
            template = source_sections.get(spec.source)
            if template is None:
                continue
            template_fields = template.get("fields") or {}
            fields: dict[str, Any] = {}
            for order, (field_name, label) in enumerate(spec.fields.items()):
                field_template = template_fields.get(field_name)
                if field_template is None:
                    continue
                path = f"{spec.source}.{field_name}"
                entry = dict(field_template)
                hint = _UI_HINTS.get(path, entry.get("description") or "")
                entry.update(label=label, hint=hint, description=hint, order=order)
                if path == "plugin.config_version":
                    entry["disabled"] = True
                fields[field_name] = entry
            if not fields:
                continue
            sections[section_id] = {
                **template,
                "name": spec.source,
                "title": spec.title,
                "description": spec.description,
                "collapsed": spec.collapsed,
                "order": len(sections),
                "fields": fields,
            }
    return {**schema, "sections": sections, "layout": {"type": "tabs", "tabs": tabs}}


class PluginSection(PluginConfigBase):
    """插件基础配置。"""

    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="是否启用插件")
    config_version: str = Field(default="2.0.0", description="配置版本")


class ComponentsSection(PluginConfigBase):
    """组件开关。"""

    __ui_label__ = "组件"
    __ui_icon__ = "toggle-left"
    __ui_order__ = 1

    enable_tarots: bool = Field(default=True, description="是否启用抽牌工具与关键词兜底")
    enable_commands: bool = Field(default=True, description="是否启用 /tarots 管理指令")


class ProxySection(PluginConfigBase):
    """卡面下载代理。"""

    __ui_label__ = "下载代理"
    __ui_icon__ = "globe"
    __ui_order__ = 2

    enable_proxy: bool = Field(default=False, description="下载卡面时是否使用代理")
    proxy_url: str = Field(default="", description="代理地址，例如 http://127.0.0.1:7890")


class CardsSection(PluginConfigBase):
    """牌组设置。"""

    __ui_label__ = "牌组"
    __ui_icon__ = "layers"
    __ui_order__ = 3

    using_cards: str = Field(default="classical", description="默认使用的牌组名（可用 /tarots switch 切换）")
    use_cards: list[str] = Field(
        default_factory=lambda: ["classical", "elaine"],
        description="允许使用的牌组名列表；不在列表里的牌组即使存在也不可用（可用 /tarots allow 临时解锁）",
    )


class AdjustmentSection(PluginConfigBase):
    """功能微调。"""

    __ui_label__ = "功能微调"
    __ui_icon__ = "sliders"
    __ui_order__ = 4

    enable_original_text: bool = Field(default=False, description="是否额外发送未经过人设解读的原始牌面文本")
    enable_keyword_fallback: bool = Field(
        default=True,
        description="关键词兜底：检测到「塔罗/占卜 + 抽/看/问…」时直接抽牌，避免规划器漏调工具",
    )
    emoji_enabled: bool = Field(default=True, description="结果文本里是否使用 emoji")
    strict_images: bool = Field(default=True, description="卡面图片缺失时是否中止占卜（关闭则降级为纯文字）")


class PermissionsSection(PluginConfigBase):
    """管理员白名单。"""

    __ui_label__ = "权限"
    __ui_icon__ = "shield"
    __ui_order__ = 5

    admin_users: list[str] = Field(
        default_factory=list,
        description="允许使用 /tarots 指令的 QQ 号列表（纯数字）",
    )


class TarotsConfig(PluginConfigBase):
    """塔罗牌插件完整配置。"""

    plugin: PluginSection = Field(default_factory=PluginSection)
    components: ComponentsSection = Field(default_factory=ComponentsSection)
    proxy: ProxySection = Field(default_factory=ProxySection)
    cards: CardsSection = Field(default_factory=CardsSection)
    adjustment: AdjustmentSection = Field(default_factory=AdjustmentSection)
    permissions: PermissionsSection = Field(default_factory=PermissionsSection)


class TarotsPlugin(MaiBotPlugin):
    """塔罗牌插件。"""

    config_model: ClassVar[type[PluginConfigBase] | None] = TarotsConfig

    def __init__(self) -> None:
        super().__init__()
        self._formations: dict[str, dict[str, Any]] = {}
        self._deck: dict[str, Any] | None = None
        self._using_cards: str = ""
        self._unlocked_decks: list[str] = []
        self._image_cache: dict[tuple[str, str], bytes] = {}

    @classmethod
    def build_config_schema(cls, **plugin_info: str) -> dict[str, Any]:
        """在 SDK Schema 上套用配置页布局（页签 / 中文分组 / 提示）。"""

        return build_config_ui_schema(super().build_config_schema(**plugin_info))

    # ──── 生命周期 ────

    async def on_load(self) -> None:
        """加载牌阵与牌组，记录启动信息。"""

        self.ctx.paths.data_dir.mkdir(parents=True, exist_ok=True)
        self._formations = load_formations()
        self._apply_deck_state()
        self.ctx.logger.info(
            "塔罗牌插件已加载：%d 种牌阵，当前牌组=%s，可用牌组=%s，管理员=%d",
            len(self._formations),
            self._using_cards or "（无）",
            self._allowed_decks(),
            len(self.config.permissions.admin_users),
        )

    async def on_unload(self) -> None:
        """清理缓存。"""

        self._image_cache.clear()
        self.ctx.logger.info("塔罗牌插件已卸载")

    async def on_config_update(self, scope: str, config_data: dict[str, object], version: str) -> None:
        """配置热更新后重载牌组。"""

        del config_data
        self._apply_deck_state()
        self.ctx.logger.info("塔罗牌插件配置已更新：scope=%s version=%s 当前牌组=%s", scope, version, self._using_cards)

    # ──── 抽牌入口：Tool ────

    @Tool(
        "tarot_draw",
        description=(
            "【塔罗占卜专用】当聊天里有人想抽塔罗牌、用塔罗占卜、看运势/吉凶/感情/工作/天气时调用。"
            "会抽 1~7 张牌、发送牌面图片，并用当前人设给出解读。"
            "用户没指定牌阵时用「单张」，没指定范围时用「全部」；只是聊到塔罗而没人想抽牌时不要调用。"
        ),
        parameters=[
            ToolParameterInfo(
                name="formation",
                param_type=ToolParamType.STRING,
                description="牌阵名称；用户没说就用「单张」",
                required=False,
                default=DEFAULT_FORMATION,
                enum_values=list(FALLBACK_FORMATION_NAMES),
            ),
            ToolParameterInfo(
                name="card_type",
                param_type=ToolParamType.STRING,
                description="抽牌范围：全部 / 大阿卡纳 / 小阿卡纳",
                required=False,
                default=DEFAULT_CARD_TYPE,
                enum_values=list(CARD_TYPES),
            ),
            ToolParameterInfo(
                name="question",
                param_type=ToolParamType.STRING,
                description="用户想占卜的问题；没有就留空",
                required=False,
                default="",
            ),
        ],
    )
    async def handle_tarot_tool(
        self,
        formation: str = DEFAULT_FORMATION,
        card_type: str = DEFAULT_CARD_TYPE,
        question: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Tool：抽塔罗牌并发送结果。"""

        stream_id = str(kwargs.get("stream_id") or "").strip()
        if not stream_id:
            return {"success": False, "content": "缺少聊天流 ID，无法发送塔罗牌。"}
        self.ctx.logger.info(
            "tarot_draw 被调用：formation=%s card_type=%s stream_id=%s", formation, card_type, stream_id
        )
        ok = await self._execute_draw(stream_id, formation, card_type, str(question or ""))
        if not ok:
            return {"success": False, "content": "塔罗牌没能发送成功，请告诉用户稍后再试。"}
        return {
            "success": True,
            "content": "塔罗牌已经抽好并发给用户了。",
            "stop_after_execution": True,
        }

    # ──── 关键词兜底 ────

    @HookHandler(
        hook="chat.receive.after_process",
        name="tarots_keyword_trigger",
        description="检测到「塔罗/占卜 + 抽/看/问…」的请求时直接抽牌",
        mode=HookMode.BLOCKING,
        order=HookOrder.EARLY,
    )
    async def handle_keyword_trigger(self, **kwargs: Any) -> dict[str, str] | None:
        """关键词命中时抽牌并中断下游处理；失败则放行。"""

        if not self.config.components.enable_tarots or not self.config.adjustment.enable_keyword_fallback:
            return None
        message = kwargs.get("message")
        if not isinstance(message, dict):
            return None
        raw_text = self._message_text(message)
        if not looks_like_tarot_request(raw_text):
            return None
        stream_id = str(message.get("session_id") or "").strip()
        if not stream_id:
            return None

        formation = parse_formation_from_text(raw_text, self._formations)
        card_type = parse_card_type_from_text(raw_text)
        self.ctx.logger.info("塔罗关键词触发：formation=%s card_type=%s", formation, card_type)
        ok = await self._execute_draw(stream_id, formation, card_type, raw_text)
        return {"action": "abort"} if ok else None

    # ──── 管理指令 ────

    @Command(
        "tarots_command",
        description="/tarots list | /tarots switch <牌组> | /tarots allow <牌组> | /tarots lock <牌组> | /tarots cache（管理员）",
        pattern=_TAROTS_COMMAND_PATTERN,
    )
    async def handle_tarots_command(self, **kwargs: Any) -> tuple[bool, str, int]:
        """处理 /tarots 指令（宿主不会自动发命令返回值，插件自己发）。"""

        stream_id = str(kwargs.get("stream_id") or "").strip()
        user_id = str(kwargs.get("user_id") or "").strip()
        matched_groups = kwargs.get("matched_groups")
        action = str(matched_groups.get("action") or "").strip() if isinstance(matched_groups, dict) else ""
        value = str(matched_groups.get("value") or "").strip() if isinstance(matched_groups, dict) else ""

        if not self.config.components.enable_commands:
            return False, "塔罗插件指令已在配置中关闭", 1
        if not self._is_admin(user_id):
            message = "🔒 权限不足：请让管理员把你的 QQ 号加入插件配置的 [permissions] admin_users。"
            await self._reply_text(stream_id, message)
            return False, message, 1

        if action == "list":
            message = self._deck_list_text()
        elif action == "switch":
            message = await self._switch_deck(value)
        elif action == "allow":
            message = await self._allow_deck(value)
        elif action == "lock":
            message = await self._lock_deck(value)
        elif action == "cache":
            message = await self._cache_all_images(stream_id)
        else:
            message = "用法：/tarots list｜switch <牌组>｜allow <牌组>｜lock <牌组>｜cache"
        await self._reply_text(stream_id, message)
        return True, message, 1

    # ──── 抽牌流程 ────

    async def _execute_draw(self, stream_id: str, formation_name: str, card_type: str, question: str) -> bool:
        """完整抽牌：校验 → 抽牌 → 发图 → 原始文本 → 人设解读。"""

        deck = self._deck
        if deck is None:
            await self._reply_text(stream_id, "⚠️ 没有可用的牌组，请检查插件 tarot_jsons 目录。")
            return False
        formation = self._formations.get(formation_name)
        if formation is None:
            await self._reply_text(stream_id, f"⚠️ 不存在牌阵「{formation_name}」。")
            return False
        card_type = card_type if card_type in CARD_TYPES else DEFAULT_CARD_TYPE
        valid_ids = deck_card_range(deck, card_type)
        if not valid_ids:
            await self._reply_text(stream_id, f"⚠️ 当前牌组没有「{card_type}」范围内的牌。")
            return False
        cards_num = min(formation["cards_num"], len(valid_ids))
        selected_ids = random.sample(valid_ids, cards_num)
        cards = [
            (card_id, random.random() < 0.5 if formation["is_cut"] else False)
            for card_id in selected_ids
        ]

        image_failures: list[str] = []
        for card_id, is_reversed in cards:
            image_bytes = await self._get_card_image(card_id, is_reversed)
            if image_bytes is None:
                card_name = str(deck["cards"][card_id].get("name") or card_id)
                image_failures.append(card_name)
                continue
            await self._send_image(image_bytes, stream_id)
            await asyncio.sleep(0.2)

        if image_failures:
            if self.config.adjustment.strict_images:
                await self._reply_text(
                    stream_id,
                    f"⚠️ 以下牌面获取失败，占卜中止：{'、'.join(image_failures)}\n"
                    "可以稍后重试，或先发 /tarots cache 缓存当前牌组。",
                )
                return False
            self.ctx.logger.warning("卡面缺失，降级为纯文字：%s", image_failures)

        raw_text = build_result_text(formation_name, self._using_cards, cards, formation, deck)
        if self.config.adjustment.enable_original_text:
            await self._reply_text(stream_id, raw_text)

        material = build_persona_material(formation_name, cards, formation, deck, question)
        persona_text = await self._compose_persona_text(stream_id, material, raw_text)
        # 发送只尝试一次：平台回执超时但消息已送达的情况很常见，重发会造成重复发图。
        await self._reply_text(stream_id, persona_text)
        return True

    async def _compose_persona_text(self, stream_id: str, material: str, fallback: str) -> str:
        """用宿主人设生成解读；失败时退回原始牌面文本。"""

        try:
            result = await self.ctx.maisaka.reply.generate(
                stream_id,
                reference=material,
                format_instruction=PERSONA_INSTRUCTION,
                timeout_ms=90000,
            )
        except Exception:
            self.ctx.logger.warning("塔罗人设解读生成失败，使用原始文本", exc_info=True)
            return fallback
        if isinstance(result, dict) and result.get("success"):
            text = str(result.get("text") or result.get("response") or "").strip()
            if text:
                return text
        self.ctx.logger.warning("塔罗人设解读返回空内容，使用原始文本：%s", result)
        return fallback

    # ──── 图片下载与缓存 ────

    async def _get_card_image(self, card_id: str, is_reversed: bool) -> bytes | None:
        """取卡面图（缓存优先）；逆位旋转 180°。"""

        source = await self._ensure_cached_image(card_id)
        if source is None:
            return None
        if not is_reversed:
            return source
        rotated = rotate_image_180(source)
        return rotated if rotated is not None else source

    async def _ensure_cached_image(self, card_id: str) -> bytes | None:
        """确保卡面已缓存，返回图片字节。"""

        cache_key = (self._using_cards, card_id)
        cached = self._image_cache.get(cache_key)
        if cached is not None:
            return cached
        if self._uses_local_images():
            local_path = self._local_image_path(card_id)
            if local_path is None:
                self.ctx.logger.warning("本地卡面缺失：deck=%s card=%s", self._using_cards, card_id)
                return None
            try:
                data = local_path.read_bytes()
            except OSError:
                self.ctx.logger.warning("本地卡面读取失败：%s", local_path, exc_info=True)
                return None
            self._image_cache[cache_key] = data
            return data
        cache_path = self._cache_path(card_id)
        if cache_path.is_file() and cache_path.stat().st_size > 0:
            try:
                data = cache_path.read_bytes()
            except OSError:
                data = b""
            if data:
                self._image_cache[cache_key] = data
                return data
        data = await asyncio.to_thread(self._download_image, card_id, cache_path)
        if data is None:
            return None
        self._image_cache[cache_key] = data
        return data

    def _uses_local_images(self) -> bool:
        """牌组是否直接使用仓库内置的本地卡面（不联网下载）。"""

        deck = self._deck
        if deck is None:
            return False
        value = deck["meta"].get("local_images")
        return value is True or str(value).strip().lower() == "true"

    def _local_image_path(self, card_id: str) -> Path | None:
        """解析本地卡面路径，并阻止路径穿越。"""

        deck = self._deck
        if deck is None:
            return None
        card = deck["cards"].get(card_id)
        if not isinstance(card, dict):
            return None
        info = card.get("info") if isinstance(card.get("info"), dict) else {}
        relative = str(info.get("imgUrl") or "").strip().lstrip("/\\")
        if not relative:
            return None
        base_dir = (TAROT_JSON_DIR / self._using_cards).resolve()
        path = (base_dir / relative).resolve()
        if path != base_dir and base_dir not in path.parents:
            return None
        return path if path.is_file() else None

    def _cache_dir(self) -> Path:
        return self.ctx.paths.data_dir / "tarots_cache" / (self._using_cards or "default")

    def _cache_path(self, card_id: str) -> Path:
        return self._cache_dir() / f"{card_id}_norm.png"

    def _download_url(self, card_id: str) -> str | None:
        deck = self._deck
        if deck is None:
            return None
        card = deck["cards"].get(card_id)
        if not isinstance(card, dict):
            return None
        info = card.get("info") if isinstance(card.get("info"), dict) else {}
        image_path = str(info.get("imgUrl") or "").strip()
        base_url = str(deck["meta"].get("base_url") or "").strip()
        if not image_path or not base_url:
            return None
        return f"{base_url}{image_path}"

    def _download_image(self, card_id: str, save_path: Path) -> bytes | None:
        """同步下载卡面（在线程中执行），返回字节并写入缓存。"""

        url = self._download_url(card_id)
        if not url:
            self.ctx.logger.warning("牌组缺少卡面地址：card=%s", card_id)
            return None
        save_path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(1, IMAGE_DOWNLOAD_RETRIES + 1):
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "MaiBot-Tarots-Plugin/2.0"})
                if self.config.proxy.enable_proxy and self.config.proxy.proxy_url:
                    opener = urllib.request.build_opener(
                        urllib.request.ProxyHandler({"http": self.config.proxy.proxy_url, "https": self.config.proxy.proxy_url})
                    )
                    response = opener.open(request, timeout=IMAGE_DOWNLOAD_TIMEOUT)
                else:
                    response = urllib.request.urlopen(request, timeout=IMAGE_DOWNLOAD_TIMEOUT)
                with response:
                    data = response.read(IMAGE_MAX_BYTES + 1)
                if len(data) > IMAGE_MAX_BYTES:
                    raise ValueError("图片超过大小限制")
                if not data:
                    raise ValueError("图片内容为空")
                save_path.write_bytes(data)
                self.ctx.logger.info("卡面已缓存：%s（第 %d 次尝试）", save_path.name, attempt)
                return data
            except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
                self.ctx.logger.warning("卡面下载失败（第 %d/%d 次）：%s %s", attempt, IMAGE_DOWNLOAD_RETRIES, card_id, exc)
                if attempt < IMAGE_DOWNLOAD_RETRIES:
                    time.sleep(2 ** (attempt - 1))
        self.ctx.logger.error("卡面下载最终失败：%s %s", card_id, url)
        return None

    # ──── 牌组管理 ────

    def _read_state(self) -> dict[str, Any]:
        state_path = self.ctx.paths.data_dir / "state.json"
        data = load_json(state_path)
        return data or {}

    def _save_state(self) -> None:
        state_path = self.ctx.paths.data_dir / "state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(
                {"using_cards": self._using_cards, "unlocked_decks": self._unlocked_decks},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _apply_deck_state(self) -> None:
        """从 state.json 恢复当前牌组与已自助解锁的牌组。"""

        state = self._read_state()
        unlocked = state.get("unlocked_decks")
        self._unlocked_decks = [str(item).strip() for item in unlocked] if isinstance(unlocked, list) else []
        self._using_cards = self._resolve_using_cards()
        self._deck = load_deck(self._using_cards) if self._using_cards else None

    def _allowed_decks(self) -> list[str]:
        """当前可用的牌组 = 配置里启用的 + 管理员自助确认解锁的。"""

        available = set(available_deck_ids())
        allowed: list[str] = []
        for deck_id in [*self.config.cards.use_cards, *self._unlocked_decks]:
            if deck_id in available and deck_id not in allowed:
                allowed.append(deck_id)
        return allowed

    def _resolve_using_cards(self) -> str:
        allowed = self._allowed_decks()
        if not allowed:
            return ""
        state_deck = str(self._read_state().get("using_cards") or "").strip()
        if state_deck in allowed:
            return state_deck
        configured = str(self.config.cards.using_cards or "").strip()
        if configured in allowed:
            return configured
        return allowed[0]

    def _deck_notice(self, deck: dict[str, Any]) -> str:
        """牌组的版权/使用提示（来自 deck.json 的 _meta.notice）。"""

        return str(deck["meta"].get("notice") or "").strip()

    def _deck_list_text(self) -> str:
        allowed = self._allowed_decks()
        available = available_deck_ids()
        ordered = [deck for deck in allowed]
        if self._using_cards in ordered:
            ordered.remove(self._using_cards)
            ordered.insert(0, self._using_cards)
        locked = [deck for deck in available if deck not in allowed]

        blocks: list[str] = [f"{TAROT_ICON} 塔罗牌组（{len(allowed)} 套已启用）", "━━━━━━━━━━━━━━"]
        for deck_id in ordered:
            deck = load_deck(deck_id)
            if deck is None:
                continue
            name = deck_display_name(deck_id, deck)
            mark = "✅" if deck_id == self._using_cards else "⚪"
            suffix = " ← 当前" if deck_id == self._using_cards else ""
            blocks.append(f"{mark} {name}{suffix}")

        if locked:
            blocks.append("")
            blocks.append(f"🔒 未启用牌组（{len(locked)} 套）")
            blocks.append("━━━━━━━━━━━━━━")
            for deck_id in locked:
                deck = load_deck(deck_id)
                if deck is None:
                    continue
                lines = [f"🔒 {deck_display_name(deck_id, deck)}"]
                notice = self._deck_notice(deck)
                if notice:
                    lines.append(f"   ⚠️ {notice}")
                lines.append(f"   ↳ 确认你有使用权：/tarots allow {deck_id}")
                blocks.append("\n".join(lines))

        blocks.append("")
        blocks.append("切换：/tarots switch <牌组名>")
        blocks.append("缓存：/tarots cache ｜ 撤销解锁：/tarots lock <牌组名>")
        return "\n".join(blocks)

    async def _allow_deck(self, deck_id: str) -> str:
        """管理员自助确认授权后启用带锁牌组（插件无法验证授权真实性）。"""

        if not deck_id:
            return "用法：/tarots allow <牌组名>，先用 /tarots list 查看带锁牌组。"
        if deck_id not in available_deck_ids():
            return f"⚠️ 找不到牌组「{deck_id}」。"
        deck = load_deck(deck_id)
        notice = self._deck_notice(deck) if deck is not None else ""
        if deck_id in self._allowed_decks():
            return f"✅ 「{deck_id}」已经可用，直接 /tarots switch {deck_id} 即可。"
        if deck_id not in self._unlocked_decks:
            self._unlocked_decks.append(deck_id)
            self._save_state()
        lines = [f"✅ 已记录：操作者确认「{deck_id}」有权使用（非商业允许场景 / 已获授权）。"]
        if notice:
            lines.append(f"📌 版权提示：{notice}")
        lines.append(f"现在可以：/tarots switch {deck_id} 或 /tarots cache")
        lines.append("（插件无法验证授权真实性，此确认仅代表操作者自行确认；如需撤销用 /tarots lock）")
        return "\n".join(lines)

    async def _lock_deck(self, deck_id: str) -> str:
        """撤销自助解锁。"""

        if not deck_id:
            return "用法：/tarots lock <牌组名>"
        if deck_id in self.config.cards.use_cards:
            return f"「{deck_id}」属于配置 [cards] use_cards 里的默认启用牌组，请改配置来关闭，不能在这里锁定。"
        if deck_id not in self._unlocked_decks:
            return f"「{deck_id}」当前没有解锁，无需锁定。"
        self._unlocked_decks.remove(deck_id)
        if self._using_cards == deck_id:
            allowed = self._allowed_decks()
            self._using_cards = allowed[0] if allowed else ""
            self._deck = load_deck(self._using_cards) if self._using_cards else None
        self._save_state()
        return f"🔒 已锁定「{deck_id}」，如需再次启用请重新 /tarots allow {deck_id}。"

    async def _switch_deck(self, deck_id: str) -> str:
        if not deck_id:
            return "用法：/tarots switch <牌组名>，可先用 /tarots list 查看。"
        if deck_id not in self._allowed_decks():
            deck = load_deck(deck_id)
            notice = self._deck_notice(deck) if deck is not None else ""
            reason = notice or "它不存在，或者还没启用。"
            return (
                f"🔒 牌组「{deck_id}」默认锁定：{reason}\n"
                f"确认你有使用权后，先执行 /tarots allow {deck_id}，再 /tarots switch {deck_id}。"
            )
        deck = load_deck(deck_id)
        if deck is None:
            return f"⚠️ 牌组「{deck_id}」读取失败。"
        self._using_cards = deck_id
        self._deck = deck
        self._image_cache.clear()
        self._save_state()
        self.ctx.logger.info("塔罗牌组已切换为：%s", deck_id)
        return f"✅ 已切换牌组：{deck_display_name(deck_id, deck)}"

    async def _cache_all_images(self, stream_id: str) -> str:
        deck = self._deck
        if deck is None:
            return "⚠️ 没有可用的牌组。"
        card_ids = list(deck["cards"].keys())
        await self._reply_text(stream_id, f"⏳ 开始缓存「{self._using_cards}」牌组的 {len(card_ids)} 张牌面，请稍候…")
        success = 0
        for card_id in card_ids:
            if await self._ensure_cached_image(card_id) is not None:
                success += 1
        return f"✅ 缓存完成：成功 {success}/{len(card_ids)} 张牌面（牌组 {self._using_cards}）"

    # ──── 发送与权限 ────

    def _message_text(self, message: dict[str, Any]) -> str:
        for key in ("processed_plain_text", "plain_text", "raw_message"):
            value = message.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _is_admin(self, user_id: str) -> bool:
        admins = {str(item).strip() for item in self.config.permissions.admin_users}
        return bool(user_id) and user_id in admins

    async def _reply_text(self, stream_id: str, text: str) -> bool:
        """发文字；只尝试一次（回执超时不重发，避免重复消息）。"""

        if not stream_id or not text.strip():
            return False
        try:
            result = await self.ctx.send.text(text, stream_id, return_details=True)
            sent = bool(result.get("sent")) if isinstance(result, dict) else bool(result)
        except Exception:
            self.ctx.logger.warning("塔罗文字发送异常", exc_info=True)
            return False
        if not sent:
            self.ctx.logger.warning("塔罗文字未确认送达（按已发送处理，不重发）：stream_id=%s", stream_id)
        return sent

    async def _send_image(self, image_bytes: bytes, stream_id: str) -> bool:
        """发图片；只尝试一次，避免回执超时导致重复发图。"""

        image_base64 = base64.b64encode(image_bytes).decode("ascii")
        try:
            result = await self.ctx.send.image(image_base64, stream_id, return_details=True)
            sent = bool(result.get("sent")) if isinstance(result, dict) else bool(result)
        except Exception:
            self.ctx.logger.warning("塔罗图片发送异常", exc_info=True)
            return False
        if not sent:
            self.ctx.logger.warning("塔罗图片未确认送达（按已发送处理，不重发）：stream_id=%s", stream_id)
        return sent


def create_plugin() -> TarotsPlugin:
    """创建插件实例。"""

    return TarotsPlugin()
