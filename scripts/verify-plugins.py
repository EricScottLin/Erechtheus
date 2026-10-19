"""验证 NoneBot2 加载适配器 + 两个插件，并统计注册的命令/matcher。

用法： .venv/bin/python scripts/verify-plugins.py
宿主机测试时需 MAIMAIDX_PATH 指向宿主机路径（环境变量优先级高于 .env 文件）。
"""
import os
import sys

# 宿主机 venv 测试：static 在仓库里；Docker 容器内由 .env.prod 提供 /static
if len(sys.argv) > 1:
    os.environ["MAIMAIDX_PATH"] = sys.argv[1]
elif not os.environ.get("MAIMAIDX_PATH"):
    os.environ["MAIMAIDX_PATH"] = "/srv/qqbot/maimaidx-static/static"

import nonebot

nonebot.init()

from nonebot.adapters.onebot.v11 import Adapter as OneBotAdapter

driver = nonebot.get_driver()
driver.register_adapter(OneBotAdapter)

PLUGINS = ["nonebot_plugin_maimaidx", "nonebot_plugin_bilibili"]

ok, fail = [], []
for p in PLUGINS:
    try:
        name = nonebot.load_plugin(p)
        if name:
            ok.append(p)
            print(f"[OK]   {p}  -> {name}")
        else:
            fail.append(p)
            print(f"[FAIL] {p}  load_plugin 返回 None")
    except Exception as e:
        fail.append(p)
        print(f"[ERR]  {p}: {type(e).__name__}: {e}")

# 也加载内置 echo 做回显测试
try:
    nonebot.load_builtin_plugins("echo")
    print("[OK]   builtin echo")
except Exception as e:
    print(f"[ERR]  builtin echo: {type(e).__name__}: {e}")

print("\n" + "=" * 60)
print("已加载插件清单：")
for p in sorted(nonebot.get_loaded_plugins(), key=lambda x: x.name):
    print(f"  - {p.name:35} matcher={len(p.matcher)}")

print("\n各插件 matcher / 命令统计：")
for p in PLUGINS + ["echo"]:
    plugin = nonebot.get_plugin(p)
    if plugin is None:
        continue
    print(f"\n### {p}   ({len(plugin.matcher)} 个 matcher)")
    for m in plugin.matcher:
        print(f"    type={m.type:10} module={m.module_name}")

print("\n" + "=" * 60)
print(f"RESULT: {'PASS' if ok and not fail else 'FAIL'}")
print(f"  loaded : {ok}")
print(f"  failed : {fail}")
sys.exit(0 if ok and not fail else 1)
