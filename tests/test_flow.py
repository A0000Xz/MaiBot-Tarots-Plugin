"""整链路单元测试：假 ctx 跑 Tool / 关键词兜底 / 管理指令。"""

from __future__ import annotations

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
                mock.patch.object(plugin, "_get_card_image", new=mock.AsyncMock(return_value=b"fake-image")):
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
                mock.patch.object(plugin, "_get_card_image", new=mock.AsyncMock(return_value=b"fake-image")):
            result = await plugin.handle_tarot_tool(stream_id="stream-1", formation="六芒星")
        self.assertTrue(result["success"])
        self.assertEqual(harness.capabilities().count("send.image"), 7)

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
                mock.patch.object(plugin, "_get_card_image", new=mock.AsyncMock(return_value=b"fake-image")):
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
                mock.patch.object(plugin, "_get_card_image", new=mock.AsyncMock(return_value=b"fake-image")):
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
        self.assertIn("allow blackcat", message)

    async def test_switch_to_disabled_deck_rejected(self) -> None:
        plugin, harness = await self.make_plugin(admin=(ADMIN_QQ,))
        success, message, _ = await plugin.handle_tarots_command(
            stream_id="stream-1", user_id=ADMIN_QQ, matched_groups={"action": "switch", "value": "blackcat"}
        )
        self.assertIn("默认锁定", message)
        self.assertIn("非商业", message)
        self.assertIn("/tarots allow blackcat", message)
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
            stream_id="stream-1", user_id=ADMIN_QQ, matched_groups={"action": "allow", "value": "blackcat"}
        )
        self.assertTrue(success)
        self.assertIn("已记录", message)
        self.assertIn("blackcat", plugin._allowed_decks())
        state = json.loads((self._tmp_dir / "data" / "state.json").read_text(encoding="utf-8"))
        self.assertIn("blackcat", state["unlocked_decks"])

    async def test_switch_after_allow(self) -> None:
        plugin, _ = await self.make_plugin(admin=(ADMIN_QQ,))
        await plugin.handle_tarots_command(
            stream_id="stream-1", user_id=ADMIN_QQ, matched_groups={"action": "allow", "value": "blackcat"}
        )
        success, message, _ = await plugin.handle_tarots_command(
            stream_id="stream-1", user_id=ADMIN_QQ, matched_groups={"action": "switch", "value": "blackcat"}
        )
        self.assertTrue(success)
        self.assertIn("已切换", message)
        self.assertEqual(plugin._using_cards, "blackcat")

    async def test_lock_revokes_unlock(self) -> None:
        plugin, _ = await self.make_plugin(admin=(ADMIN_QQ,))
        await plugin.handle_tarots_command(
            stream_id="stream-1", user_id=ADMIN_QQ, matched_groups={"action": "allow", "value": "blackcat"}
        )
        success, message, _ = await plugin.handle_tarots_command(
            stream_id="stream-1", user_id=ADMIN_QQ, matched_groups={"action": "lock", "value": "blackcat"}
        )
        self.assertTrue(success)
        self.assertIn("已锁定", message)
        self.assertNotIn("blackcat", plugin._allowed_decks())

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
