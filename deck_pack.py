"""灵契秘仪（Kindred Arcana）牌组资源包管理。

资源来源：独立牌组仓库 NethielChou/kindred-arcana 的固定版本 Release。
- 首次启用时下载固定版本 ZIP；
- 校验 ZIP/manifest/78 张图片摘要后安装到插件 data 目录；
- 之后抽牌与重启均从本地读取，支持离线；
- 升级先暂存、验证后原子激活，失败保留旧版本；
- 支持本地 ZIP 导入作为下载失败的回退。

本模块只使用标准库，不依赖宿主或 Pillow。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Any

DECK_ID = "kindred-arcana"
DISPLAY_NAME = "灵契秘仪"
CONTENT_VERSION = "1.0.0-rc.5"
SCHEMA_VERSION = 1

# 公开发布锁（独立牌组仓库 v1.0.0-rc.5 的 Release 资源包）
ARCHIVE_FILE = "kindred-arcana-1.0.0-rc.5-public.zip"
ARCHIVE_SHA256 = "47969bf8015f3025d1d5af6e84caff0a9e6c2461688c5aef6792bb20c5480a1b"
MANIFEST_SHA256 = "c20e0e8b06750464bb706d073471fe23d2a00528ee4d4a52de5b77e393f8b79d"
ARCHIVE_BYTES = 224506403
ARCHIVE_URL = (
    "https://github.com/NethielChou/kindred-arcana/releases/download/"
    f"v{CONTENT_VERSION}/{ARCHIVE_FILE}"
)
MANIFEST_URL = "https://raw.githubusercontent.com/NethielChou/kindred-arcana/main/manifest.json"

CARD_COUNT = 78
SUITS = ("cups", "pentacles", "swords", "wands")
RANKS = ("ace", "2", "3", "4", "5", "6", "7", "8", "9", "10", "page", "knight", "queen", "king")
CARD_IDS = tuple([f"major-{index:02d}" for index in range(22)] + [f"{suit}-{rank}" for suit in SUITS for rank in RANKS])
MODELS = ("deepseek", "gemini", "claude", "gpt", "qwen", "doubao", "seed", "glm", "grok", "kimi", "minimax")

MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_ENTRIES = 100
MAX_IMAGE_BYTES = 16 * 1024 * 1024
IMAGE_WIDTH, IMAGE_HEIGHT = 800, 1200

_REQUIRED_METADATA = ("manifest.json", "schema/deck.schema.json", "RIGHTS.md", "ATTRIBUTION.md")
_VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")


class DeckPackError(Exception):
    """资源包错误；code 供上层转成用户可读提示。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def expected_entries() -> set[str]:
    return {f"assets/cards/{card_id}.png" for card_id in CARD_IDS} | set(_REQUIRED_METADATA)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_root(data_dir: Path) -> Path:
    return (Path(data_dir) / "deck_packs" / DECK_ID).resolve()


def versions_dir(data_dir: Path) -> Path:
    return package_root(data_dir) / "versions"


def _active_file(data_dir: Path) -> Path:
    return package_root(data_dir) / "active.json"


def _png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise DeckPackError("image_invalid", "图片不是合法 PNG")
    return struct.unpack(">II", data[16:24])


def _check_publication(manifest: dict[str, Any], allow_pending: bool) -> None:
    status = manifest.get("status")
    license_info = manifest.get("license") or {}
    if allow_pending:
        if status not in ("integration-preview", "candidate", "release"):
            raise DeckPackError("manifest_invalid", f"未知的发布状态：{status!r}")
        return
    if status != "release":
        raise DeckPackError("license_pending", "该牌组资源尚未标记为正式发布（status != release）")
    if license_info.get("status") != "reviewed-scoped" or license_info.get("publicDistributionReady") is not True:
        raise DeckPackError("license_pending", "该牌组资源尚未完成公开许可核实（publicDistributionReady=false）")


