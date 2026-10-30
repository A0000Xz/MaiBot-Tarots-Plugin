"""整链路单元测试：假 ctx 跑 Tool / 关键词兜底 / 管理指令。"""

from __future__ import annotations

import base64
import importlib.util
import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path
from typing import Any
from unittest import mock

from maibot_sdk import PluginContext, PluginPaths


PLUGIN_DIR = Path(__file__).resolve().parents[1]
MODULE_NAME = "maibot_tarots_plugin"
PLUGIN_ID = "github.a0000xz.maibot-tarots-plugin"
RUN_TMP_ROOT = Path(__file__).resolve().parent / "_tmp"
ADMIN_QQ = "10001"
PERSONA_TEXT = "麦麦用自己的人设说出的解牌"
# 合法的最小 PNG（1x1），用于替代真实卡面，避免测试联网
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def load_plugin_module() -> Any:
    cached = sys.modules.get(MODULE_NAME)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME,
        PLUGIN_DIR / "plugin.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载塔罗插件模块")
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


PLUGIN = load_plugin_module()


class FakeHarness:
    """替换底层 RPC，记录 capability 调用。"""

    def __init__(self, reply_fail: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.reply_fail = reply_fail

    async def rpc(
        self,
        method: str,
        plugin_id: str,
        payload: dict[str, Any],
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        del method, plugin_id, timeout_ms
        capability = str(payload.get("capability"))
        args = dict(payload.get("args") or {})
        self.calls.append((capability, args))
        if capability == "send.text":
            if args.get("text") == "":
                return {"success": False, "sent": False}
            return {"success": True, "sent": True, "message_id": f"m{len(self.calls)}"}
        if capability == "send.image":
            return {"success": True, "sent": True, "message_id": f"m{len(self.calls)}"}
        if capability == "maisaka.reply.generate":
            if self.reply_fail:
                return {"success": False, "error": "fake failure"}
            return {"success": True, "text": PERSONA_TEXT}
        return {"success": True}

    def capabilities(self) -> list[str]:
        return [capability for capability, _ in self.calls]

    def args_of(self, capability: str) -> list[dict[str, Any]]:
        return [args for name, args in self.calls if name == capability]


class PluginTestCase(unittest.IsolatedAsyncioTestCase):
    """构造带假 ctx、默认配置并完成 on_load 的插件实例。"""

    def setUp(self) -> None:
        self._tmp_dir = RUN_TMP_ROOT / uuid.uuid4().hex
        self._tmp_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, self._tmp_dir, True)

    async def make_plugin(
        self,
        harness: FakeHarness | None = None,
        admin: tuple[str, ...] = (),
        use_cards: tuple[str, ...] = ("classical",),
        **overrides: Any,
    ) -> tuple[Any, FakeHarness]:
        harness = harness or FakeHarness()
        plugin = PLUGIN.TarotsPlugin()
        ctx = PluginContext(
            PLUGIN_ID,
            rpc_call=harness.rpc,
            paths=PluginPaths(data_dir=self._tmp_dir / "data", runtime_dir=self._tmp_dir / "runtime"),
        )
        plugin._set_context(ctx)
        config = plugin.build_default_config()
        config["cards"]["use_cards"] = list(use_cards)
        config["permissions"]["admin_users"] = list(admin)
        config["adjustment"]["show_deck_thumbnail"] = False
        for section, values in overrides.items():
            config[section].update(values)
        plugin.set_plugin_config(config)
        await plugin.on_load()
        return plugin, harness

    @staticmethod
    def speed_up_sleep() -> Any:
        return mock.patch.object(PLUGIN.asyncio, "sleep", new=mock.AsyncMock())

    @staticmethod
    def patch_sample(count: int) -> Any:
        return mock.patch.object(
            PLUGIN.random, "sample", side_effect=lambda seq, n: list(seq)[: min(n, count)]
        )


class ToolTest(PluginTestCase):
    """tarot_draw Tool。"""

    async def test_single_draw(self) -> None:
        plugin, harness = await self.make_plugin()
        with self.speed_up_sleep(), self.patch_sample(1), \
                mock.patch.object(PLUGIN.random, "random", return_value=0.9), \
                mock.patch.object(plugin, "_get_card_image", new=mock.AsyncMock(return_value=TINY_PNG)):
            result = await plugin.handle_tarot_tool(stream_id="stream-1", formation="单张")

        self.assertTrue(result["success"])
        self.assertTrue(result["stop_after_execution"])
        capabilities = harness.capabilities()
        self.assertIn("maisaka.reply.generate", capabilities)
        self.assertEqual(capabilities.count("send.image"), 1)
        self.assertIn("send.text", capabilities)
        text = harness.args_of("send.text")[0]["text"]
        self.assertEqual(text, PERSONA_TEXT)

    async def test_seven_card_spread(self) -> None:
        plugin, harness = await self.make_plugin()
        with self.speed_up_sleep(), self.patch_sample(7), \
                mock.patch.object(PLUGIN.random, "random", return_value=0.9), \
                mock.patch.object(plugin, "_get_card_image", new=mock.AsyncMock(return_value=TINY_PNG)):
            result = await plugin.handle_tarot_tool(stream_id="stream-1", formation="六芒星")
        self.assertTrue(result["success"])
        # 默认开启布局合成图：7 张牌只发 1 张合成图
        self.assertEqual(harness.capabilities().count("send.image"), 1)

    async def test_invalid_formation(self) -> None:
        plugin, harness = await self.make_plugin()
        result = await plugin.handle_tarot_tool(stream_id="stream-1", formation="不存在的牌阵")
        self.assertFalse(result["success"])
        self.assertTrue(harness.args_of("send.text"))
        self.assertIn("不存在牌阵", harness.args_of("send.text")[0]["text"])

    async def test_persona_failure_uses_raw_text(self) -> None:
        plugin, harness = await self.make_plugin(FakeHarness(reply_fail=True))
        with self.speed_up_sleep(), self.patch_sample(1), \
                mock.patch.object(PLUGIN.random, "random", return_value=0.9), \
                mock.patch.object(plugin, "_get_card_image", new=mock.AsyncMock(return_value=TINY_PNG)):
            result = await plugin.handle_tarot_tool(stream_id="stream-1", formation="单张")
        self.assertTrue(result["success"])
        text = harness.args_of("send.text")[0]["text"]
        self.assertIn("🔮", text)
        self.assertIn("单张", text)

    async def test_strict_image_failure_aborts(self) -> None:
        plugin, harness = await self.make_plugin()
        with self.speed_up_sleep(), self.patch_sample(1), \
                mock.patch.object(plugin, "_get_card_image", new=mock.AsyncMock(return_value=None)):
            result = await plugin.handle_tarot_tool(stream_id="stream-1", formation="单张")
        self.assertFalse(result["success"])
        texts = harness.args_of("send.text")
        self.assertTrue(any("占卜中止" in args["text"] for args in texts))


class KeywordTriggerTest(PluginTestCase):
    """关键词兜底 Hook。"""

    async def test_keyword_triggers_and_aborts(self) -> None:
        plugin, harness = await self.make_plugin()
        with self.speed_up_sleep(), self.patch_sample(1), \
                mock.patch.object(PLUGIN.random, "random", return_value=0.9), \
                mock.patch.object(plugin, "_get_card_image", new=mock.AsyncMock(return_value=TINY_PNG)):
            result = await plugin.handle_keyword_trigger(
                message={"session_id": "stream-1", "processed_plain_text": "@麦麦 帮我占卜一下明天天气"}
            )
        self.assertEqual(result, {"action": "abort"})
        self.assertIn("send.image", harness.capabilities())

    async def test_casual_mention_not_triggered(self) -> None:
        plugin, harness = await self.make_plugin()
        result = await plugin.handle_keyword_trigger(
            message={"session_id": "stream-1", "processed_plain_text": "塔罗牌真好玩"}
        )
        self.assertIsNone(result)
        self.assertEqual(harness.calls, [])

    async def test_missing_session_not_triggered(self) -> None:
        plugin, harness = await self.make_plugin()
        result = await plugin.handle_keyword_trigger(
            message={"processed_plain_text": "帮我抽一张塔罗"}
        )
        self.assertIsNone(result)
        self.assertEqual(harness.calls, [])


class CommandTest(PluginTestCase):
    """/tarots 管理指令。"""

    async def test_non_admin_denied(self) -> None:
        plugin, harness = await self.make_plugin(admin=(ADMIN_QQ,))
        success, _, _ = await plugin.handle_tarots_command(
            stream_id="stream-1", user_id="99999", matched_groups={"action": "list"}
        )
        self.assertFalse(success)
        self.assertIn("权限不足", harness.args_of("send.text")[0]["text"])

    async def test_admin_list_decks(self) -> None:
        plugin, harness = await self.make_plugin(admin=(ADMIN_QQ,))
        success, message, _ = await plugin.handle_tarots_command(
            stream_id="stream-1", user_id=ADMIN_QQ, matched_groups={"action": "list"}
        )
        self.assertTrue(success)
        self.assertIn("已启用", message)
        self.assertIn("classical", message)
        self.assertIn("未启用牌组", message)
        self.assertIn("allow bilibili", message)

    async def test_switch_to_disabled_deck_rejected(self) -> None:
        plugin, harness = await self.make_plugin(admin=(ADMIN_QQ,))
        success, message, _ = await plugin.handle_tarots_command(
            stream_id="stream-1", user_id=ADMIN_QQ, matched_groups={"action": "switch", "value": "bilibili"}
        )
        self.assertIn("默认锁定", message)
        self.assertIn("/tarots allow bilibili", message)
        self.assertEqual(plugin._using_cards, "classical")

    async def test_switch_allowed_deck_saves_state(self) -> None:
        plugin, _ = await self.make_plugin(admin=(ADMIN_QQ,), use_cards=("classical", "east"))
        success, message, _ = await plugin.handle_tarots_command(
            stream_id="stream-1", user_id=ADMIN_QQ, matched_groups={"action": "switch", "value": "east"}
        )
        self.assertTrue(success)
        self.assertEqual(plugin._using_cards, "east")
        state = json.loads((self._tmp_dir / "data" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["using_cards"], "east")

    async def test_cache_command(self) -> None:
        plugin, harness = await self.make_plugin(admin=(ADMIN_QQ,))
        with mock.patch.object(plugin, "_ensure_cached_image", new=mock.AsyncMock(return_value=b"x")):
            success, message, _ = await plugin.handle_tarots_command(
                stream_id="stream-1", user_id=ADMIN_QQ, matched_groups={"action": "cache"}
            )
        self.assertTrue(success)
        self.assertIn("缓存完成", message)


class AllowLockTest(PluginTestCase):
    """/tarots allow 与 /tarots lock：自助确认授权与撤销。"""

    async def test_allow_unlocks_deck(self) -> None:
        plugin, _ = await self.make_plugin(admin=(ADMIN_QQ,))
        success, message, _ = await plugin.handle_tarots_command(
            stream_id="stream-1", user_id=ADMIN_QQ, matched_groups={"action": "allow", "value": "bilibili"}
        )
        self.assertTrue(success)
        self.assertIn("已记录", message)
        self.assertIn("bilibili", plugin._allowed_decks())
        state = json.loads((self._tmp_dir / "data" / "state.json").read_text(encoding="utf-8"))
        self.assertIn("bilibili", state["unlocked_decks"])

    async def test_switch_after_allow(self) -> None:
        plugin, _ = await self.make_plugin(admin=(ADMIN_QQ,))
        await plugin.handle_tarots_command(
            stream_id="stream-1", user_id=ADMIN_QQ, matched_groups={"action": "allow", "value": "bilibili"}
        )
        success, message, _ = await plugin.handle_tarots_command(
            stream_id="stream-1", user_id=ADMIN_QQ, matched_groups={"action": "switch", "value": "bilibili"}
        )
        self.assertTrue(success)
        self.assertIn("已切换", message)
        self.assertEqual(plugin._using_cards, "bilibili")

    async def test_lock_revokes_unlock(self) -> None:
        plugin, _ = await self.make_plugin(admin=(ADMIN_QQ,))
        await plugin.handle_tarots_command(
            stream_id="stream-1", user_id=ADMIN_QQ, matched_groups={"action": "allow", "value": "bilibili"}
        )
        success, message, _ = await plugin.handle_tarots_command(
            stream_id="stream-1", user_id=ADMIN_QQ, matched_groups={"action": "lock", "value": "bilibili"}
        )
        self.assertTrue(success)
        self.assertIn("已锁定", message)
        self.assertNotIn("bilibili", plugin._allowed_decks())

    async def test_allow_unknown_deck(self) -> None:
        plugin, _ = await self.make_plugin(admin=(ADMIN_QQ,))
        _, message, _ = await plugin.handle_tarots_command(
            stream_id="stream-1", user_id=ADMIN_QQ, matched_groups={"action": "allow", "value": "不存在的牌组"}
        )
        self.assertIn("找不到", message)

    async def test_lock_default_deck_rejected(self) -> None:
        plugin, _ = await self.make_plugin(admin=(ADMIN_QQ,))
        _, message, _ = await plugin.handle_tarots_command(
            stream_id="stream-1", user_id=ADMIN_QQ, matched_groups={"action": "lock", "value": "classical"}
        )
        self.assertIn("默认启用", message)


class LocalImageTest(PluginTestCase):
    """仓库内置图源（local_images）的牌组不联网，直接读本地图片。"""

    PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="

    def _make_demo_deck(self, img_url: str) -> Any:
        tarot_root = self._tmp_dir / "tarot_jsons"
        deck_dir = tarot_root / "demo"
        (deck_dir / "images").mkdir(parents=True)
        (deck_dir / "images" / "m00.png").write_bytes(base64.b64decode(self.PNG))
        (deck_dir / "tarots.json").write_text(
            json.dumps(
                {
                    "_meta": {"card_types": "大阿卡纳", "total_cards": 1, "local_images": True},
                    "0": {
                        "name": "愚者",
                        "info": {"description": "正位", "reverseDescription": "逆位", "imgUrl": img_url},
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return tarot_root

    async def test_local_images_support(self) -> None:
        tarot_root = self._make_demo_deck("images/m00.png")
        with mock.patch.object(PLUGIN, "TAROT_JSON_DIR", new=tarot_root):
            plugin, _ = await self.make_plugin(use_cards=("demo",))
            self.assertEqual(plugin._using_cards, "demo")
            data = await plugin._ensure_cached_image("0")
        self.assertEqual(data, base64.b64decode(self.PNG))
        self.assertFalse((self._tmp_dir / "data" / "tarots_cache").exists())

    async def test_local_image_traversal_blocked(self) -> None:
        tarot_root = self._make_demo_deck("../../../secret.png")
        with mock.patch.object(PLUGIN, "TAROT_JSON_DIR", new=tarot_root):
            plugin, _ = await self.make_plugin(use_cards=("demo",))
            self.assertIsNone(await plugin._ensure_cached_image("0"))


class CustomFormationTest(PluginTestCase):
    """data 目录自定义牌阵。"""

    async def test_custom_formation_loaded(self) -> None:
        data_dir = self._tmp_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "formations.json").write_text(
            json.dumps(
                {"双牌测试": {"cards_num": 2, "is_cut": False, "represent": [["左手", "右手"]]}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        plugin, _ = await self.make_plugin()
        self.assertIn("双牌测试", plugin._formations)
        self.assertEqual(plugin._formations["双牌测试"]["cards_num"], 2)
        self.assertTrue(plugin._formations["双牌测试"].get("custom"))


class FollowupTest(PluginTestCase):
    """抽牌后的追问上下文。"""

    async def test_followup_uses_last_draw(self) -> None:
        plugin, harness = await self.make_plugin()
        with self.speed_up_sleep(), self.patch_sample(1), \
                mock.patch.object(PLUGIN.random, "random", return_value=0.9), \
                mock.patch.object(plugin, "_get_card_image", new=mock.AsyncMock(return_value=TINY_PNG)):
            result = await plugin.handle_tarot_tool(stream_id="stream-1", formation="单张")
        self.assertTrue(result["success"])
        self.assertIn("stream-1", plugin._last_draws)

        result = await plugin.handle_keyword_trigger(
            message={"session_id": "stream-1", "processed_plain_text": "刚才那张牌什么意思"}
        )
        self.assertEqual(result, {"action": "abort"})
        calls = harness.args_of("maisaka.reply.generate")
        self.assertGreaterEqual(len(calls), 2)
        self.assertIn("用户追问", str(calls[-1].get("reference")))

    async def test_followup_without_last_draw_ignored(self) -> None:
        plugin, harness = await self.make_plugin()
        result = await plugin.handle_keyword_trigger(
            message={"session_id": "stream-1", "processed_plain_text": "刚才那张牌什么意思"}
        )
        self.assertIsNone(result)
        self.assertEqual(harness.calls, [])


class DailyTest(PluginTestCase):
    """每日一牌：所有人可用、同一天同一人结果一致。"""

    async def test_daily_draw_deterministic(self) -> None:
        plugin, harness = await self.make_plugin(admin=(ADMIN_QQ,))
        with self.speed_up_sleep(), \
                mock.patch.object(plugin, "_get_card_image", new=mock.AsyncMock(return_value=TINY_PNG)):
            success1, _, _ = await plugin.handle_tarots_command(
                stream_id="stream-1", user_id="99999", matched_groups={"action": "daily"}
            )
            success2, _, _ = await plugin.handle_tarots_command(
                stream_id="stream-1", user_id="99999", matched_groups={"action": "daily"}
            )
        self.assertTrue(success1)
        self.assertTrue(success2)
        images = harness.args_of("send.image")
        self.assertEqual(len(images), 2)
        self.assertEqual(images[0], images[1])


class ThumbnailTest(PluginTestCase):
    """牌组列表附带预览图。"""

    async def test_list_sends_thumbnail(self) -> None:
        plugin, harness = await self.make_plugin(
            admin=(ADMIN_QQ,), adjustment={"show_deck_thumbnail": True}
        )
        with mock.patch.object(plugin, "_get_card_image", new=mock.AsyncMock(return_value=TINY_PNG)):
            success, _, _ = await plugin.handle_tarots_command(
                stream_id="stream-1", user_id=ADMIN_QQ, matched_groups={"action": "list"}
            )
        self.assertTrue(success)
        self.assertIn("send.image", harness.capabilities())


class FormationsCommandTest(PluginTestCase):
    """牌阵列表：所有人可用。"""

    async def test_formations_list_for_everyone(self) -> None:
        plugin, _ = await self.make_plugin(admin=(ADMIN_QQ,))
        success, message, _ = await plugin.handle_tarots_command(
            stream_id="stream-1", user_id="99999", matched_groups={"action": "formations"}
        )
        self.assertTrue(success)
        self.assertIn("可用牌阵", message)
        self.assertIn("凯尔特十字", message)
        self.assertIn("身心灵", message)


class NewFormationTest(PluginTestCase):
    """/tarots newformation：管理员在群里直接建牌阵。"""

    async def test_admin_creates_formation(self) -> None:
        plugin, _ = await self.make_plugin(admin=(ADMIN_QQ,))
        success, message, _ = await plugin.handle_tarots_command(
            stream_id="stream-1",
            user_id=ADMIN_QQ,
            matched_groups={"action": "newformation", "value": "群友三连", "rest": "3 起因|经过|结果"},
        )
        self.assertTrue(success)
        self.assertIn("已新增牌阵", message)
        self.assertIn("群友三连", plugin._formations)
        self.assertEqual(plugin._formations["群友三连"]["cards_num"], 3)
        self.assertEqual(plugin._formations["群友三连"]["represent"], ["起因", "经过", "结果"])
        saved = json.loads((self._tmp_dir / "data" / "formations.json").read_text(encoding="utf-8"))
        self.assertIn("群友三连", saved)

    async def test_positions_auto_padded(self) -> None:
        plugin, _ = await self.make_plugin(admin=(ADMIN_QQ,))
        await plugin.handle_tarots_command(
            stream_id="stream-1",
            user_id=ADMIN_QQ,
            matched_groups={"action": "newformation", "value": "双牌", "rest": "2 左手"},
        )
        self.assertEqual(plugin._formations["双牌"]["represent"], ["左手", "位置2"])

    async def test_builtin_name_rejected(self) -> None:
        plugin, _ = await self.make_plugin(admin=(ADMIN_QQ,))
        _, message, _ = await plugin.handle_tarots_command(
            stream_id="stream-1",
            user_id=ADMIN_QQ,
            matched_groups={"action": "newformation", "value": "时间之流", "rest": "3 a|b|c"},
        )
        self.assertIn("内置牌阵", message)

    async def test_invalid_count_rejected(self) -> None:
        plugin, _ = await self.make_plugin(admin=(ADMIN_QQ,))
        _, message, _ = await plugin.handle_tarots_command(
            stream_id="stream-1",
            user_id=ADMIN_QQ,
            matched_groups={"action": "newformation", "value": "太多牌", "rest": "99 a|b"},
        )
        self.assertIn("1~10", message)

    async def test_non_admin_denied(self) -> None:
        plugin, _ = await self.make_plugin(admin=(ADMIN_QQ,))
        success, _, _ = await plugin.handle_tarots_command(
            stream_id="stream-1",
            user_id="99999",
            matched_groups={"action": "newformation", "value": "群友三连", "rest": "3 a|b|c"},
        )
        self.assertFalse(success)
        self.assertNotIn("群友三连", plugin._formations)


class ConfigTest(PluginTestCase):
    """配置默认值与配置页 Schema。"""

    def test_defaults(self) -> None:
        config = PLUGIN.TarotsPlugin.build_default_config()
        self.assertEqual(config["plugin"]["config_version"], "2.0.0")
        self.assertEqual(config["cards"]["using_cards"], "classical")
        self.assertEqual(config["cards"]["use_cards"], ["classical", "elaine"])
        self.assertTrue(config["adjustment"]["enable_keyword_fallback"])
        self.assertEqual(config["permissions"]["admin_users"], [])

    def test_config_ui_schema_has_tabs(self) -> None:
        schema = PLUGIN.TarotsPlugin.build_config_schema(plugin_id=PLUGIN_ID, plugin_name="塔罗牌插件", plugin_version="2.0.0")
        layout = schema.get("layout") or {}
        self.assertEqual(layout.get("type"), "tabs")
        tab_ids = [tab["id"] for tab in layout.get("tabs") or []]
        self.assertEqual(tab_ids, ["basic", "draw", "advanced"])
        # 每个展示块都要能映射回真实配置节
        for section in schema["sections"].values():
            self.assertIn(section["name"], ("plugin", "components", "proxy", "cards", "adjustment", "permissions"))
            for field in section["fields"].values():
                self.assertIn("label", field)
                self.assertIn("hint", field)


if __name__ == "__main__":
    unittest.main()
