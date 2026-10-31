"""塔罗工坊（塔罗牌插件 v2）— 为 MaiBot 1.3.x / 插件 SDK 2.x。

插件入口：生命周期、Tool / Hook / Command 组件与发送流程。
牌阵、牌组、布局图等纯逻辑在 tarots_core.py，配置模型在 config.py，
WebUI 配置页布局在 config_ui.py。
"""

import asyncio
import base64
import json
import random
import re
import time
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any, ClassVar

from maibot_sdk import Command, HookHandler, MaiBotPlugin, PluginConfigBase, Tool
from maibot_sdk.types import HookMode, HookOrder, ToolParameterInfo, ToolParamType

from . import tarots_core
from .config import TarotsConfig
from .config_ui import build_config_ui_schema
from .tarots_core import (
    CARD_TYPES,
    DAILY_INSTRUCTION,
    DEFAULT_CARD_TYPE,
    DEFAULT_FORMATION,
    FOLLOWUP_INSTRUCTION,
    IMAGE_DOWNLOAD_RETRIES,
    IMAGE_DOWNLOAD_TIMEOUT,
    IMAGE_MAX_BYTES,
    LAST_DRAW_TTL,
    PERSONA_INSTRUCTION,
    TAROT_ICON,
    available_deck_ids,
    build_persona_material,
    build_result_text,
    compose_spread_image,
    deck_card_range,
    deck_display_name,
    is_daily_request,
    is_followup_text,
    load_custom_formations,
    load_deck,
    load_formations,
    load_json,
    looks_like_tarot_request,
    parse_card_type_from_text,
    parse_formation_from_text,
    rotate_image_180,
)