def validate_manifest(
    manifest: Any,
    *,
    expected_version: str = CONTENT_VERSION,
    allow_pending: bool = False,
) -> dict[str, Any]:
    """校验 manifest 结构与固定身份；返回 manifest。"""

    if not isinstance(manifest, dict):
        raise DeckPackError("manifest_invalid", "manifest 不是对象")
    if manifest.get("schemaVersion") != SCHEMA_VERSION:
        raise DeckPackError("manifest_invalid", f"schemaVersion 必须为 {SCHEMA_VERSION}")
    if manifest.get("deckId") != DECK_ID:
        raise DeckPackError("manifest_invalid", "deckId 不匹配")
    if manifest.get("structure") != "RWS-78":
        raise DeckPackError("manifest_invalid", "structure 必须为 RWS-78")
    if manifest.get("imageCount") != CARD_COUNT:
        raise DeckPackError("manifest_invalid", "imageCount 必须为 78")
    version = manifest.get("contentVersion")
    if not isinstance(version, str) or not _VERSION_PATTERN.match(version):
        raise DeckPackError("manifest_invalid", "contentVersion 格式无效")
    if version != expected_version:
        raise DeckPackError("version_mismatch", f"资源版本 {version} 与锁定版本 {expected_version} 不一致")
    if manifest.get("order") != ["major", *SUITS]:
        raise DeckPackError("manifest_invalid", "卡牌分组顺序必须为 major/cups/pentacles/swords/wands")
    if manifest.get("reversedImagePolicy") != "rotate-upright-180-degrees":
        raise DeckPackError("manifest_invalid", "逆位策略必须为 rotate-upright-180-degrees")
    title = manifest.get("title")
    if not isinstance(title, dict) or not title.get("zh") or not title.get("en"):
        raise DeckPackError("manifest_invalid", "缺少中英文牌组名")
    _check_publication(manifest, allow_pending)

    cards = manifest.get("cards")
    if not isinstance(cards, list) or len(cards) != CARD_COUNT:
        raise DeckPackError("manifest_invalid", "cards 必须为 78 张")
    seen_hashes: set[str] = set()
    for index, card in enumerate(cards):
        expected_id = CARD_IDS[index]
        if card.get("id") != expected_id or card.get("ordinal") != index:
            raise DeckPackError("manifest_invalid", f"第 {index} 张牌的 cardId/ordinal 不稳定")
        is_minor = index >= 22
        if card.get("arcana") != ("minor" if is_minor else "major"):
            raise DeckPackError("manifest_invalid", f"{expected_id} arcana 不匹配")
        expected_suit = SUITS[(index - 22) // 14] if is_minor else None
        expected_rank = RANKS[(index - 22) % 14] if is_minor else str(index)
        if card.get("suit") != expected_suit or card.get("rank") != expected_rank:
            raise DeckPackError("manifest_invalid", f"{expected_id} suit/rank 不匹配")
        name = card.get("name")
        if not isinstance(name, dict) or not name.get("zh") or not name.get("en"):
            raise DeckPackError("manifest_invalid", f"{expected_id} 缺少中英文牌名")
        if card.get("model") not in MODELS:
            raise DeckPackError("manifest_invalid", f"{expected_id} model 无效")
        if not isinstance(card.get("interpretation"), str) or not card["interpretation"].strip():
            raise DeckPackError("manifest_invalid", f"{expected_id} 缺少 interpretation")
        image = card.get("image")
        if not isinstance(image, dict):
            raise DeckPackError("manifest_invalid", f"{expected_id} 缺少 image")
        if image.get("path") != f"assets/cards/{expected_id}.png":
            raise DeckPackError("manifest_invalid", f"{expected_id} 图片路径必须使用稳定 cardId")
        if image.get("mime") != "image/png":
            raise DeckPackError("manifest_invalid", f"{expected_id} 图片类型必须为 image/png")
        if (image.get("width"), image.get("height")) != (IMAGE_WIDTH, IMAGE_HEIGHT):
            raise DeckPackError("manifest_invalid", f"{expected_id} 图片尺寸必须为 {IMAGE_WIDTH}x{IMAGE_HEIGHT}")
        size = image.get("bytes")
        if not isinstance(size, int) or not 25 <= size <= MAX_IMAGE_BYTES:
            raise DeckPackError("manifest_invalid", f"{expected_id} 图片字节数无效")
        digest = image.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise DeckPackError("manifest_invalid", f"{expected_id} 图片摘要格式无效")
        if digest in seen_hashes:
            raise DeckPackError("manifest_invalid", f"{expected_id} 图片摘要重复")
        seen_hashes.add(digest)
    return manifest


def verify_package(
    directory: Path,
    *,
    expected_version: str = CONTENT_VERSION,
    expected_manifest_sha: str = MANIFEST_SHA256,
    allow_pending: bool = False,
) -> dict[str, Any]:
    """校验一个解压后的资源目录并返回 manifest。"""

    root = Path(directory).resolve()
    if not root.is_dir():
        raise DeckPackError("package_missing", f"资源目录不存在：{root}")
    files = {
        str(path.relative_to(root)).replace("\\", "/")
        for path in root.rglob("*")
        if path.is_file()
    }
    expected = expected_entries()
    if files != expected:
        missing = sorted(expected - files)[:5]
        extra = sorted(files - expected)[:5]
        raise DeckPackError("package_invalid", f"资源目录条目不符合预期，缺少 {missing}，多出 {extra}")
    manifest_path = root / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest_sha = sha256_bytes(manifest_bytes)
    if manifest_sha != expected_manifest_sha:
        raise DeckPackError("manifest_sha", "manifest SHA-256 与锁定值不一致")
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeckPackError("manifest_invalid", f"manifest 不是合法 JSON：{exc}") from exc
    validate_manifest(manifest, expected_version=expected_version, allow_pending=allow_pending)
    for card in manifest["cards"]:
        image = card["image"]
        path = root / image["path"]
        if not path.is_file():
            raise DeckPackError("image_missing", f"缺少图片：{image['path']}")
        data = path.read_bytes()
        if len(data) != image["bytes"]:
            raise DeckPackError("image_size", f"{card['id']} 图片字节数不一致")
        if sha256_bytes(data) != image["sha256"]:
            raise DeckPackError("image_sha", f"{card['id']} 图片 SHA-256 不一致")
        width, height = _png_size(data)
        if (width, height) != (IMAGE_WIDTH, IMAGE_HEIGHT):
            raise DeckPackError("image_dims", f"{card['id']} PNG 尺寸不是 {IMAGE_WIDTH}x{IMAGE_HEIGHT}")
    return manifest


def _validate_zip_entries(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) != len(expected_entries()):
        raise DeckPackError("zip_invalid", f"ZIP 条目数 {len(infos)} 不符合预期")
    names: list[str] = []
    total = 0
    for info in infos:
        name = info.filename
        if name.startswith("/") or "\\" in name or ".." in Path(name).parts:
            raise DeckPackError("unsafe_entry", f"ZIP 包含不安全路径：{name}")
        if info.is_dir():
            raise DeckPackError("zip_invalid", f"ZIP 不应包含目录条目：{name}")
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise DeckPackError("unsafe_entry", f"ZIP 包含符号链接：{name}")
        if info.file_size > MAX_IMAGE_BYTES and not name.endswith(".json"):
            raise DeckPackError("zip_invalid", f"ZIP 条目过大：{name}")
        total += info.file_size
        names.append(name)
    if len(set(names)) != len(names):
        raise DeckPackError("zip_invalid", "ZIP 包含重复条目")
    if set(names) != expected_entries():
        raise DeckPackError("zip_invalid", "ZIP 条目与固定资源清单不一致")
    if total > MAX_UNCOMPRESSED_BYTES:
        raise DeckPackError("zip_invalid", "ZIP 解压后体积超过限制")
    return infos


def _safe_extract(archive: zipfile.ZipFile, target: Path, infos: list[zipfile.ZipInfo]) -> None:
    target.mkdir(parents=True, exist_ok=True)
    root = target.resolve()
    for info in infos:
        destination = (root / info.filename).resolve()
        if root not in destination.parents:
            raise DeckPackError("unsafe_entry", f"ZIP 路径越界：{info.filename}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as source, destination.open("wb") as sink:
            shutil.copyfileobj(source, sink, 1024 * 1024)


def _write_active(data_dir: Path, version: str, manifest_sha: str) -> None:
    root = package_root(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "deckId": DECK_ID,
        "contentVersion": version,
        "manifestSha256": manifest_sha,
        "activatedAt": int(time.time()),
    }
    temp = root / f".active-{uuid.uuid4().hex}.json"
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, _active_file(data_dir))


def _read_active(data_dir: Path) -> dict[str, Any] | None:
    path = _active_file(data_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("deckId") != DECK_ID:
        return None
    version = data.get("contentVersion")
    if not isinstance(version, str) or not version:
        return None
    return data


def verify_active(
    data_dir: Path,
    *,
    allow_pending: bool = False,
    full: bool = False,
) -> dict[str, Any] | None:
    """校验当前激活版本；full=True 时重算 78 张图片摘要。"""

    active = _read_active(data_dir)
    if active is None:
        return None
    version = active["contentVersion"]
    directory = versions_dir(data_dir) / version
    if not directory.is_dir():
        return None
    try:
        if full:
            return verify_package(directory, expected_version=version, allow_pending=allow_pending)
        manifest_path = directory / "manifest.json"
        manifest_bytes = manifest_path.read_bytes()
        if sha256_bytes(manifest_bytes) != active.get("manifestSha256"):
            return None
        manifest = json.loads(manifest_bytes.decode("utf-8-sig"))
        validate_manifest(manifest, expected_version=version, allow_pending=allow_pending)
        for card in manifest["cards"]:
            if not (directory / card["image"]["path"]).is_file():
                return None
        return manifest
    except (OSError, DeckPackError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def active_version(data_dir: Path) -> str | None:
    manifest = verify_active(data_dir)
    if manifest is None:
        return None
    return str(manifest.get("contentVersion"))


def active_manifest(data_dir: Path) -> dict[str, Any] | None:
    return verify_active(data_dir)


def set_active(data_dir: Path, version: str) -> dict[str, Any]:
    """切换到已安装的某个版本（用于回滚/测试）；只校验清单与文件存在。"""

    directory = versions_dir(data_dir) / version
    if not directory.is_dir():
        raise DeckPackError("version_missing", f"版本 {version} 未安装")
    manifest_sha = sha256_bytes((directory / "manifest.json").read_bytes())
    manifest = verify_package(
        directory,
        expected_version=version,
        expected_manifest_sha=manifest_sha,
        allow_pending=True,
    )
    _write_active(data_dir, version, manifest_sha)
    return manifest


def image_path(data_dir: Path, relative: str, version: str | None = None) -> Path | None:
    """解析激活版本（或指定版本）下的图片路径，阻止路径穿越。"""

    if not relative:
        return None
    selected = version or active_version(data_dir)
    if not selected:
        return None
    root = (versions_dir(data_dir) / selected).resolve()
    candidate = (root / relative).resolve()
    if candidate == root or root not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None


def list_versions(data_dir: Path) -> list[str]:
    base = versions_dir(data_dir)
    if not base.is_dir():
        return []
    return sorted(child.name for child in base.iterdir() if child.is_dir() and not child.name.startswith("."))


def status(data_dir: Path) -> dict[str, Any]:
    manifest = active_manifest(data_dir)
    if manifest is None:
        return {"installed": False, "version": None, "deckId": DECK_ID}
    return {
        "installed": True,
        "version": manifest["contentVersion"],
        "deckId": DECK_ID,
        "versions": list_versions(data_dir),
        "root": str(versions_dir(data_dir) / manifest["contentVersion"]),
    }


def install_zip(
    data_dir: Path,
    zip_path: Path,
    *,
    expected_archive_sha: str = ARCHIVE_SHA256,
    expected_manifest_sha: str = MANIFEST_SHA256,
    expected_version: str = CONTENT_VERSION,
    allow_pending: bool = False,
) -> dict[str, Any]:
    """校验并安装一个固定版本 ZIP；失败时保留当前激活版本。"""

    zip_path = Path(zip_path)
    if not zip_path.is_file():
        raise DeckPackError("archive_missing", f"资源包不存在：{zip_path}")
    archive_size = zip_path.stat().st_size
    if archive_size > MAX_ARCHIVE_BYTES:
        raise DeckPackError("archive_size", "资源包超过大小限制")
    archive_sha = sha256_file(zip_path)
    if archive_sha != expected_archive_sha:
        raise DeckPackError("archive_sha", "资源包 SHA-256 与锁定值不一致")
    root = package_root(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    staging = root / f".staging-{uuid.uuid4().hex}"
    previous = active_version(data_dir)
    try:
        staging.mkdir(parents=True)
        try:
            with zipfile.ZipFile(zip_path) as archive:
                infos = _validate_zip_entries(archive)
                _safe_extract(archive, staging, infos)
        except zipfile.BadZipFile as exc:
            raise DeckPackError("zip_invalid", f"无法读取 ZIP：{exc}") from exc
        manifest = verify_package(
            staging,
            expected_version=expected_version,
            expected_manifest_sha=expected_manifest_sha,
            allow_pending=allow_pending,
        )
        version = manifest["contentVersion"]
        destination = versions_dir(data_dir) / version
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            try:
                verify_package(
                    destination,
                    expected_version=version,
                    expected_manifest_sha=expected_manifest_sha,
                    allow_pending=allow_pending,
                )
                shutil.rmtree(staging, ignore_errors=True)
            except DeckPackError:
                backup = versions_dir(data_dir) / f".broken-{version}-{uuid.uuid4().hex}"
                os.replace(destination, backup)
                os.replace(staging, destination)
        else:
            os.replace(staging, destination)
        _write_active(data_dir, version, expected_manifest_sha)
        return {
            "manifest": manifest,
            "version": version,
            "root": str(destination),
            "previousVersion": previous,
            "archiveSha256": archive_sha,
            "manifestSha256": expected_manifest_sha,
        }
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise


def download_archive(
    url: str,
    destination: Path,
    *,
    timeout: int = 60,
    proxy_url: str = "",
    max_bytes: int = MAX_ARCHIVE_BYTES,
) -> Path:
    """流式下载资源包到临时文件；只允许 http/https。"""

    if not url.startswith(("https://", "http://")):
        raise DeckPackError("download_failed", "下载地址必须是 http(s) URL")
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "MaiBot-Tarots-Plugin/2"})
    opener = None
    if proxy_url:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
        )
    try:
        response = opener.open(request, timeout=timeout) if opener else urllib.request.urlopen(request, timeout=timeout)
        with response, destination.open("wb") as sink:
            total = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise DeckPackError("archive_size", "下载资源包超过大小限制")
                sink.write(chunk)
    except DeckPackError:
        destination.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        destination.unlink(missing_ok=True)
        raise DeckPackError("download_failed", f"下载失败：{exc}") from exc
    return destination


def download_and_install(
    data_dir: Path,
    url: str = "",
    *,
    proxy_url: str = "",
    timeout: int = 60,
    expected_archive_sha: str = ARCHIVE_SHA256,
    expected_manifest_sha: str = MANIFEST_SHA256,
    expected_version: str = CONTENT_VERSION,
) -> dict[str, Any]:
    """下载固定版本 ZIP 并安装；失败不影响旧版本。"""

    target_url = (url or ARCHIVE_URL).strip()
    root = package_root(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    temp = root / f".download-{uuid.uuid4().hex}.zip.part"
    try:
        download_archive(target_url, temp, timeout=timeout, proxy_url=proxy_url)
        return install_zip(
            data_dir,
            temp,
            expected_archive_sha=expected_archive_sha,
            expected_manifest_sha=expected_manifest_sha,
            expected_version=expected_version,
        )
    finally:
        temp.unlink(missing_ok=True)


def ensure_available(
    data_dir: Path,
    *,
    url: str = "",
    auto_download: bool = True,
    proxy_url: str = "",
    timeout: int = 60,
    expected_archive_sha: str = ARCHIVE_SHA256,
    expected_manifest_sha: str = MANIFEST_SHA256,
    expected_version: str = CONTENT_VERSION,
) -> dict[str, Any]:
    """确保牌组资源可用：已安装直接返回；否则按需下载。"""

    manifest = active_manifest(data_dir)
    if manifest is not None:
        return manifest
    if not auto_download:
        raise DeckPackError(
            "not_installed",
            "牌组资源未安装；请在配置中允许自动下载，或用 /tarots import <本地zip路径> 导入。",
        )
    try:
        result = download_and_install(
            data_dir,
            url=url,
            proxy_url=proxy_url,
            timeout=timeout,
            expected_archive_sha=expected_archive_sha,
            expected_manifest_sha=expected_manifest_sha,
            expected_version=expected_version,
        )
    except DeckPackError as exc:
        raise DeckPackError(
            exc.code,
            f"{exc.message}\n可改用 /tarots import <本地zip路径> 导入同一版本资源包。",
        ) from exc
    return result["manifest"]


def cleanup_staging(data_dir: Path, max_age_seconds: int = 3600) -> int:
    """清理安装失败遗留的临时目录/下载文件。"""

    root = package_root(data_dir)
    if not root.is_dir():
        return 0
    removed = 0
    now = time.time()
    for child in root.iterdir():
        if not (child.name.startswith(".staging-") or child.name.startswith(".download-")):
            continue
        try:
            if now - child.stat().st_mtime < max_age_seconds:
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
            removed += 1
        except OSError:
            continue
    return removed
