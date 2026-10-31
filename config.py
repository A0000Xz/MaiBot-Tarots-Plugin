"""塔罗工坊配置模型。"""

from maibot_sdk import Field, PluginConfigBase



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
    layout_image: bool = Field(default=True, description="把整副牌阵合成一张布局图发送（关闭则逐张发送）")
    single_card_plain: bool = Field(
        default=True,
        description="单张抽牌直接发牌面图，不套牌阵桌布背景（便于区分单抽与牌阵）",
    )
    merged_message: bool = Field(
        default=True,
        description="把牌面、牌义和解读合并成一条转发消息发送，减少刷屏",
    )
    show_deck_thumbnail: bool = Field(default=True, description="/tarots list 附带当前牌组预览图")


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

