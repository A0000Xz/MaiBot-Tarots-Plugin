"""由牌组资源 manifest 生成插件 tarots.json。

- 牌名使用资源 manifest 的标准牌名（中文），不把模型娘昵称当牌名
- 牌义复用插件现有牌义库（按 RWS-78 ordinal 映射）
- 图片路径使用资源内稳定 cardId；实际图片由 deck_pack 从本地缓存解析
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
DOCS = PROJECT / "docs" / "kindred-arcana"
MANIFEST = DOCS / "manifest.json"
CLASSICAL = PROJECT / "tarot_jsons" / "classical" / "tarots.json"
OUTPUT = PROJECT / "tarot_jsons" / "kindred-arcana" / "tarots.json"
DECK_ID = "kindred-arcana"
VERSION = "1.0.0-rc.5"
SUITS = ("cups", "pentacles", "swords", "wands")
RANKS = ("ace", "2", "3", "4", "5", "6", "7", "8", "9", "10", "page", "knight", "queen", "king")


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    classical = json.loads(CLASSICAL.read_text(encoding="utf-8"))
    if len(classical) - 1 != 78:
        raise SystemExit("现有牌义库不是 78 张")
    expected_ids = [f"major-{i:02d}" for i in range(22)] + [f"{s}-{r}" for s in SUITS for r in RANKS]
    if [c["id"] for c in manifest["cards"]] != expected_ids:
        raise SystemExit("资源 manifest 牌序不是 RWS-78 标准顺序")
    if manifest["contentVersion"] != VERSION:
        raise SystemExit("资源版本不匹配")

    deck: dict[str, object] = {
        "_meta": {
            "name": "灵契秘仪",
            "description": "灵契秘仪 · 78 张（独立牌组资源包）",
            "card_types": "全部",
            "total_cards": 78,
            "local_images": True,
            "pack_deck_id": DECK_ID,
            "pack_content_version": VERSION,
            "card_scheme": "rws-78",
            "stable_ids": expected_ids,
            "author": "NethielChou",
            "license": "LicenseRef-Kindred-Arcana-Artwork",
            "homepage": "https://github.com/NethielChou/kindred-arcana",
            "notice": "牌面为原创作品，仅授权随 MaiBot 塔罗工坊插件使用；详见牌组仓库 LICENSE-ARTWORK.md / RIGHTS.md。",
        }
    }
    for ordinal, card in enumerate(manifest["cards"]):
        source = classical[str(ordinal)]
        info = source.get("info") if isinstance(source.get("info"), dict) else {}
        description = str(info.get("description") or "").strip()
        reverse = str(info.get("reverseDescription") or "").strip()
        if not description or not reverse:
            raise SystemExit(f"现有牌义缺失：ordinal {ordinal}")
        deck[str(ordinal)] = {
            "name": card["name"]["zh"],
            "stableId": card["id"],
            "ordinal": ordinal,
            "model": card["model"],
            "info": {
                "description": description,
                "reverseDescription": reverse,
                "imgUrl": f"assets/cards/{card['id']}.png",
            },
        }

    # 关键映射自检：8=力量、11=正义、四花色顺序
    checks = {
        8: "力量", 11: "正义", 22: "圣杯ACE", 36: "星币ACE", 50: "宝剑ACE", 64: "权杖ACE",
        35: "圣杯国王", 49: "星币国王", 63: "宝剑国王", 77: "权杖国王",
    }
    for ordinal, expected_name in checks.items():
        actual = deck[str(ordinal)]["name"]  # type: ignore[index]
        if actual != expected_name:
            raise SystemExit(f"映射错误：ordinal {ordinal} 期望 {expected_name}，实际 {actual}")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(deck, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 仓库内保留一份可审计的资源清单/锁/说明（不含图片）
    DOCS.mkdir(parents=True, exist_ok=True)
    for name in ("schema",):
        source = MANIFEST.parent / name
        if source.is_dir():
            target = DOCS / name
            target.mkdir(parents=True, exist_ok=True)
            for child in source.iterdir():
                if child.is_file():
                    (target / child.name).write_bytes(child.read_bytes())
    (DOCS / "README.md").write_text(
        "# 灵契秘仪资源（插件侧接入记录）\n\n"
        "- deckId: `kindred-arcana`\n"
        f"- contentVersion: `{VERSION}`\n"
        "- 资源仓库: https://github.com/NethielChou/kindred-arcana\n"
        "- 图片只在牌组仓库的固定版本 Release 中分发，本插件仓库不包含图片。\n"
        "- 插件首次启用时下载并校验 ZIP/manifest/78 图摘要，之后离线读取。\n"
        "- 许可：`LicenseRef-Kindred-Arcana-Artwork`（见牌组仓库 LICENSE-ARTWORK.md / RIGHTS.md）。\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(OUTPUT), "cards": len(manifest["cards"]),
        "meta": deck["_meta"], "docs": str(DOCS),
        "checks": checks,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
