# 塔罗工坊 · 扩展手册

面向想自己加**牌组**或**牌阵**的进阶用户。普通使用说明见 [README.md](README.md)。

## 一、自定义牌组

### 1. 目录结构

每套牌组是 `tarot_jsons/` 下的一个文件夹，**文件夹名就是牌组名**：

```
tarot_jsons/
└── my_deck/
    ├── tarots.json        # 必需：卡牌数据
    └── images/            # 可选：本地卡面（配合 local_images 使用）
        ├── m00.png
        └── ...
```

### 2. `_meta` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `card_types` | 字符串 | 牌组包含的范围：`全部` / `大阿卡纳` / `小阿卡纳` |
| `total_cards` | 数字 | 卡牌总数（展示用） |
| `description` | 字符串 | 牌组描述，会显示在 `/tarots list` |
| `base_url` | 字符串 | 卡面图源前缀；远程下载时与 `imgUrl` 拼接 |
| `local_images` | 布尔 | `true` = 卡面随插件内置，直接读本地文件、不联网 |
| `notice` | 字符串 | 版权/使用提示，牌组被锁定时会显示 |

### 3. 卡牌 ID 规则

| ID | 含义 |
|---|---|
| `0` – `21` | 大阿卡纳（愚者 … 世界） |
| `22` – `77` | 小阿卡纳：圣杯 22–35、星币 36–49、宝剑 50–63、权杖 64–77 |

每张牌的结构：

```json
"0": {
  "name": "愚者",
  "info": {
    "description": "正位牌义",
    "reverseDescription": "逆位牌义",
    "imgUrl": "MajorArcana/0.png"
  }
}
```

### 4. 示例 A：远程图源（首次抽到才下载并缓存）

```json
{
  "_meta": {
    "card_types": "大阿卡纳",
    "total_cards": 22,
    "description": "我的远程牌组",
    "base_url": "https://example.com/tarot/"
  },
  "0": {
    "name": "愚者",
    "info": {
      "description": "新的开始、冒险",
      "reverseDescription": "鲁莽、时机不对",
      "imgUrl": "MajorArcana/0.png"
    }
  }
}
```

`base_url` + `imgUrl` = 完整下载地址，图片缓存在插件 data 目录的 `tarots_cache/<牌组名>/`。

### 5. 示例 B：本地内置图源（不联网，适合自制/授权牌面）

```json
{
  "_meta": {
    "card_types": "大阿卡纳",
    "total_cards": 22,
    "description": "我的原创牌组",
    "local_images": true
  },
  "0": {
    "name": "愚者",
    "info": {
      "description": "新的开始、冒险",
      "reverseDescription": "鲁莽、时机不对",
      "imgUrl": "images/m00.png"
    }
  }
}
```

`imgUrl` 是相对牌组目录的路径；图片放在 `tarot_jsons/my_deck/images/`。
`local_images: true` 时插件**不会发起任何网络请求**。

### 6. 启用新牌组

1. 把牌组名加进配置 `[cards] use_cards`（或在群里让管理员 `/tarots allow <牌组名>`）
2. `/tarots switch <牌组名>` 切换，或 `/tarots cache` 预缓存全部卡面

> 版权：请只使用你有权使用的卡面图片。公版（1909 韦特、马赛塔罗等）、你自制或已获授权的图源最稳妥；版权受限的牌组请在 `_meta.notice` 写清说明，并保持默认锁定。

## 二、自定义牌阵

### 1. 群里直接建（管理员，即时生效）

```
/tarots newformation 我的三连 3 起因|经过|结果
```

- 牌位分隔符支持 `|` `/` `，` `、`
- 张数 1–10；牌位不够会自动补「位置N」
- 会写入插件 data 目录的 `formations.json`，并立即生效（无需重启）

### 2. 手写 `formations.json`

文件位置：插件 data 目录，例如
`MaiBot/data/plugins/github.a0000xz.maibot-tarots-plugin/formations.json`

```json
{
  "我的三连": {
    "cards_num": 3,
    "is_cut": true,
    "represent": [["起因", "经过", "结果"]]
  }
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `cards_num` | 数字 | 抽几张，1–10 |
| `is_cut` | 布尔 | `true` = 可能抽到逆位；`false` = 全部正位 |
| `represent` | 数组的数组 | 牌位名称，填 `represent[0]` 这一层 |

保存后重启插件或让配置热更新一次即可生效，自然语言和 Tool 都能识别新牌阵。

### 3. 布局图

- 内置 11 种牌阵有专门排版（单张、圣三角、时间之流、四要素、五牌阵、吉普赛十字、马蹄、六芒星、凯尔特十字、二选一、身心灵）
- 自定义/未知牌阵自动按网格排版生成桌布风布局图
- 不想拼图可在配置里关掉 `adjustment.layout_image`，改为逐张发送

## 三、常见扩展问题

**牌组不出现怎么办？**
检查三件事：目录名与 `tarots.json` 存在、牌组名已加进 `[cards] use_cards`（或 `/tarots allow`）、JSON 是 UTF-8 且能被解析。

**卡面下载失败？**
确认部署机网络能访问 `base_url` 图源；必要时配 `[proxy]`，再 `/tarots cache`。已缓存图片不再联网。

**图片格式/大小有要求吗？**
支持 png / jpg / jpeg / webp；单张建议 ≤1MB。逆位由插件旋转 180°，不需要单独画逆位图。

**自定义牌阵自然语言识别不到？**
先用 `/tarots formations` 确认牌阵已经加载；自然语言里要带上牌阵名（或常见说法），否则默认按「单张」处理。

---

许可与致谢见 [README.md](README.md)；更新记录见 [CHANGELOG.md](CHANGELOG.md)。
