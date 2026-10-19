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
| `/升阶`（别名 `/觉醒`） | 到达觉醒点时升阶（★+1），领取一次性月币奖励 |
| `/倍率`（别名 `/加成`） | 仅 bot 主：查看/设置荣耀倍率（周末、节日、自定义日期） |
| `/发红包 <总额> <份数> [祝福语]` | 仅 bot 主：群里发月币红包（从自己钱包扣，30 分钟有效） |
| `/抢红包 [编号]`（别名 `/领红包`） | 群里抢红包，每人每个红包限一次 |
| `/领金币` | **限时活动**：按 QQ 群等级发放月币，每人限领一次；跨群取**最高等级**，每多一个共同群 +10%（封顶 +30%） |
| `/爆金币 <@某人\|QQ号> [数量]` | 仅 bot 主与白名单可直接发币，默认 100 |
| `/领金币开关 on\|off`（别名 `/活动开关`） | 仅 bot 主可用；群内回复 6 秒后自动撤回 |
| `/配置`（别名 `/核心状态`） | 仅 bot 主：查看整体配置（开关、档位、上限、白名单、用户数、流通量），群内自动撤回 |

## 称号与折扣

荣耀走 **maimai 旅行伙伴式**的里程制：**100 荣耀 = 1 级**（1000 级后 10 荣耀 = 1 级），满级 Lv.9999，
在 9 / 49 / 299 / 499 / 999 级各觉醒一次（★1–★5），**Lv.9999 觉醒第六颗星**。
到达觉醒点会**卡住等级，必须手动 `/升阶`** 才能继续（忘了的话签到回复会提醒）。

| 星级 | 等级 | 称号 | 累计里程 | 折扣 |
|---|---|---|---|---|
| ★1 | Lv.9 | 🌿 月桂 | 800 | ~0.1% |
| ★2 | Lv.49 | ⚖️ 执政官 | 4,800 | ~0.5% |
| ★3 | Lv.299 | 🏅 大执政官 | 29,800 | ~3% |
| ★4 | Lv.499 | 🔱 海神眷者 | 49,800 | ~5% |
| ★5 | Lv.999 | 🛡 雅典守护者 | 99,800 | ~10% |
| ★6 | Lv.9999 | ✨ 神话 | 189,890 | **100%（科技功能全免）** |

每次升阶的一次性奖励：★1 +50、★2 +100、★3 +200、★4 +300、★5 +500、★6 +1000 月币。

折扣按等级线性给（每级 -0.01%），Lv.999 时约 -10%，Lv.4999 约 -50%，
只有到 Lv.9999 才吃满 100%。按日均 400~600 荣耀，满级约 10~14 个月。

## 红包

bot 主在群里 `/发红包 300 3 恭喜发财`：从自己钱包扣 300 月币，发成 3 份，
群友 `/抢红包` 随机抢（二倍均值法），30 分钟未抢完的**退回发送者**。
不凭空增发；进程重启会丢失未抢完的红包。

## 荣耀倍率（周末 / 节日 / 自定义）

倍率只放大**荣耀**获取（签到、捕鱼），不影响月币/鱼币。
规则存 `data/qqbot_core/multipliers.json`，bot 主可用 `/倍率` 修改：

```
/倍率                        查看今日倍率与近期安排
/倍率 周末 3                 周末统一 ×3（默认 ×2）
/倍率 2026-11-11 5 生日       指定日期 ×5
/倍率 2026-11-11 20 生日 uncap   ×20 且捕鱼荣耀不设上限
/倍率 区间 2026-10-01 2026-10-07 5 国庆
/倍率 删除 2026-11-11
```

默认规则：周末 ×2；2026 国庆（10-01~10-07）×5。
倍率也作用于捕鱼荣耀的日上限（×倍率同步抬高；`uncap` 则完全不设上限）。

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
| `qqbot_core_discount_per_level` | `0.0001` | 每级荣耀等级带来的折扣（满级约 100%） |
| `qqbot_core_discount_cap` | `1.0` | 折扣上限（1.0 = 满级全免） |
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
