#!/bin/sh
# 启动前校正依赖：
#   - 某些包（nbnhhsh / platformdirs / ddgs）是镜像外补装的，镜像重建或被覆盖后会丢
#   - 这里做一次幂等的缺失检查，缺什么补什么，再交给 bot.py
set -e

need_install=""
python - <<'PY' || need_install=1
import sys

missing = []
try:
    import nonebot_plugin_nbnhhsh  # noqa: F401
except Exception:
    missing.append("nonebot-plugin-nbnhhsh")
try:
    import platformdirs  # noqa: F401
except Exception:
    missing.append("platformdirs")
try:
    import ddgs  # noqa: F401  （qqbot_ai 联网搜索）
except Exception:
    missing.append("ddgs")

if missing:
    print(f"[entrypoint] 缺少依赖：{missing}")
    sys.exit(1)
PY

if [ -n "$need_install" ]; then
    pip install --no-cache-dir "pillow<13" nonebot-plugin-nbnhhsh platformdirs ddgs
fi

exec "$@"
