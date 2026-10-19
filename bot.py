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

# 2. 注册 OneBot v11 适配器
driver = nonebot.get_driver()
driver.register_adapter(OneBotAdapter)

# 3. 本地插件（官方文档：本地插件用 pathlib.Path 加载，字符串会被当作模块名）
#    https://nonebot.dev/docs/tutorial/create-plugin
nonebot.load_plugin(Path("plugins/qqbot_toolbox"))
# B站视频解析：GreaterLG70/nonebot-plugin-bilibili，vendored 进来是为了避开
# PyPI 上同名的 mengbingnaixi「动态订阅」插件（同名不同物，pip 会装错）
nonebot.load_plugin(Path("plugins/nonebot_plugin_bilibili"))
# 签到金币 + 捕鱼
nonebot.load_plugin(Path("plugins/qqbot_economy"))

# 4. 第三方插件
nonebot.load_plugin("nonebot_plugin_maimaidx")
# 今日小猪：每日抽猪 + 猪圈收藏 + 烤群友（Felis2026/nonebot-plugin-rollpig-plus）
# AI 烤猪/云存储/群日报均默认关闭，数据走 localstore（容器 /app/data，已持久化）
nonebot.load_plugin("nonebot_plugin_rollpig_plus")

# 5. 启动
nonebot.run()
