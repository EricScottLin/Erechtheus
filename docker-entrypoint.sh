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
try:
    import Crypto  # noqa: F401  （舞萌直连：机台协议加解密）
except Exception:
    missing.append("pycryptodome")
try:
    import maimai_chn  # noqa: F401  （舞萌直连：vendor/MaimaiDXCHN 只读挂载）
    import maimai_chn.chat  # noqa: F401  （ChatBridge 在 0.4.0 之后的分支上）
except Exception as exc:
    print(f"[entrypoint] maimai_chn 不可用：{type(exc).__name__}: {exc}")

if missing:
    print(f"[entrypoint] 缺少依赖：{missing}")
    sys.exit(1)
PY

if [ -n "$need_install" ]; then
    pip install --no-cache-dir "pillow<13" nonebot-plugin-nbnhhsh platformdirs ddgs "pycryptodome>=3.20,<4"
fi

exec "$@"
