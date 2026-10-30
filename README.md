# 塔罗牌插件

麦麦（MaiBot）的塔罗牌占卜插件：支持 **8 种牌阵**、**大/小阿卡纳**、**多套牌组**，卡面图片自动下载缓存，并用**麦麦自己的人设语气**解牌。

> - 原作者：[A0000Xz](https://github.com/A0000Xz)（本项目 1.x 版本作者）
> - 2.0.0 起由 [NethielChou](https://github.com/NethielChou) 接手维护（原作者已授权），完成到 MaiBot 1.3.x / 插件 SDK 2.x 的整体迁移
> - 协议：[AGPL-3.0](LICENSE)，继续沿用

## 功能

- **自然语言抽牌**：群里有人表示想抽塔罗/占卜（例如「爱丽丝抽一张塔罗」「帮我占个卜看看工作」），麦麦会自己判断并调用抽牌工具
- **人设解牌**：牌面结果交给麦麦本人，用她平时的语气解读（不是固定文案）
- **11 种牌阵**：单张、圣三角、时间之流、四要素、五牌阵、吉普赛十字、马蹄、六芒星、凯尔特十字、二选一、身心灵
- **抽牌范围**：全部 / 大阿卡纳 / 小阿卡纳（取决于牌组本身包含的牌）
- **多套牌组**：默认使用经典韦特牌组；牌组是纯 JSON，可以自己写、随时加
- **卡面缓存**：图片只在第一次抽到时下载，之后走本地缓存；支持代理
- **管理指令**：`/tarots list`、`/tarots switch <牌组>`、`/tarots cache`，仅限管理员 QQ
- **友好排版**：带 emoji 的牌阵结果 + 逐张牌义
- **牌阵布局合成图**：把整副牌按牌阵位置拼成一张图再发（也可配置成逐张发送）
- **自定义牌阵**：往插件 data 目录放一个 `formations.json` 就能加新牌阵，不用改代码
- **占卜追问**：记住最近一次抽牌 10 分钟，接着问「刚才那张牌什么意思」不用重抽
- **每日一牌**：`/tarots daily` 同一天同一人抽到同一张，所有人可用
- **牌组预览图**：`/tarots list` 回复后附带当前牌组的预览牌面

## 安装

1. 把本仓库文件夹放进 MaiBot 的 `plugins/` 目录（目录名随意）
2. 在 WebUI「插件扩展」里启用
3. 首次加载会自动生成 `config.toml`

## 使用

### 自然语言（推荐，普通群友可用）

直接说话即可，例如：

- `爱丽丝抽一张塔罗`
- `帮我抽三张塔罗牌看看最近运势`
- `用时间之流牌阵占卜一下这段感情`
- `帮我占卜一下明天的工作`

麦麦会自己决定是否抽牌、用哪个牌阵；没说牌阵时默认「单张」。
懂塔罗的群友可以直接说牌阵名或常见说法：「用凯尔特十字看看」「抽三张」「过去现在未来」「做个二选一」「身心灵牌阵」等都会被识别。
关键词兜底：消息里同时出现「塔罗/占卜」和「抽/看/问/算/测/来/帮」时，插件会直接抽牌，避免她只聊天不抽牌。

### 管理指令（仅管理员）

| 指令 | 说明 |
|------|------|
| `/tarots daily` | 每日一牌（所有人可用，同一天同一人结果一致） |
| `/tarots formations` | 查看全部牌阵（所有人可用，含自定义牌阵） |
| `/tarots newformation <名称> <张数> <位置1\|位置2>` | 管理员在群里直接新增牌阵，即时生效 |
| `/tarots list` | 查看可用牌组、当前牌组和使用状态 |
| `/tarots switch <牌组名>` | 切换当前牌组（需已启用） |
| `/tarots allow <牌组名>` | 确认你有使用权后，解锁一套带 🔒 的牌组 |
| `/tarots lock <牌组名>` | 撤销解锁 |
| `/tarots cache` | 预下载当前牌组所有卡面（首次使用建议先跑一次） |

管理员 QQ 号填在配置的 `[permissions] admin_users`。
`/tarots list` 会把**已启用牌组排在前面、带锁牌组排在后面**，每个牌组一行并附版权说明；解锁会记录到插件 data 目录的 `state.json`，重启不丢。

## 牌阵

| 牌阵 | 张数 | 位置 |
|------|------|------|
| 单张 | 1 | 现状 |
| 圣三角 | 3 | 现状 / 愿望 / 行动 |
| 时间之流 | 3 | 过去 / 现在 / 未来 |
| 四要素 | 4 | 火 / 气 / 水 / 土 |
| 五牌阵 | 5 | 现在或主要问题 / 过去的影响 / 未来 / 主要原因 / 行动可能带来的结果 |
| 吉普赛十字 | 5 | 对方的想法 / 你的想法 / 相处中存在的问题 / 二人目前的环境 / 关系发展的结果 |
| 马蹄 | 6 | 现状 / 可预知的情况 / 不可预知的情况 / 即将发生的 / 结果 / 问卜者的主观想法 |
| 六芒星 | 7 | 过去 / 现在 / 对策 / 环境 / 态度 / 预测结果 等 |

> 牌阵定义在 `tarot_jsons/formation.json`，可以自己加；也可以不改仓库，直接在插件 data 目录放 `formations.json` 扩展，见下文「自定义牌阵」。

## 牌组

`tarot_jsons/<牌组名>/tarots.json` 就是一套牌组，自带三套：

| 牌组 | 张数 | 图片版权说明 | 默认 |
|------|------|--------------|------|
| `classical` 经典韦特 | 78 | 韦特塔罗原版已进入公有领域；图源仓库为 MIT | ✅ 启用 |
| `elaine` Elaine 韦特公版 | 78 | Rider-Waite-Smith 公版牌面（1909）；图源仓库仅托管公版扫描图 | ✅ 启用 |
| `bilibili` B站幻星集 | 78 | **2233 娘形象版权归 B 站**，非开放版权，仅限个人非商业娱乐 | ⛔ 默认关闭 |
| `east` 东方塔罗 | 22 | 东方 Project 二次创作，需遵守 ZUN 二创规约（非商业、注明来源） | ⛔ 默认关闭 |

- 默认启用 `classical` 与 `elaine`（都是公版韦特）
- 带 🔒 的牌组是**锁定**的：`/tarots list` 会显示它的版权说明；直接 `/tarots switch` 会被拒绝并提示原因
- 确认你拥有某套牌的使用权（或属于非商业允许场景）后，执行 `/tarots allow <牌组名>` 即可解锁切换与缓存；`/tarots lock <牌组名>` 可撤销
- 也可以在配置 `[cards] use_cards` 里直接写上牌组名永久启用

### 自己加牌组

1. 在 `tarot_jsons/` 下新建文件夹，文件夹名就是牌组名
2. 放入 `tarots.json`：

```json
{
  "_meta": {
    "card_types": "大阿卡纳",
    "total_cards": 22,
    "description": "我的牌组",
    "base_url": "https://example.com/tarot/"
  },
  "0": {
    "name": "愚者",
    "info": {
      "description": "正位牌义",
      "reverseDescription": "逆位牌义",
      "imgUrl": "MajorArcana/0.png"
    }
  }
}
```

说明：卡牌 ID `0`–`21` 是大阿卡纳，`22`–`77` 是小阿卡纳；`imgUrl` 是相对 `base_url` 的图片路径。
3. 把新牌组名加进配置 `[cards] use_cards`，然后 `/tarots switch <牌组名>` 或 `/tarots cache`，**不用重启**

## 自定义牌阵（不改代码）

两种方式：

1. **群里直接建**（管理员）：`/tarots newformation 我的三连 3 起因|经过|结果`，写入 data 目录的 `formations.json` 并即时生效
2. **手写 JSON**：在插件 data 目录放 `formations.json`，重启插件或触发配置更新后生效

在插件 data 目录（例如 `MaiBot/data/plugins/github.a0000xz.maibot-tarots-plugin/`）放一个 `formations.json`：

```json
{
  "我的三连": {
    "cards_num": 3,
    "is_cut": true,
    "represent": [["起因", "经过", "结果"]]
  }
}
```

- `cards_num`：抽几张（1~10）
- `is_cut`：是否可能抽到逆位
- `represent`：牌位名称，数量不够会自动补「位置N」
- 保存后重启插件（或触发一次配置更新）即可用，自然语言和 Tool 都能识别新牌阵；未预置布局的牌阵会自动用网格排版生成布局图

## 配置

`config.toml` 由 MaiBot 自动生成，不纳入版本库；参考 `config.example.toml`。

WebUI 的插件设置页按 **「常用 / 抽牌 / 高级」** 三个页签组织，每个字段都有中文标签和填写提示：

- **常用**：牌组设置、管理员 QQ
- **抽牌**：功能开关、抽牌体验（关键词兜底、原始文本）
- **高级**：下载代理、emoji/图片容错、插件信息

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `plugin.enabled` | `true` | 是否启用 |
| `plugin.config_version` | `2.0.0` | 配置版本 |
| `components.enable_tarots` | `true` | 抽牌工具 + 关键词兜底 |
| `components.enable_commands` | `true` | `/tarots` 指令 |
| `proxy.enable_proxy` | `false` | 下载卡面时用代理 |
| `proxy.proxy_url` | `""` | 代理地址 |
| `cards.using_cards` | `classical` | 默认牌组 |
| `cards.use_cards` | `["classical"]` | 允许使用的牌组 |
| `adjustment.enable_original_text` | `false` | 额外发送原始牌面文本 |
| `adjustment.enable_keyword_fallback` | `true` | 关键词兜底抽牌 |
| `adjustment.emoji_enabled` | `true` | 结果用 emoji |
| `adjustment.strict_images` | `true` | 卡面缺失时中止（false = 纯文字降级） |
| `adjustment.layout_image` | `true` | 把整副牌阵合成一张布局图发送 |
| `adjustment.show_deck_thumbnail` | `true` | `/tarots list` 附带当前牌组预览图 |
| `permissions.admin_users` | `[]` | 管理指令 QQ 白名单 |

## 常见问题

**卡面下载失败怎么办？**
先确认部署机网络能访问牌组 `base_url` 指向的图源；必要时在配置里开启代理，然后 `/tarots cache` 重新缓存。缓存过的图片后续不再联网。

**为什么默认没有 bilibili 牌组？**
B 站 2233 娘形象不是开放版权。为了不默认分发风险素材，2.0.0 把它改为「可选、默认关闭」。

**旧版（1.x）配置怎么办？**
2.0.0 是整体迁移到新插件框架的版本，配置项重新生成；把旧配置里的 QQ 号、代理、常用牌组按上表填回即可。

## 兼容性

- MaiBot `1.3.13+`（实测 1.3.18）
- 插件 SDK `2.16.0+`（实测 2.19.0）
- 逆位图片旋转需要 Pillow（MaiBot 环境自带；无 Pillow 时逆位会退化为正位图）

## 许可与致谢

- 本项目以 [AGPL-3.0](LICENSE) 协议开源，沿用原作者 A0000Xz 的协议
- 玩法参考 [FloatTech/ZeroBot-Plugin](https://github.com/FloatTech/ZeroBot-Plugin) 的塔罗牌插件
- 牌面数据与图源见各 `tarots.json` 的 `_meta`；`classical` 使用公有领域韦特牌
