"""灵契秘仪资源包与插件接入测试。

覆盖：
- 官方公开发布包（若本地存在）的 78 图 SHA/尺寸/安装/离线读取；
- ZIP/manifest/图片摘要校验、路径穿越、缺图、损坏包拒绝；
- 同版本重装、升级成功、升级失败回滚、版本固定；
- 插件牌组列表、切换、导入回退、抽牌映射、逆位旋转、离线重启。
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import shutil
import sys
import types
import unittest
import uuid
import zipfile
from pathlib import Path
from typing import Any
from unittest import mock

from PIL import Image
from maibot_sdk import PluginContext, PluginPaths

PLUGIN_DIR = Path(__file__).resolve().parents[1]
MODULE_NAME = "maibot_tarots_plugin"
PLUGIN_ID = "github.a0000xz.maibot-tarots-plugin"
TMP_ROOT = Path(__file__).resolve().parent / "_tmp"
DOCS = PLUGIN_DIR / "docs" / "kindred-arcana"
REAL_MANIFEST = DOCS / "manifest.json"
REAL_ZIP_CANDIDATES = (
    Path(r"D:\Agent\Agent Workspace\DSH\projects\maibot-tarots-plugin\deck_packs\kindred-arcana\1.0.0-rc.5\kindred-arcana-1.0.0-rc.5-public.zip"),
    Path(r"D:\Agent\Agent Workspace\DSH\tmp\kindred-public\kindred-arcana-1.0.0-rc.5-public.zip"),
)


def load_plugin_module() -> Any:
    module_key = f"{MODULE_NAME}.plugin"
    cached = sys.modules.get(module_key)
    if cached is not None:
        return cached
    if MODULE_NAME not in sys.modules:
        package = types.ModuleType(MODULE_NAME)
        package.__path__ = [str(PLUGIN_DIR)]
        sys.modules[MODULE_NAME] = package
    spec = importlib.util.spec_from_file_location(module_key, PLUGIN_DIR / "plugin.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载塔罗插件模块")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_key] = module
    spec.loader.exec_module(module)
    return module


PLUGIN = load_plugin_module()
CORE = sys.modules[f"{MODULE_NAME}.tarots_core"]
DECK = sys.modules[f"{MODULE_NAME}.deck_pack"]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_png(seed: int) -> bytes:
    color = ((seed * 7) % 256, (seed * 13 + 40) % 256, (seed * 29 + 80) % 256)
    buffer = io.BytesIO()
    Image.new("RGB", (800, 1200), color).save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def build_synthetic_package(
    root: Path,
    *,
    version: str = "1.0.0-rc.5",
    seed: int = 0,
    corrupt_card: int | None = None,
    corrupt_mode: str = "same_size",
) -> dict[str, Any]:
    """用真实 manifest 结构 + 合成小图构建可安装测试包。"""

    manifest = json.loads(REAL_MANIFEST.read_text(encoding="utf-8"))
    manifest["contentVersion"] = version
    files: dict[str, bytes] = {}
    for index, card in enumerate(manifest["cards"]):
        data = make_png(seed + index)
        card["image"]["bytes"] = len(data)
        card["image"]["sha256"] = sha(data)
        files[card["image"]["path"]] = data
    if corrupt_card is not None:
        card = manifest["cards"][corrupt_card]
        path = card["image"]["path"]
        original = files[path]
        if corrupt_mode == "same_size":
            files[path] = bytes([original[0] ^ 0x01]) + original[1:]
        else:
            files[path] = b"broken-png"
    manifest_bytes = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    files["manifest.json"] = manifest_bytes
    files["schema/deck.schema.json"] = (DOCS / "schema" / "deck.schema.json").read_bytes()
    files["RIGHTS.md"] = b"rights"
    files["ATTRIBUTION.md"] = b"attribution"
    zip_path = root / f"synthetic-{version}-{seed}-{uuid.uuid4().hex}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            archive.writestr(name, files[name])
    archive_bytes = zip_path.read_bytes()
    return {
        "zip": zip_path,
        "version": version,
        "manifest": manifest,
        "manifest_sha256": sha(manifest_bytes),
        "archive_sha256": sha(archive_bytes),
    }


def install_synthetic(data_dir: Path, package: dict[str, Any]) -> dict[str, Any]:
    return DECK.install_zip(
        data_dir,
        package["zip"],
        expected_archive_sha=package["archive_sha256"],
        expected_manifest_sha=package["manifest_sha256"],
        expected_version=package["version"],
    )


class DeckPackManagerTest(unittest.TestCase):
    """资源包管理器：校验、安装、升级、回滚、离线。"""

    def setUp(self) -> None:
        self._tmp = TMP_ROOT / uuid.uuid4().hex
        self._tmp.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, self._tmp, True)
        self.data = self._tmp / "data"

    def test_real_public_package_install_and_offline(self) -> None:
        real_zip = next((path for path in REAL_ZIP_CANDIDATES if path.is_file()), None)
        if real_zip is None:
            self.skipTest("本地没有公开发布包")
        result = DECK.install_zip(self.data, real_zip)
        self.assertEqual(result["version"], "1.0.0-rc.5")
        manifest = DECK.verify_active(self.data, full=True)
        assert manifest is not None
        self.assertEqual(len(manifest["cards"]), 78)
        self.assertEqual(DECK.active_version(self.data), "1.0.0-rc.5")
        for card_id in ("major-00", "major-08", "major-11", "cups-ace", "wands-king"):
            path = DECK.image_path(self.data, f"assets/cards/{card_id}.png")
            self.assertIsNotNone(path)
            with Image.open(path) as image:
                image.load()
                self.assertEqual(image.size, (800, 1200))
        with mock.patch.object(DECK.urllib.request, "urlopen", side_effect=AssertionError("不应联网")):
            self.assertIsNotNone(DECK.active_manifest(self.data))
            self.assertIsNotNone(DECK.image_path(self.data, "assets/cards/major-08.png"))

    def test_bad_archive_sha_rejected(self) -> None:
        package = build_synthetic_package(self._tmp)
        with self.assertRaises(DECK.DeckPackError) as raised:
            DECK.install_zip(self.data, package["zip"], expected_archive_sha="0" * 64)
        self.assertEqual(raised.exception.code, "archive_sha")
        self.assertFalse(DECK.status(self.data)["installed"])

    def test_corrupt_image_rejected(self) -> None:
        package = build_synthetic_package(self._tmp, corrupt_card=0)
        with self.assertRaises(DECK.DeckPackError) as raised:
            DECK.install_zip(
                self.data,
                package["zip"],
                expected_archive_sha=package["archive_sha256"],
                expected_manifest_sha=package["manifest_sha256"],
                expected_version=package["version"],
            )
        self.assertIn(raised.exception.code, ("image_sha", "image_size", "image_dims"))
        self.assertFalse(DECK.status(self.data)["installed"])

    def test_missing_image_entry_rejected(self) -> None:
        package = build_synthetic_package(self._tmp)
        broken = self._tmp / "missing.zip"
        with zipfile.ZipFile(package["zip"]) as source, zipfile.ZipFile(broken, "w") as target:
            for info in source.infolist():
                if info.filename == "assets/cards/major-10.png":
                    continue
                target.writestr(info.filename, source.read(info))
        with self.assertRaises(DECK.DeckPackError) as raised:
            DECK.install_zip(
                self.data,
                broken,
                expected_archive_sha=sha(broken.read_bytes()),
                expected_manifest_sha=package["manifest_sha256"],
                expected_version=package["version"],
            )
        self.assertEqual(raised.exception.code, "zip_invalid")

    def test_path_traversal_entry_rejected(self) -> None:
        package = build_synthetic_package(self._tmp)
        broken = self._tmp / "traversal.zip"
        with zipfile.ZipFile(package["zip"]) as source, zipfile.ZipFile(broken, "w") as target:
            for info in source.infolist():
                target.writestr(info.filename, source.read(info))
            target.writestr("assets/cards/../evil.png", b"evil")
        with self.assertRaises(DECK.DeckPackError) as raised:
            DECK.install_zip(
                self.data,
                broken,
                expected_archive_sha=sha(broken.read_bytes()),
                expected_manifest_sha=package["manifest_sha256"],
                expected_version=package["version"],
            )
        self.assertIn(raised.exception.code, ("unsafe_entry", "zip_invalid"))

    def test_same_version_reinstall_idempotent(self) -> None:
        package = build_synthetic_package(self._tmp)
        first = install_synthetic(self.data, package)
        second = install_synthetic(self.data, package)
        self.assertEqual(first["version"], second["version"])
        self.assertEqual(DECK.list_versions(self.data), ["1.0.0-rc.5"])
        self.assertEqual(DECK.status(self.data)["version"], "1.0.0-rc.5")

    def test_upgrade_success_and_failure_rollback(self) -> None:
        v1 = build_synthetic_package(self._tmp, version="1.0.0-rc.5", seed=1)
        v2 = build_synthetic_package(self._tmp, version="1.0.0-rc.6", seed=2)
        install_synthetic(self.data, v1)
        old_image = DECK.image_path(self.data, "assets/cards/major-00.png", version="1.0.0-rc.5")
        install_synthetic(self.data, v2)
        self.assertEqual(DECK.active_version(self.data), "1.0.0-rc.6")
        self.assertEqual(DECK.list_versions(self.data), ["1.0.0-rc.5", "1.0.0-rc.6"])
        new_image = DECK.image_path(self.data, "assets/cards/major-00.png", version="1.0.0-rc.6")
        self.assertNotEqual(old_image.read_bytes(), new_image.read_bytes())

        broken = build_synthetic_package(self._tmp, version="1.0.0-rc.7", seed=3, corrupt_card=3)
        with self.assertRaises(DECK.DeckPackError):
            install_synthetic(self.data, broken)
        self.assertEqual(DECK.active_version(self.data), "1.0.0-rc.6")

        manifest = DECK.set_active(self.data, "1.0.0-rc.5")
        self.assertEqual(manifest["contentVersion"], "1.0.0-rc.5")
        self.assertEqual(DECK.active_version(self.data), "1.0.0-rc.5")

    def test_download_failure_keeps_active(self) -> None:
        package = build_synthetic_package(self._tmp)
        install_synthetic(self.data, package)
        with mock.patch.object(
            DECK, "download_archive", side_effect=DECK.DeckPackError("download_failed", "模拟下载失败")
        ):
            with self.assertRaises(DECK.DeckPackError):
                DECK.download_and_install(self.data, url="https://example.invalid/deck.zip")
        self.assertEqual(DECK.active_version(self.data), "1.0.0-rc.5")

    def test_ensure_available_downloads_fixed_package(self) -> None:
        package = build_synthetic_package(self._tmp, version="1.0.0-rc.6", seed=4)

        def fake_download(url: str, destination: Path, **kwargs: Any) -> Path:
            self.assertTrue(url.startswith("https://"))
            shutil.copyfile(package["zip"], destination)
            return destination

        with mock.patch.object(DECK, "download_archive", side_effect=fake_download):
            manifest = DECK.ensure_available(
                self.data,
                url="https://example.invalid/deck.zip",
                expected_archive_sha=package["archive_sha256"],
                expected_manifest_sha=package["manifest_sha256"],
                expected_version=package["version"],
            )
        self.assertEqual(manifest["contentVersion"], "1.0.0-rc.6")
        self.assertEqual(DECK.active_version(self.data), "1.0.0-rc.6")


class FakeRpc:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, method: str, plugin_id: str, payload: dict[str, Any], timeout_ms: int | None = None) -> dict[str, Any]:
        del method, plugin_id, timeout_ms
        capability = str(payload.get("capability"))
        args = dict(payload.get("args") or {})
        self.calls.append((capability, args))
        if capability == "maisaka.reply.generate":
            return {"success": True, "text": "人设解读"}
        return {"success": True, "sent": True, "message_id": f"m{len(self.calls)}"}

    def capabilities(self) -> list[str]:
        return [capability for capability, _ in self.calls]

    def args_of(self, capability: str) -> list[dict[str, Any]]:
        return [args for name, args in self.calls if name == capability]

    def forward_segments(self) -> list[dict[str, Any]]:
        segments: list[dict[str, Any]] = []
        for args in self.args_of("send.forward"):
            for node in args.get("messages") or []:
                segments.extend(node.get("segments") or [])
        return segments

    def forward_images(self) -> list[str]:
        return [str(seg.get("content")) for seg in self.forward_segments() if seg.get("type") == "image"]

    def forward_texts(self) -> list[str]:
        return [str(seg.get("content")) for seg in self.forward_segments() if seg.get("type") == "text"]


class KindredPluginTest(unittest.IsolatedAsyncioTestCase):
    """插件侧：列表、切换、导入、抽牌映射、逆位、离线重启。"""

    def setUp(self) -> None:
        self._tmp = TMP_ROOT / uuid.uuid4().hex
        self._tmp.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, self._tmp, True)
        self.data = self._tmp / "data"
        self._package: dict[str, Any] | None = None

    def install_package(self) -> dict[str, Any]:
        if self._package is None:
            # 使用真实 manifest 结构 + 合成图，避免每个插件测试安装 214MB
            self._package = build_synthetic_package(self._tmp)
        install_synthetic(self.data, self._package)
        return self._package

    async def make_plugin(
        self,
        *,
        admin: tuple[str, ...] = (),
        deck_pack_overrides: dict[str, Any] | None = None,
        data_dir: Path | None = None,
    ) -> tuple[Any, FakeRpc]:
        rpc = FakeRpc()
        plugin = PLUGIN.TarotsPlugin()
        ctx = PluginContext(
            PLUGIN_ID,
            rpc_call=rpc,
            paths=PluginPaths(data_dir=data_dir or self.data, runtime_dir=self._tmp / "runtime"),
        )
        plugin._set_context(ctx)
        config = plugin.build_default_config()
        config["cards"]["use_cards"] = ["classical", "elaine"]
        config["adjustment"]["show_deck_thumbnail"] = False
        config["permissions"]["admin_users"] = list(admin)
        if deck_pack_overrides:
            config["deck_pack"].update(deck_pack_overrides)
        plugin.set_plugin_config(config)
        await plugin.on_load()
        return plugin, rpc

    async def switch_to_kindred(self, plugin: Any) -> str:
        _, message, _ = await plugin.handle_tarots_command(
            stream_id="stream-1", user_id="10001", matched_groups={"action": "switch", "value": "kindred-arcana"}
        )
        return message

    async def test_list_shows_kindred_status(self) -> None:
        self.install_package()
        plugin, _ = await self.make_plugin(admin=("10001",))
        _, message, _ = await plugin.handle_tarots_command(
            stream_id="stream-1", user_id="10001", matched_groups={"action": "list"}
        )
        self.assertIn("灵契秘仪（kindred-arcana）", message)
        self.assertIn("资源 v1.0.0-rc.5 已就绪", message)

    async def test_switch_and_draw_uses_pack_images_and_stable_ids(self) -> None:
        self.install_package()
        plugin, rpc = await self.make_plugin(admin=("10001",))
        message = await self.switch_to_kindred(plugin)
        self.assertIn("已切换", message)
        self.assertEqual(plugin._using_cards, "kindred-arcana")
        with mock.patch.object(PLUGIN.random, "sample", side_effect=lambda seq, n: list(seq)[:n]), \
                mock.patch.object(PLUGIN.random, "random", return_value=0.9):
            result = await plugin.handle_tarot_tool(stream_id="stream-1", formation="时间之流")
        self.assertTrue(result["success"])
        self.assertEqual(len(rpc.forward_images()), 1)
        texts = " ".join(rpc.forward_texts())
        self.assertIn("愚者", texts)
        self.assertIn("新的开始", texts)
        record = plugin._last_draws["stream-1"]
        self.assertEqual(record["deckId"], "kindred-arcana")
        self.assertEqual(record["contentVersion"], "1.0.0-rc.5")
        self.assertEqual([item["cardId"] for item in record["cards"]], ["major-00", "major-01", "major-02"])
        self.assertTrue(all(item["reversed"] is False for item in record["cards"]))
        state = json.loads((self.data / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["using_cards"], "kindred-arcana")
        self.assertEqual(state["using_deck_version"], "1.0.0-rc.5")

    async def test_reversed_rotation_uses_upright_image(self) -> None:
        self.install_package()
        plugin, _ = await self.make_plugin(admin=("10001",))
        await self.switch_to_kindred(plugin)
        upright = await plugin._get_card_image("8", False, "1.0.0-rc.5")
        reversed_image = await plugin._get_card_image("8", True, "1.0.0-rc.5")
        self.assertIsNotNone(upright)
        self.assertEqual(reversed_image, CORE.rotate_image_180(upright))
        deck = plugin._deck
        self.assertEqual(deck["cards"]["8"]["stableId"], "major-08")
        self.assertEqual(deck["cards"]["8"]["name"], "力量")
        self.assertEqual(deck["cards"]["11"]["name"], "正义")

    async def test_offline_restart_uses_local_cache(self) -> None:
        self.install_package()
        first, _ = await self.make_plugin(admin=("10001",))
        await self.switch_to_kindred(first)
        with mock.patch.object(DECK.urllib.request, "urlopen", side_effect=AssertionError("离线时不应联网")):
            second, rpc = await self.make_plugin(admin=("10001",))
            self.assertEqual(second._using_cards, "kindred-arcana")
            with mock.patch.object(PLUGIN.random, "sample", side_effect=lambda seq, n: list(seq)[:n]), \
                    mock.patch.object(PLUGIN.random, "random", return_value=0.9):
                result = await second.handle_tarot_tool(stream_id="stream-2", formation="单张")
        self.assertTrue(result["success"])
        self.assertEqual(len(rpc.forward_images()), 1)

    async def test_import_command_installs_and_switches(self) -> None:
        package = build_synthetic_package(self._tmp)
        plugin, _ = await self.make_plugin(admin=("10001",), deck_pack_overrides={"auto_download": False})
        original_install = DECK.install_zip
        with mock.patch.object(
            DECK,
            "install_zip",
            side_effect=lambda data_dir, zip_path, **kwargs: original_install(
                data_dir,
                package["zip"],
                expected_archive_sha=package["archive_sha256"],
                expected_manifest_sha=package["manifest_sha256"],
                expected_version=package["version"],
            ),
        ):
            success, message, _ = await plugin.handle_tarots_command(
                stream_id="stream-1",
                user_id="10001",
                matched_groups={"action": "import", "value": str(package["zip"]), "rest": ""},
            )
        self.assertTrue(success)
        self.assertIn("已导入", message)
        self.assertIn("已切换", await self.switch_to_kindred(plugin))

    async def test_switch_auto_download_failure_keeps_old_deck(self) -> None:
        plugin, _ = await self.make_plugin(admin=("10001",))
        with mock.patch.object(
            DECK, "download_archive", side_effect=DECK.DeckPackError("download_failed", "模拟下载失败")
        ):
            message = await self.switch_to_kindred(plugin)
        self.assertIn("资源不可用", message)
        self.assertEqual(plugin._using_cards, "classical")

    async def test_disabled_kindred_hidden(self) -> None:
        plugin, _ = await self.make_plugin(
            admin=("10001",), deck_pack_overrides={"kindred_arcana_enabled": False}
        )
        _, message, _ = await plugin.handle_tarots_command(
            stream_id="stream-1", user_id="10001", matched_groups={"action": "list"}
        )
        self.assertNotIn("灵契秘仪", message)
        self.assertIn("关闭", await self.switch_to_kindred(plugin))

    async def test_import_requires_admin(self) -> None:
        package = build_synthetic_package(self._tmp)
        plugin, _ = await self.make_plugin(admin=("10001",))
        success, _, _ = await plugin.handle_tarots_command(
            stream_id="stream-1",
            user_id="99999",
            matched_groups={"action": "import", "value": str(package["zip"]), "rest": ""},
        )
        self.assertFalse(success)


if __name__ == "__main__":
    unittest.main()
