# nonebot-plugin-bilibili · B站助手

一个给 [NoneBot2](https://github.com/nonebot/nonebot2) 用的 B站（bilibili）插件：
**群里艾特机器人 + 链接就自动解析视频**，另外还能查**今日排行榜**和**UP主数据**。

自研原因：社区现有的 B站插件（`mengshouer/nonebot_plugin_analysis_bilibili` 等）多停在
2023~2024 年，没做 B站的 wbi 签名与反爬适配，实测已大面积失效。本插件的所有接口调用都
于 **2026-09 在真实服务器环境实测通过**，并针对风控做了重试与降级。

## 功能

| # | 功能 | 触发方式 |
|---|---|---|
| 1 | **视频解析**：标题 / 简介 / UP主（含粉丝数）/ 播放 / 点赞 / 投币 / 收藏 / 弹幕 / 评论 / 分享 / 时长 | 群里 `@机器人 + 链接`，或私聊直接发链接 |
| 2 | **排行榜**：全站或 16 个分区，可指定条数 | `/bilibili rank [分区] [条数]` |
| 3 | **UP主查询**：粉丝 / 稿件 / 关注 / 获赞 | `/bilibili up <UID 或 昵称>` |

支持的链接形态：完整链接 `bilibili.com/video/BV...`、av 号、`BV` 裸号、
以及 **`b23.tv` 短链**。

## 命令清单

```
@机器人 https://www.bilibili.com/video/BVxxxx    ← 解析视频（群里必须@，私聊不用）
@机器人 https://b23.tv/xxxx                      ← 短链也行

/bilibili                    查看帮助
/bilibili rank               今日全站榜 TOP10
/bilibili rank 动画 5        指定分区 + 条数
/bilibili up 影视飓风        按昵称查 UP 主
/bilibili up 946974          按 UID 查 UP 主
/bilibili BV1GJ411x7h7       命令式解析视频
```

命令别名：`/bili`、`/B站`、`/b站`。群聊里也可以 `@机器人 /bilibili rank 游戏`。

可用分区：`全站 动画 番剧 国创 音乐 舞蹈 游戏 知识 科技 运动 汽车 生活 美食 动物 影视 娱乐`

## 安装

```bash
pip install nonebot2 nonebot-adapter-onebot httpx
```

任选其一：

```python
# 方式 A：作为包加载
nonebot.load_plugin("nonebot_plugin_bilibili")

# 方式 B：把 nonebot_plugin_bilibili/ 整个目录拷进你的 plugins/ 下
```

无需任何配置项，装完即可用。依赖 `httpx` 与 OneBot V11 适配器。

## 输出示例

```
📺 【官方 MV】Never Gonna Give You Up
UP：索尼音乐中国（UID 486906719）· 粉丝 1738.3万
时长 3:33

▶️ 播放 1.1亿　💬 弹幕 10.2万　💭 评论 23.2万
👍 点赞 291.1万　🪙 投币 126.7万
⭐ 收藏 153.1万　🔁 分享 49.0万
🔗 https://www.bilibili.com/video/BV1GJ411x7h7
```

## 已知限制

- **UP主“总播放量”拿不到**：B站的 `x/space/upstat` 已被掏空（加不加 wbi 签名都返回空字段），
  APP 端接口要签名、web 端 `arc/search` 直接 -352。所以 UP主查询只提供
  **粉丝 / 稿件 / 关注 / 被关注 / 获赞**，不编造播放量。
- **`-352` 是 B站频率风控**：插件内置指数退避重试（1.2s / 2.4s）；若分区榜仍被拦，
  会自动降级返回全站热门榜并说明原因。

## 给开发者的踩坑记录（2026-09 实测）

1. **必须带 UA + Referer**：裸请求一律 HTTP 412 反爬 HTML，且不是 JSON。
2. **`b23.tv` 短链只能读 302 的 `Location` 头**：一旦跟随进 `www.bilibili.com` 页面就吃 412。
   同理，插件从不请求任何 B站 HTML 页面，只走 API。
3. **排行榜接口已经分化**：裸的 `x/web-interface/ranking/v2` 已 -352 风控，
   全站榜改用 `x/web-interface/popular`；分区榜需 wbi 签名后才可用。
4. **按昵称搜 UP 主必须 wbi 签名**（`x/web-interface/wbi/search/type`），否则 412。
5. **wbi 的 `mixin_key` 每天变**，按天缓存即可，取自 `x/web-interface/nav`。
6. `-352 / -509` 多数是**瞬时**频率限制，不是签名错了 —— 改签名逻辑前先在服务器上连打 10 次确认。

## License

MIT © 牧歌（survoid）
