"""牌库、牌阵、文本构造与关键词识别的纯逻辑单元测试。"""

from __future__ import annotations

import importlib.util
import io
import sys
import types
import unittest
from pathlib import Path
from typing import Any


PLUGIN_DIR = Path(__file__).resolve().parents[1]
MODULE_NAME = "maibot_tarots_plugin"


def load_plugin_module() -> Any:
    """按包方式加载插件（plugin.py 通过相对导入引用同目录模块）。"""

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


class FormationAndDeckTest(unittest.TestCase):
    """牌阵与牌组数据。"""

    def test_formations_loaded(self) -> None:
        formations = PLUGIN.load_formations()
        self.assertGreaterEqual(len(formations), 11)
        for name in PLUGIN.FALLBACK_FORMATION_NAMES:
            self.assertIn(name, formations, name)
        self.assertEqual(formations["单张"]["cards_num"], 1)
        self.assertEqual(formations["六芒星"]["cards_num"], 7)
        self.assertEqual(formations["凯尔特十字"]["cards_num"], 10)
        self.assertEqual(formations["二选一"]["cards_num"], 5)
        self.assertEqual(formations["身心灵"]["cards_num"], 3)
        self.assertEqual(len(formations["时间之流"]["represent"]), 3)
        self.assertTrue(formations["时间之流"]["represent"])

    def test_available_decks(self) -> None:
        decks = PLUGIN.available_deck_ids()
        for expected in ("bilibili", "classical", "east", "elaine"):
            self.assertIn(expected, decks)
        self.assertIsNone(PLUGIN.load_deck("不存在的牌组"))

    def test_elaine_deck(self) -> None:
        deck = PLUGIN.load_deck("elaine")
        self.assertIsNotNone(deck)
        self.assertEqual(len(deck["cards"]), 78)
        self.assertEqual(deck["cards"]["22"]["info"]["imgUrl"], "cards/cuac.jpg")
        self.assertEqual(deck["cards"]["77"]["info"]["imgUrl"], "cards/waki.jpg")
        self.assertEqual(deck["cards"]["50"]["info"]["imgUrl"], "cards/swac.jpg")

    def test_locked_decks_have_notice(self) -> None:
        for deck_id in ("bilibili", "east"):
            deck = PLUGIN.load_deck(deck_id)
            self.assertIsNotNone(deck)
            self.assertTrue(str(deck["meta"].get("notice") or "").strip(), deck_id)

    def test_deck_card_range(self) -> None:
        classical = PLUGIN.load_deck("classical")
        east = PLUGIN.load_deck("east")
        self.assertEqual(len(PLUGIN.deck_card_range(classical, "全部")), 78)
        self.assertEqual(len(PLUGIN.deck_card_range(classical, "大阿卡纳")), 22)
        self.assertEqual(len(PLUGIN.deck_card_range(classical, "小阿卡纳")), 56)
        self.assertEqual(len(PLUGIN.deck_card_range(east, "大阿卡纳")), 22)
        self.assertEqual(PLUGIN.deck_card_range(east, "小阿卡纳"), [])


