"""WebUI 配置页布局。

只重组 SDK 生成的 Schema（页签 / 分组 / 标签 / 提示），不改配置模型、默认值和 TOML 路径。
"""

from dataclasses import dataclass
from typing import Any



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
            "merged_message": "合并消息发送（防刷屏）",
        },
    ),
    "draw_advanced": _UiSection(
        source="adjustment",
        title="呈现细节",
        description="emoji、布局图与图片容错，一般保持默认即可。",
        fields={
            "emoji_enabled": "结果使用 emoji",
            "strict_images": "卡面缺失时中止占卜",
            "layout_image": "牌阵布局合成图",
            "single_card_plain": "单张直接发牌面",
            "show_deck_thumbnail": "牌组列表附预览图",
        },
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
    "adjustment.merged_message": "开：把标题、牌面图、牌义、人设解读合并成一条转发消息发送，避免一大堆字刷屏；关：分条发送。",
    "adjustment.emoji_enabled": "结果标题与正逆位前的 emoji。",
    "adjustment.strict_images": "开：任一张卡面下载失败就中止占卜；关：降级为纯文字解读。",
    "adjustment.layout_image": "开：把整副牌按牌阵位置拼成一张桌布背景布局图再发（更像真的牌阵）；关：逐张发送牌面。",
    "adjustment.single_card_plain": "开：单张抽牌直接发牌面图，不加牌阵桌布背景——一眼区分「单抽」和「牌阵」。",
    "adjustment.show_deck_thumbnail": "/tarots list 回复后附一张当前牌组的预览图。",
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