_TAROTS_COMMAND_PATTERN = (
    r"/tarots\s+(?P<action>cache|switch|list|allow|lock|daily|formations|newformation)"
    r"(?:\s+(?P<value>\S+))?(?:\s+(?P<rest>.*))?"
)



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
        self._last_draws: dict[str, dict[str, Any]] = {}

    @classmethod
    def build_config_schema(cls, **plugin_info: str) -> dict[str, Any]:
        """在 SDK Schema 上套用配置页布局（页签 / 中文分组 / 提示）。"""

        return build_config_ui_schema(super().build_config_schema(**plugin_info))

    # ──── 生命周期 ────

    async def on_load(self) -> None:
        """加载牌阵与牌组，记录启动信息。"""

        self.ctx.paths.data_dir.mkdir(parents=True, exist_ok=True)
        self._reload_formations()
        self._apply_deck_state()
        self.ctx.logger.info(
            "塔罗工坊已加载：%d 种牌阵（含自定义），当前牌组=%s，可用牌组=%s，管理员=%d",
            len(self._formations),
            self._using_cards or "（无）",
            self._allowed_decks(),
            len(self.config.permissions.admin_users),
        )

    async def on_unload(self) -> None:
        """清理缓存。"""

        self._image_cache.clear()
        self.ctx.logger.info("塔罗工坊已卸载")

    async def on_config_update(self, scope: str, config_data: dict[str, object], version: str) -> None:
        """配置热更新后重载牌组。"""

        del config_data
        self._reload_formations()
        self._apply_deck_state()
        self.ctx.logger.info("塔罗工坊配置已更新：scope=%s version=%s 当前牌组=%s", scope, version, self._using_cards)

    # ──── 抽牌入口：Tool ────

    @Tool(
        "tarot_draw",
        description=(
            "【塔罗占卜专用】当聊天里有人想抽塔罗牌、用塔罗占卜、看运势/吉凶/感情/工作/天气时调用。"
            "会按牌阵抽 1~10 张牌、发送牌阵布局图，并用当前人设给出解读。"
            "常用牌阵：单张、圣三角、时间之流、四要素、五牌阵、吉普赛十字、马蹄、六芒星、凯尔特十字、二选一、身心灵；"
            "用户没指定牌阵时用「单张」，没指定范围时用「全部」；只是聊到塔罗而没人想抽牌时不要调用。"
        ),
        parameters=[
            ToolParameterInfo(
                name="formation",
                param_type=ToolParamType.STRING,
                description=(
                    "牌阵名称，例如：单张、圣三角、时间之流、四要素、五牌阵、吉普赛十字、马蹄、六芒星；"
                    "也支持用户自定义牌阵名。用户没说就用「单张」"
                ),
                required=False,
                default=DEFAULT_FORMATION,
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

    # ──── 关键词兜底 / 追问 / 每日一牌 ────

    @HookHandler(
        hook="chat.receive.after_process",
        name="tarots_keyword_trigger",
        description="检测到塔罗请求、占卜追问或每日一牌时接管处理",
        mode=HookMode.BLOCKING,
        order=HookOrder.EARLY,
    )
    async def handle_keyword_trigger(self, **kwargs: Any) -> dict[str, str] | None:
        """命中追问 / 每日一牌 / 塔罗请求时处理并中断下游；失败则放行。"""

        if not self.config.components.enable_tarots or not self.config.adjustment.enable_keyword_fallback:
            return None
        message = kwargs.get("message")
        if not isinstance(message, dict):
            return None
        raw_text = self._message_text(message)
        stream_id = str(message.get("session_id") or "").strip()
        if not stream_id or not raw_text:
            return None

        if not raw_text.startswith("/"):
            followup = self._match_followup(stream_id, raw_text)
            if followup is not None:
                self.ctx.logger.info("塔罗追问触发：stream_id=%s", stream_id)
                ok = await self._handle_followup(stream_id, raw_text, followup)
                return {"action": "abort"} if ok else None
            if self._looks_like_daily(raw_text):
                self.ctx.logger.info("每日一牌触发：stream_id=%s", stream_id)
                ok = await self._daily_draw(stream_id, str(message.get("user_id") or ""))
                return {"action": "abort"} if ok else None

        if not looks_like_tarot_request(raw_text):
            return None
        formation = parse_formation_from_text(raw_text, self._formations)
        card_type = parse_card_type_from_text(raw_text)
        self.ctx.logger.info("塔罗关键词触发：formation=%s card_type=%s", formation, card_type)
        ok = await self._execute_draw(stream_id, formation, card_type, raw_text)
        return {"action": "abort"} if ok else None

    # ──── 指令 ────

    @Command(
        "tarots_command",
        description="/tarots daily | formations（所有人）｜ list / switch / allow / lock / cache / newformation（管理员）",
        pattern=_TAROTS_COMMAND_PATTERN,
    )
    async def handle_tarots_command(self, **kwargs: Any) -> tuple[bool, str, int]:
        """处理 /tarots 指令（宿主不会自动发命令返回值，插件自己发）。"""

        stream_id = str(kwargs.get("stream_id") or "").strip()
        user_id = str(kwargs.get("user_id") or "").strip()
        matched_groups = kwargs.get("matched_groups")
        action = str(matched_groups.get("action") or "").strip() if isinstance(matched_groups, dict) else ""
        value = str(matched_groups.get("value") or "").strip() if isinstance(matched_groups, dict) else ""
        rest = str(matched_groups.get("rest") or "").strip() if isinstance(matched_groups, dict) else ""

        if not self.config.components.enable_commands:
            return False, "塔罗插件指令已在配置中关闭", 1
        # 这几个指令对所有人开放，其余仅管理员可用
        if action not in ("daily", "formations") and not self._is_admin(user_id):
            message = "🔒 权限不足：请让管理员把你的 QQ 号加入插件配置的 [permissions] admin_users。"
            await self._reply_text(stream_id, message)
            return False, message, 1

        skip_send = False
        if action == "list":
            message = self._deck_list_text()
        elif action == "formations":
            message = self._formations_text()
        elif action == "newformation":
            message = await self._new_formation(value, rest)
        elif action == "switch":
            message = await self._switch_deck(value)
        elif action == "allow":
            message = await self._allow_deck(value)
        elif action == "lock":
            message = await self._lock_deck(value)
        elif action == "cache":
            message = await self._cache_all_images(stream_id)
        elif action == "daily":
            ok = await self._daily_draw(stream_id, user_id)
            message = "🎴 今日一牌已发送。" if ok else "⚠️ 今日一牌没发出去，稍后再试。"
            skip_send = ok
        else:
            message = "用法：/tarots daily｜list｜switch <牌组>｜allow <牌组>｜lock <牌组>｜cache"

        if not skip_send:
            await self._reply_text(stream_id, message)
        if action == "list":
            await self._send_deck_thumbnail(stream_id)
        return True, message, 1

    # ──── 抽牌流程 ────

    def _reload_formations(self) -> None:
        """内置牌阵 + data 目录自定义牌阵（需重启或配置更新生效）。"""

        builtin = load_formations()
        custom = load_custom_formations(self.ctx.paths.data_dir)
        self._formations = {**builtin, **custom}

    async def _execute_draw(
        self,
        stream_id: str,
        formation_name: str,
        card_type: str,
        question: str,
        seed: str | None = None,
        mode: str = "normal",
    ) -> bool:
        """完整抽牌：校验 → 抽牌 → 布局图/牌面 → 原始文本 → 人设解读。"""

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
        rng = random.Random(seed) if seed else random
        cards_num = min(formation["cards_num"], len(valid_ids))
        selected_ids = rng.sample(valid_ids, cards_num)
        cards = [
            (card_id, rng.random() < 0.5 if formation["is_cut"] else False)
            for card_id in selected_ids
        ]
        positions = formation.get("represent") or []

        images: list[bytes | None] = []
        image_failures: list[str] = []
        for card_id, is_reversed in cards:
            image_bytes = await self._get_card_image(card_id, is_reversed)
            images.append(image_bytes)
            if image_bytes is None:
                image_failures.append(str(deck["cards"][card_id].get("name") or card_id))

        if image_failures:
            if self.config.adjustment.strict_images:
                await self._reply_text(
                    stream_id,
                    f"⚠️ 以下牌面获取失败，占卜中止：{'、'.join(image_failures)}\n"
                    "可以稍后重试，或先发 /tarots cache 缓存当前牌组。",
                )
                return False
            self.ctx.logger.warning("卡面缺失，降级为纯文字：%s", image_failures)

        entries = [
            (
                image_bytes,
                str(deck["cards"][card_id].get("name") or card_id),
                positions[index] if index < len(positions) else f"位置{index + 1}",
            )
            for index, ((card_id, _), image_bytes) in enumerate(zip(cards, images))
            if image_bytes is not None
        ]
        composite = None
        use_layout = self.config.adjustment.layout_image and (
            len(cards) > 1 or not self.config.adjustment.single_card_plain
        )
        if use_layout and len(entries) == len(cards) and entries:
            composite = compose_spread_image(formation_name, entries)
        payloads: list[bytes] = [composite] if composite is not None else [item for item in images if item is not None]

        raw_text = build_result_text(formation_name, self._using_cards, cards, formation, deck)
        material = build_persona_material(formation_name, cards, formation, deck, question)
        instruction = DAILY_INSTRUCTION if mode == "daily" else None
        persona_text = await self._compose_persona_text(stream_id, material, raw_text, instruction)

        # 默认合并成一条转发消息，减少刷屏；转发失败自动回退到分条发送
        sent = False
        if self.config.adjustment.merged_message:
            sent = await self._send_merged(
                stream_id, formation_name, payloads, raw_text, persona_text
            )
        if not sent:
            for payload in payloads:
                await self._send_image(payload, stream_id)
                await asyncio.sleep(0.2)
            if self.config.adjustment.enable_original_text:
                await self._reply_text(stream_id, raw_text)
            # 发送只尝试一次：平台回执超时但消息已送达的情况很常见，重发会造成重复发图。
            await self._reply_text(stream_id, persona_text)
        self._remember_draw(stream_id, formation_name, cards, formation, deck, question, material, raw_text, persona_text)
        return True

    async def _send_merged(
        self,
        stream_id: str,
        formation_name: str,
        images: Sequence[bytes],
        raw_text: str,
        persona_text: str,
    ) -> bool:
        """把标题 / 牌面图 / 牌义 / 人设解读合成一条合并转发消息发送。"""

        nodes: list[dict[str, Any]] = [
            {
                "nickname": "塔罗工坊",
                "segments": [
                    {
                        "type": "text",
                        "content": f"{TAROT_ICON} {formation_name}牌阵 · {self._using_cards} 牌组",
                    }
                ],
            }
        ]
        if images:
            nodes.append(
                {
                    "nickname": "牌面",
                    "segments": [
                        {"type": "image", "content": base64.b64encode(image).decode("ascii")}
                        for image in images
                    ],
                }
            )
        nodes.append({"nickname": "牌义", "segments": [{"type": "text", "content": raw_text}]})
        if persona_text.strip() and persona_text.strip() != raw_text.strip():
            nodes.append({"nickname": "解读", "segments": [{"type": "text", "content": persona_text}]})
        try:
            result = await self.ctx.send.forward(nodes, stream_id, return_details=True)
            sent = bool(result.get("sent")) if isinstance(result, dict) else bool(result)
        except Exception:
            self.ctx.logger.warning("合并消息发送异常，回退到分条发送", exc_info=True)
            return False
        if not sent:
            self.ctx.logger.warning("合并消息未确认送达，回退到分条发送：stream_id=%s", stream_id)
            return False
        return True

    def _remember_draw(
        self,
        stream_id: str,
        formation_name: str,
        cards: Sequence[tuple[str, bool]],
        formation: dict[str, Any],
        deck: dict[str, Any],
        question: str,
        material: str,
        raw_text: str,
        persona_text: str,
    ) -> None:
        """记录最近一次抽牌，供 10 分钟内的追问使用。"""

        now = time.time()
        for key in [key for key, value in self._last_draws.items() if now - value["ts"] > LAST_DRAW_TTL]:
            self._last_draws.pop(key, None)
        positions = formation.get("represent") or []
        detail = []
        for index, (card_id, is_reversed) in enumerate(cards):
            card = deck["cards"][card_id]
            info = card.get("info") if isinstance(card.get("info"), dict) else {}
            description = str(
                (info.get("reverseDescription") if is_reversed else info.get("description")) or ""
            )
            detail.append(
                {
                    "position": positions[index] if index < len(positions) else f"位置{index + 1}",
                    "name": str(card.get("name") or card_id),
                    "reversed": is_reversed,
                    "description": description,
                }
            )
        self._last_draws[stream_id] = {
            "ts": now,
            "formation": formation_name,
            "deck": self._using_cards,
            "question": question,
            "cards": detail,
            "material": material,
            "raw_text": raw_text,
            "persona_text": persona_text,
        }

    def _match_followup(self, stream_id: str, text: str) -> dict[str, Any] | None:
        """判断这句话是不是在追问上一次抽牌。"""

        record = self._last_draws.get(stream_id)
        if record is None:
            return None
        if time.time() - record["ts"] > LAST_DRAW_TTL:
            self._last_draws.pop(stream_id, None)
            return None
        return record if is_followup_text(text) else None

    async def _handle_followup(self, stream_id: str, text: str, record: dict[str, Any]) -> bool:
        """基于上一次抽牌回答追问，不重新抽牌。"""

        material = f"{record['material']}\n用户追问：{text}"
        persona = await self._compose_persona_text(
            stream_id, material, record["raw_text"], FOLLOWUP_INSTRUCTION
        )
        return await self._reply_text(stream_id, persona)

    def _looks_like_daily(self, text: str) -> bool:
        """判断是否是每日一牌/今日运势请求。"""

        return is_daily_request(text)

    async def _daily_draw(self, stream_id: str, user_id: str = "") -> bool:
        """每日一牌：同一天同一用户抽到同一张，可重复查看。"""

        today = date.today().isoformat()
        seed = f"{today}:{user_id or stream_id}"
        formation = DEFAULT_FORMATION if DEFAULT_FORMATION in self._formations else next(iter(self._formations), "")
        if not formation:
            await self._reply_text(stream_id, "⚠️ 没有可用的牌阵。")
            return False
        return await self._execute_draw(
            stream_id, formation, "全部", f"今日运势（{today}）", seed=seed, mode="daily"
        )

    async def _compose_persona_text(
        self,
        stream_id: str,
        material: str,
        fallback: str,
        instruction: str | None = None,
    ) -> str:
        """用宿主人设生成解读；失败时退回原始牌面文本。"""

        try:
            result = await self.ctx.maisaka.reply.generate(
                stream_id,
                reference=material,
                format_instruction=instruction or PERSONA_INSTRUCTION,
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
        base_dir = (tarots_core.TAROT_JSON_DIR / self._using_cards).resolve()
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

    async def _new_formation(self, name: str, rest: str) -> str:
        """在群里直接新增自定义牌阵（写入 data/formations.json 并即时生效）。"""

        usage = "用法：/tarots newformation <牌阵名> <张数> <位置1|位置2|…>，例如：/tarots newformation 我的三连 3 起因|经过|结果"
        if not name:
            return usage
        name = name.strip()[:20]
        if name in load_formations():
            return f"「{name}」是内置牌阵，不能覆盖；换个名字吧。"
        parts = rest.split(None, 1)
        if not parts or not parts[0].isdigit():
            return usage
        cards_num = int(parts[0])
        if not 1 <= cards_num <= 10:
            return "张数需要在 1~10 之间。"
        raw_positions = parts[1] if len(parts) > 1 else ""
        positions = [item.strip() for item in re.split(r"[|｜/,，、;；]", raw_positions) if item.strip()]
        while len(positions) < cards_num:
            positions.append(f"位置{len(positions) + 1}")
        positions = positions[:cards_num]

        path = self.ctx.paths.data_dir / "formations.json"
        data = load_json(path) or {}
        data[name] = {"cards_num": cards_num, "is_cut": True, "represent": [positions]}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self._reload_formations()
        self.ctx.logger.info("新增自定义牌阵：%s（%d 张）", name, cards_num)
        return (
            f"✅ 已新增牌阵「{name}」（{cards_num} 张）\n"
            f"牌位：{' / '.join(positions)}\n"
            f"现在可以直接说「用{name}占卜一下」，或用 /tarots formations 查看。"
        )

    def _formations_text(self) -> str:
        """列出所有可用牌阵（含自定义）。"""

        blocks = [f"🔮 可用牌阵（{len(self._formations)} 种）", "━━━━━━━━━━━━━━"]
        for name, formation in self._formations.items():
            custom = " · 自定义" if formation.get("custom") else ""
            positions = " / ".join(formation.get("represent") or [])
            cut = "可逆位" if formation.get("is_cut") else "全正位"
            blocks.append(f"🃏 {name}（{formation['cards_num']} 张 · {cut}{custom}）\n   {positions}")
        blocks.append("")
        blocks.append("用法：直接说「用<牌阵名>占卜…」，例如「用六芒星帮我看看最近运势」")
        blocks.append("也可以说「抽三张」「过去现在未来」「凯尔特十字」等常见说法")
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

    async def _send_deck_thumbnail(self, stream_id: str) -> None:
        """给 /tarots list 附一张当前牌组的预览图。"""

        if not self.config.adjustment.show_deck_thumbnail or self._deck is None:
            return
        cards = self._deck["cards"]
        card_id = "0" if "0" in cards else next(iter(cards), None)
        if card_id is None:
            return
        image = await self._get_card_image(card_id, False)
        if image is not None:
            await self._send_image(image, stream_id)

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
