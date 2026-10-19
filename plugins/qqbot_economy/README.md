# qqbot_economy —— Erectheus 经济系统

每日签到领 **Kleos 荣耀**，捕鱼赚 **Drachma 德拉克马**，可升级鱼竿、买鱼饵、抽奖。

双代币设计、通胀防控规则、数值量级规范，见
[`docs/CURRENCY_SPEC.md`](../../../root/docs/CURRENCY_SPEC.md)
与同目录 [`currency.py`](currency.py)（规范的可执行版本）。

## 为什么是两种币，不是一种

签到一天只能领一次，捕鱼 60 秒可以刷一次，**产能差两个数量级**
（签到约 680/天 vs 捕鱼约 8.6 万/天）。若共用一种货币：

- 签到收益被捕鱼彻底稀释 → 玩家觉得签到白给，不再上线
- 为凸显签到而抬高到几千/天 → 与捕鱼产能叠加，全网货币超发通胀

所以拆开：**Kleos 是荣誉（不可花），Drachma 是货币（可花）**，
两个蓄水池独立，互不通兑。

## 命令

| 命令 | 别名 | 说明 |
|---|---|---|
| `/签到` | 打卡、每日签到 | 每日一次，100 + 20×(连签−1)，连签封顶 30 天 |
| `/面板` | 信息、我的信息 | 声望等级、双币余额、签到连签、鱼竿鱼饵、渔获统计、出货率 |
| `/捕鱼` | 钓鱼、下网 | 冷却 60s，按稀有度权重出货，自动消耗鱼饵（如有） |
| `/商店` | 商铺 | 鱼饵、鱼竿升级价格 |
| `/购买 <编号>` | 买、购入 | 1=鱼饵，2=鱼竿升级 |
| `/单抽` `/十连` `/百连` | — | 100 / 1000 / 10000 Drachma |
| `/货币` | 经济 | 货币体系与运行指标（**仅超级用户**） |

## 数值一览

| 项 | 值 |
|---|---|
| Kleos 签到 | 100 基础 + 20/天连签，封顶 30 天 → 单日最高 680 |
| 声望等级 | 每 1000 累计 Kleos 升 1 级 |
| 鱼种 | 14 种：R×5（10~150）、SR×5（50~400）、SSR×4（450~999） |
| 基础出货率 | R 71.4% · SR 21.4% · SSR 7.1% |
| 鱼竿加成 | 每级 SR/SSR 权重 +25%，上限 Lv10 |
| 鱼竿成本 | 500 + 300×(等级−1)，Lv1→Lv10 共 **15,300** Drachma |
| 鱼饵 | 10 Drachma，下次捕鱼 SR/SSR 权重 +50% |
| 抽奖期望 | 投入 100 → 回收 57.5（**负期望 42.5%**） |

## 配置

环境变量或 `.env` / `.env.prod`，前缀 `qqbot_economy_`：

| 变量 | 默认 | 说明 |
|---|---|---|
| `qqbot_economy_sign_base` | 100 | 签到基础 Kleos |
| `qqbot_economy_sign_streak_bonus` | 20 | 连签每日额外 Kleos |
| `qqbot_economy_sign_streak_max` | 30 | 连签奖励封顶天数 |
| `qqbot_economy_sign_drachma_ratio` | 0.10 | 签到附带月币比例（本次荣耀 × 比例，0 = 不发月币） |
| `qqbot_economy_kleos_per_level` | 1000 | 每级声望所需累计 Kleos |
| `qqbot_economy_fish_cooldown` | 60 | 捕鱼冷却（秒） |
| `qqbot_economy_bait_price` | 10 | 鱼饵单价 |
| `qqbot_economy_bait_bonus` | 0.5 | 鱼饵稀有度权重加成 |
| `qqbot_economy_rod_base_price` | 500 | 鱼竿升级基础价 |
| `qqbot_economy_rod_price_step` | 300 | 鱼竿每级递增价 |
| `qqbot_economy_rod_max_level` | 10 | 鱼竿最高等级 |
| `qqbot_economy_gacha_single` | 100 | 单抽价 |
| `qqbot_economy_gacha_ten` | 1000 | 十连价 |
| `qqbot_economy_gacha_hundred` | 10000 | 百连价 |

## 数据存储

`nonebot_plugin_localstore` 数据目录下的 `users.json`（默认
`/app/data/qqbot_economy/users.json`，宿主机对应 `./data/` 挂载）。
单文件 JSON + asyncio.Lock，写盘用临时文件原子替换。
`docker-compose.yml` 已挂载 `./data:/app/data:rw`，**容器重建不丢数据**。

## 加新代币 / 新场景前必读

先过 `docs/CURRENCY_SPEC.md` §7 的检查清单，并在 `currency.py` 的
`CURRENCIES` 登记。`currency.py: validate_registry()` 会在插件加载时
自动校验规范（层数 ≤3、同层唯一、可消费代币 sink ≥3、兑换单向且税率 ≥20%）。
