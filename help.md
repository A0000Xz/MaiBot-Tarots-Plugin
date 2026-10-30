# 如何添加自己的牌组

1. 在 `tarot_jsons/` 下新建一个文件夹，**文件夹名就是牌组名**
2. 在里面放一个 `tarots.json`，格式如下（`0`–`21` 为大阿卡纳，`22`–`77` 为小阿卡纳）：

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

- `base_url` + `imgUrl` 组成卡面图片的完整下载地址
- 图片会在第一次抽到时下载并缓存到插件 data 目录的 `tarots_cache/<牌组名>/`
- 只有 `_meta.card_types` 声明的范围会被抽到（`全部` / `大阿卡纳` / `小阿卡纳`）

3. 把新牌组名加进插件配置 `[cards] use_cards`，保存后**不用重启**
4. 用 `/tarots switch <牌组名>` 切换，或用 `/tarots cache` 预缓存全部卡面

> 注意：请只使用你有权使用的卡面图片。公版（如 1909 韦特塔罗、马赛塔罗）或你自制/获授权的图源最稳妥。


## 自定义牌阵（不用改代码）

最快的办法是在群里（管理员）执行：

```
/tarots newformation 我的三连 3 起因|经过|结果
```

会写入 data 目录的 `formations.json` 并即时生效。也可以手写下面的 JSON。

在插件 data 目录放 `formations.json`：

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
- `represent`：牌位名称
- 保存后重启插件或让配置热更新一次即可生效；未预置布局的牌阵会自动用网格排版生成布局图
