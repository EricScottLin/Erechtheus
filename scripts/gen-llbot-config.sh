#!/usr/bin/env bash
# ============================================================
#  生成 LLBot 的 OneBot v11 配置文件
#
#  LLBot 的配置文件是  <数据目录>/config_<QQ号>.json
#  （见 llbot.js: configPath = DATA_DIR/config_<uin>.json）。
#  该文件存在时优先于镜像内置的 default_config.json，
#  因此可以在「QQ 登录之前」就把协议配置写好 —— 登录成功后
#  Onebot11Adapter 加载，立刻按此配置起正向 WS 服务。
#
#  用法：./scripts/gen-llbot-config.sh    （从仓库根目录执行）
#  依赖：仓库根目录的 .env
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"

[ -f "$ENV_FILE" ] || { echo "缺少 $ENV_FILE，请先 cp .env.example .env"; exit 1; }
set -a; . "$ENV_FILE"; set +a

QQ="${LLBOT_QQ:-}"
if [ -z "$QQ" ]; then
  echo "!! .env 中 LLBOT_QQ 为空。"
  echo "   请填入用于登录的 QQ 小号后重跑；该 QQ 号同时决定配置文件名 config_<QQ>.json"
  exit 2
fi
case "$QQ" in ''|*[!0-9]*) echo "!! LLBOT_QQ 必须是纯数字"; exit 2;; esac

DATA_DIR="${LLBOT_DATA_DIR:-$ROOT/llbot-data}"
mkdir -p "$DATA_DIR"

TOKEN="${LLBOT_OB11_ACCESS_TOKEN:-}"
if [ -z "$TOKEN" ]; then echo "!! LLBOT_OB11_ACCESS_TOKEN 为空"; exit 2; fi

CFG="$DATA_DIR/config_${QQ}.json"

# ---- 备份已有配置（登录态在里面，勿丢）----
if [ -f "$CFG" ]; then
  cp -a "$CFG" "$CFG.bak.$(date +%Y%m%d_%H%M%S)"
  echo "已备份原配置 -> $CFG.bak.*"
fi

python3 - "$CFG" "$QQ" "$LLBOT_OB11_WS_HOST" "$LLBOT_OB11_WS_PORT" "$TOKEN" \
                 "$LLBOT_WEBUI_HOST" "$LLBOT_WEBUI_PORT" <<'PY'
import json, sys, os
cfg_path, qq, ws_host, ws_port, token, webui_host, webui_port = sys.argv[1:8]

# 以 LLBot 内置 default_config.json 的完整结构为基底，避免缺字段
base = {
    "webui": {"enable": True, "host": "127.0.0.1", "port": 3080},
    "milky": {"enable": False, "reportSelfMessage": False,
              "http": {"host": "127.0.0.1", "port": 3010, "prefix": "", "accessToken": ""},
              "webhook": {"urls": [], "accessToken": ""}},
    "satori": {"enable": False, "host": "127.0.0.1", "port": 5600, "token": ""},
    "ob11": {"enable": True, "connect": [
        {"type": "ws", "enable": False, "host": "127.0.0.1", "port": 3001,
         "heartInterval": 60000, "token": "", "reportSelfMessage": False,
         "reportOfflineMessage": False, "messageFormat": "array", "debug": False},
        {"type": "ws-reverse", "enable": False, "url": "", "heartInterval": 60000,
         "token": "", "reportSelfMessage": False, "reportOfflineMessage": False,
         "messageFormat": "array", "debug": False},
        {"type": "http", "enable": False, "host": "127.0.0.1", "port": 3000,
         "token": "", "reportSelfMessage": False, "reportOfflineMessage": False,
         "messageFormat": "array", "debug": False},
        {"type": "http-post", "enable": False, "url": "", "enableHeart": False,
         "heartInterval": 60000, "token": "", "reportSelfMessage": False,
         "reportOfflineMessage": False, "messageFormat": "array", "debug": False},
    ]},
    "enableLocalFile2Url": False, "log": True, "logLevel": "info",
    "autoDeleteFile": False, "autoDeleteFileSecond": 60,
    "msgCacheExpire": 120, "ffmpeg": "",
}

# 若已存在配置则在其之上合并，保留 LLBot 自己写回的字段
if os.path.exists(cfg_path):
    try:
        with open(cfg_path, encoding="utf-8") as f:
            base = json.load(f)
    except Exception as e:
        print(f"(原配置解析失败，按默认结构重建: {e})")

# ---- 只改我们要管的两个块，其余原样保留 ----
base["webui"] = {"enable": True, "host": webui_host, "port": int(webui_port)}

ob = base.setdefault("ob11", {})
ob["enable"] = True
connects = ob.setdefault("connect", [])
ws = next((c for c in connects if c.get("type") == "ws"), None)
if ws is None:
    ws = {"type": "ws"}
    connects.append(ws)
ws.update({
    "enable": True,
    "host": ws_host,          # 127.0.0.1 —— 只监听回环
    "port": int(ws_port),
    "token": token,
    "reportSelfMessage": False,
    "reportOfflineMessage": False,
    "messageFormat": "array",
    "debug": False,
})
# 确保只有正向 WS 一个连接启用，避免误开其它协议端口
for c in connects:
    if c is not ws:
        c["enable"] = False

with open(cfg_path, "w", encoding="utf-8") as f:
    json.dump(base, f, ensure_ascii=False, indent=2)
print(f"已写入 {cfg_path}")
print(f"  webui : http://{webui_host}:{webui_port}  (仅回环)")
print(f"  ob11  : ws://{ws_host}:{ws_port}  token={token[:6]}...{token[-4:]}  (仅回环)")
PY

# LLBot 容器内以 root 运行，数据目录需可写
chmod -R 777 "$DATA_DIR"
echo "完成。"
