#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NoneBot2 入口文件。

加载顺序：
  1. 初始化 NoneBot
  2. 注册 OneBot v11 适配器（作为 WebSocket 客户端连 LLBot 的正向 WS）
  3. 加载本地插件 plugins/ 下的插件（自行开发 + vendored）
  4. 加载第三方插件：舞萌DX查分

第三方插件的依赖声明见 pyproject.toml 的 [tool.nonebot.plugins]；
本地插件不走该表（由 bot.py 用 Path 显式加载），避免与 plugin_dirs 重复加载。
"""
import nonebot
from pathlib import Path

from nonebot.adapters.onebot.v11 import Adapter as OneBotAdapter

# 1. 初始化（会读取 .env / .env.prod 及环境变量）
nonebot.init()

# 1.5 预加载基础插件（必须在任何 import localstore 的插件之前！）
#     背景：qqbot_economy/data.py 在模块加载时 import nonebot_plugin_localstore，
#     若 localstore 尚未作为插件注册，它会停留在半初始化状态（economy 有
#     try/except 兜底所以静默降级，不报错），随后 rollpig 的
#     require("nonebot_plugin_localstore") 就会失败：
#     "Module nonebot_plugin_localstore is not loaded as a plugin!"
#     因此这里显式先把两个基础插件注册好，再加载业务插件。
nonebot.load_plugin("nonebot_plugin_localstore")
nonebot.load_plugin("nonebot_plugin_apscheduler")

# 2. 注册 OneBot v11 适配器
driver = nonebot.get_driver()
driver.register_adapter(OneBotAdapter)

# 3. 本地插件（官方文档：本地插件用 pathlib.Path 加载，字符串会被当作模块名）
#    https://nonebot.dev/docs/tutorial/create-plugin
nonebot.load_plugin(Path("plugins/qqbot_toolbox"))

# 4. 群聊管理：禁言/踢人/全体禁言/撤回 + 按群功能开关
nonebot.load_plugin(Path("plugins/qqbot_group"))

# 复读与「我说你说」教学。
# 会把 repeat/teach 注册进 qqbot_group 的按群功能开关，
# 因此必须排在 qqbot_group 之后加载。
nonebot.load_plugin(Path("plugins/qqbot_repeat"))

# 吐槽插件：/骂（默认关闭，按群开关）
nonebot.load_plugin(Path("plugins/qqbot_roast"))

# 5. 表情包管理：戳一戳随机回图 + 金句库
nonebot.load_plugin(Path("plugins/qqbot_meme"))
# B站视频解析：GreaterLG70/nonebot-plugin-bilibili，vendored 进来是为了避开
# PyPI 上同名的 mengbingnaixi「动态订阅」插件（同名不同物，pip 会装错）
nonebot.load_plugin(Path("plugins/nonebot_plugin_bilibili"))
# 基础服务：货币（月币/荣耀）+ 个人信息，必须早于消费它的插件
nonebot.load_plugin(Path("plugins/qqbot_core"))
# 签到金币 + 捕鱼（玩法层，通过 require("qqbot_core") 调用货币接口）
nonebot.load_plugin(Path("plugins/qqbot_economy"))

# 图片帮助菜单：/help、/帮助、/菜单（依赖 localstore / alconna / htmlrender）
nonebot.load_plugin("nonebot_plugin_picmenu_next")

# 4. 第三方插件
nonebot.load_plugin("nonebot_plugin_maimaidx")
# 今日小猪：每日抽猪 + 猪圈收藏 + 烤群友（Felis2026/nonebot-plugin-rollpig-plus）
# AI 烤猪/云存储/群日报均默认关闭，数据走 localstore（容器 /app/data，已持久化）
nonebot.load_plugin("nonebot_plugin_rollpig_plus")

# 拼音缩写翻译：/nbnhhsh、/好好说话（能不能好好说话 API）
nonebot.load_plugin("nonebot_plugin_nbnhhsh")

# 小猪宇宙：在 rollpig 之上扩展（图鉴成就 → Kleos；后续 DX PASS/斗兽场/牧场）
# 依赖 rollpig（读猪圈）与 economy（发 Kleos），必须排在两者之后加载
nonebot.load_plugin(Path("plugins/qqbot_pigverse"))

# AI 问答：喊「小e <问题>」提问，可联网搜索、可看图（qqbot_ai）
nonebot.load_plugin(Path("plugins/qqbot_ai"))

# 5. 启动
nonebot.run()