class TextBuildTest(unittest.TestCase):
    """结果文本与解读材料。"""

    def setUp(self) -> None:
        self.deck = PLUGIN.load_deck("classical")
        self.formations = PLUGIN.load_formations()
        self.cards = [("0", False), ("1", True), ("2", False)]

    def test_result_text(self) -> None:
        text = PLUGIN.build_result_text(
            "时间之流", "classical", self.cards, self.formations["时间之流"], self.deck
        )
        self.assertIn("🔮", text)
        self.assertIn("时间之流", text)
        self.assertIn("1️⃣", text)
        self.assertIn("过去", text)
        self.assertIn("正位", text)
        self.assertIn("逆位", text)
        self.assertIn("共 3 张牌，其中 1 张逆位", text)

    def test_persona_material(self) -> None:
        material = PLUGIN.build_persona_material(
            "时间之流", self.cards, self.formations["时间之流"], self.deck, "工作怎么办"
        )
        self.assertIn("时间之流", material)
        self.assertIn("「过去」", material)
        self.assertIn("工作怎么办", material)

    def test_rotate_image(self) -> None:
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (8, 4), "red").save(buffer, format="PNG")
        rotated = PLUGIN.rotate_image_180(buffer.getvalue())
        self.assertIsNotNone(rotated)
        with Image.open(io.BytesIO(rotated)) as image:
            self.assertEqual(image.size, (8, 4))

    def test_compose_spread_image(self) -> None:
        from PIL import Image

        tiny = __import__("base64").b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        result = PLUGIN.compose_spread_image(
            "时间之流", [(tiny, "愚者", "过去"), (tiny, "魔术师", "现在"), (tiny, "世界", "未来")]
        )
        self.assertIsNotNone(result)
        with Image.open(io.BytesIO(result)) as image:
            self.assertGreater(image.width, 600)
            self.assertGreater(image.height, 400)
            colors = image.convert("RGB").getcolors(maxcolors=300000) or []
            self.assertGreater(len(colors), 100, "布局图背景应该是桌布风，不是纯色")

    def test_compose_fallback_grid(self) -> None:
        from PIL import Image

        tiny = __import__("base64").b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        # 未登记的牌阵名走网格兜底
        result = PLUGIN.compose_spread_image("我的自定义牌阵", [(tiny, "愚者", "位置1"), (tiny, "魔术师", "位置2")])
        self.assertIsNotNone(result)
        with Image.open(io.BytesIO(result)) as image:
            self.assertGreaterEqual(image.width, 600)


class IntentParseTest(unittest.TestCase):
    """关键词与参数解析。"""

    def setUp(self) -> None:
        self.formations = PLUGIN.load_formations()

    def test_looks_like_tarot_request(self) -> None:
        for text in ("爱丽丝抽一张塔罗", "帮我占卜一下明天天气", "帮我看看塔罗", "@麦麦 用塔罗牌算算"):
            self.assertTrue(PLUGIN.looks_like_tarot_request(text), text)

    def test_not_tarot_request(self) -> None:
        for text in ("塔罗牌真好玩", "今天天气不错", "/tarots cache", ""):
            self.assertFalse(PLUGIN.looks_like_tarot_request(text), text)

    def test_parse_formation(self) -> None:
        self.assertEqual(
            PLUGIN.parse_formation_from_text("用时间之流牌阵占卜一下", self.formations), "时间之流"
        )
        self.assertEqual(PLUGIN.parse_formation_from_text("帮我抽三张", self.formations), "时间之流")
        self.assertEqual(PLUGIN.parse_formation_from_text("随便抽一张", self.formations), "单张")
        # 懂塔罗的群友用自己的说法也能触发
        self.assertEqual(PLUGIN.parse_formation_from_text("抽个凯尔特十字", self.formations), "凯尔特十字")
        self.assertEqual(PLUGIN.parse_formation_from_text("帮我做二选一", self.formations), "二选一")
        self.assertEqual(PLUGIN.parse_formation_from_text("用身心灵看看", self.formations), "身心灵")
        self.assertEqual(PLUGIN.parse_formation_from_text("过去现在未来", self.formations), "时间之流")
        self.assertEqual(PLUGIN.parse_formation_from_text("来个十字牌阵", self.formations), "吉普赛十字")

    def test_parse_card_type(self) -> None:
        self.assertEqual(PLUGIN.parse_card_type_from_text("只看大阿卡纳"), "大阿卡纳")
        self.assertEqual(PLUGIN.parse_card_type_from_text("小阿卡纳就好"), "小阿卡纳")
        self.assertEqual(PLUGIN.parse_card_type_from_text("随便抽"), "全部")


if __name__ == "__main__":
    unittest.main()
