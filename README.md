# MaiBot-Tarot-Action-plugins
这是给MaiM-with-u项目开发的一个伪抽塔罗牌插件

其本质为麦麦的action，现在已适配mmc0.7.1-5的插件系统。

参考了https://github.com/FloatTech/ZeroBot-Plugin
的塔罗牌插件进行开发

卡牌图片来自于https://github.com/FloatTech/zbpdata

在此鸣谢[ZeroBot插件开发组](https://github.com/FloatTech)
提供的抽牌功能思路，塔罗牌数据和卡牌图片仓库支持

在此鸣谢MaiM-with-u开发组人员的指导和帮助

完整的文件结构为tarots_action这个大文件夹，直接将其放入src/plugins中就能用。

使用时需指定抽牌方式和抽牌范围，目前已默认支持的有

牌阵："单张", "圣三角", "时间之流","四要素","五牌阵","吉普赛十字","马蹄","六芒星"

如果没有明确指定，默认抽"单张"。

范围："全部", "大阿卡纳", "小阿卡纳"

如果没有明确指定，默认抽"全部"。

注意，本插件的图片来源是Github的仓库，因此需要你的麦麦部署的网络环境足够访问Github。

不过不用担心，这个插件有缓存机制，发过的图片都会被缓存在插件的tarots_cache文件夹中，如果你缓存了全部的卡牌图片，那么你就不用担心连不上Github怎么办了。

目前main分支支持070正式版，073请使用dev分支。
