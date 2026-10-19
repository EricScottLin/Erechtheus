# qqbot_core —— 货币与个人信息基础插件

Erectheus 的**基础服务层**：钱包、代币登记、个人资料都只落盘在这里。
玩法插件（如 `qqbot_economy`）通过 `require("qqbot_core")` 调用接口，不直接碰存储。

## 对用户

| 命令 | 说明 |
|---|---|
| `/余额`（别名 `/钱包`） | 查看荣耀等级与各代币余额 |
| `/名片`（别名 `/个人信息` `/资料`） | 个人信息卡：昵称、签名、入坑日期、荣耀等级、钱包 |
| `/签名 <内容>` | 设置签名 |
| `/改昵称 <名字>` | 设置展示昵称 |
| `/称号`（别名 `/头衔`） | 查看荣耀称号、下一称号进度、专属折扣 |
| `/领金币` | **限时活动**：按 QQ 群等级发放月币，每人限领一次；跨群取**最高等级**，每多一个共同群 +10%（封顶 +30%） |
| `/爆金币 <@某人\|QQ号> [数量]` | 仅 bot 主与白名单可直接发币，默认 100 |
| `/领金币开关 on\|off`（别名 `/活动开关`） | 仅 bot 主可用；群内回复 6 秒后自动撤回 |
| `/配置`（别名 `/核心状态`） | 仅 bot 主：查看整体配置（开关、档位、上限、白名单、用户数、流通量），群内自动撤回 |

## 称号与折扣

荣耀等级解锁称号（纯展示）：

| 等级 | 称号 |
|---|---|
| Lv.3 | 🌿 月桂 |
| Lv.5 | ⚖️ 执政官 |
| Lv.10 | 🏅 大执政官 |
| Lv.20 | 🔱 海神眷者 |
| Lv.30 | 🛡 雅典守护者 |

其他插件定价时可用 `core.pricing.price(uid, base)` 按荣耀等级打折
（每级 -1%，封顶 -30%）：

```python
cost = await core.pricing.price(uid, 100)   # Lv.13 → 88
```

## 活动提醒

`qqbot_economy` 的 `/签到` 成功后会调用 `qqbot_core` 的 `sign_in_reminders(uid)`，
把**进行中且该用户还没参与**的限时活动追加到回复里（例如「🎐 领金币活动进行中」）。
新增活动时只需要在 `activities.py` 的 `reminders()` 里加一条检查，玩法插件不用改。

## 配置（`.env.prod`，前缀 `qqbot_core_`）

| 键 | 默认 | 说明 |
|---|---|---|
| `qqbot_core_whitelist` | `[]` | `/爆金币` 白名单（JSON 数组） |
| `qqbot_core_daily_earn_cap` | `{"kleos":680,"drachma":5000}` | 日获取上限（防刷） |
| `qqbot_core_claim_enabled` | `false` | 活动默认开关（bot 主私聊可覆盖） |
| `qqbot_core_claim_amount` | `100` | 群等级不到最低档时的保底额 |
| `qqbot_core_discount_per_level` | `0.01` | 每级荣耀等级带来的折扣 |
| `qqbot_core_discount_cap` | `0.30` | 折扣上限（最多 -30%） |
| `qqbot_core_claim_multi_group_bonus` | `0.10` | 每多一个共同群的加成 |
| `qqbot_core_claim_multi_group_bonus_cap` | `0.30` | 多群加成上限 |
| `qqbot_core_claim_tiers` | `{1:100,20:300,50:600,100:1000}` | 群等级 → 发放档位 |
| `qqbot_core_at_sender` | `true` | 回复是否 @ 发起者 |
| `qqbot_core_nickname_max` | `20` | 昵称长度上限 |
| `qqbot_core_signature_max` | `60` | 签名长度上限 |

bot 主由 `SUPERUSERS` 指定（NoneBot 全局配置）。

## 对其他插件（开发者接口）

```python
from nonebot.plugin import require

core = require("qqbot_core")

# 货币：只允许登记过的代币（kleos / drachma）
await core.wallet.earn(uid, "drachma", 50)          # 发币，返回实际发放额
await core.wallet.spend(uid, "drachma", 10)          # 扣币，余额不足返回 False
await core.wallet.transfer(uid, target, "drachma", 30)
await core.wallet.balance(uid, "drachma")
await core.wallet.balances(uid)                      # {currency: balance}

core.currency.fmt("drachma", 1200)                   # '🌙 1,200 月币'
core.currency.prestige_level(4500)                   # 荣耀等级

# 自己的功能状态：按插件名命名空间存，互不干扰
await core.storage.get_section(uid, "my_plugin", {"counter": 0})
await core.storage.update_section(uid, "my_plugin", counter=1)
```

## 存储

`nonebot_plugin_localstore` 数据目录下的 `users.json`（容器内
`/app/data/qqbot_core/users.json`），结构：

```json
{
  "<uid>": {
    "profile": {"nickname": "", "signature": "", "joined_at": "2026-10-19"},
    "wallet": {
      "kleos":   {"balance": 0, "total_earned": 0, "total_spent": 0, "daily_earned": {}},
      "drachma": {"balance": 0, "total_earned": 0, "total_spent": 0, "daily_earned": {}}
    },
    "sections": {"qqbot_economy": {"rod_level": 1, "bait": 0}}
  }
}
```

运行时开关（领金币开关等）存 `settings.json`，与 `users.json` 同目录。
