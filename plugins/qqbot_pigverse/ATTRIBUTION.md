# 小猪宇宙（Pigverse）来源与归属声明

> 本文件区分 **我们原创的部分** 与 **引用/整合的第三方组件**。
> 分发、二次开发或搬运本功能时，请保留本声明。

## 一、原创部分（本仓库编写，MIT）

| 组件 | 位置 | 说明 |
|---|---|---|
| 小猪宇宙插件 | `plugins/qqbot_pigverse/` | 图鉴成就、猪猪表情包库、「小猪小猪」互动；Phase 2-4 规划中 |
| 成就系统 | `achievements.py` | 里程碑档位（10/25/…/230）、称号、Kleos 奖励、幂等领取 |
| 稀有度体系 | `rarity.py` | 按猪类别派生 N/R/SR/UR 的原创设计 |
| 表情包库 | `memes.py` | 上传/删除/随机、文件头格式嗅探、发送体积过滤 |
| 小猪小猪互动 | `__init__.py` | 关键词触发、10 种猪叫文案、30s 防刷冷却 |
| 经济接入 | `plugins/qqbot_economy/currency.py` 的 `PIG_ACHIEVEMENT_TOTAL` 与 Kleos faucet 登记 | 封闭卡池一次性奖励，符合 CURRENCY_SPEC |
| 玩法设计 | `docs/PIGVERSE_SPEC.md` | 四阶段设计（成就/卡面/斗兽场/牧场）与数值 |

## 二、引用/整合的第三方组件（未修改其源码）

| 组件 | 来源 | 许可 | 整合方式 |
|---|---|---|---|
| nonebot-plugin-rollpig-plus | [Felis2026](https://github.com/Felis2026/nonebot-plugin-rollpig-plus) | MIT | pip 依赖；只读其 `store`/`pig_resource_manager` 公开数据，不改其账本 |
| NoneBot2 | [nonebot/nonebot2](https://github.com/nonebot/nonebot2) | MIT | 框架 |
| nonebot-adapter-onebot | NoneBot 团队 | MIT | OneBot v11 适配器 |
| nonebot-plugin-maimaidx | [Yuri-YuzuChaN](https://github.com/Yuri-YuzuChaN/nonebot-plugin-maimaidx) | MIT | 舞萌查分；Phase 2 卡面拟引用其曲目静态数据 |
| nonebot-plugin-localstore / apscheduler | NoneBot 团队 | MIT | 数据目录 / 定时任务 |

> 小猪的抽取算法、猪圈数据、EX 差分变体、烤猪玩法均来自 rollpig-plus，**不是本项目原创**。

## 三、素材与数据

| 素材 | 来源 | 是否随代码仓库分发 |
|---|---|---|
| 预置表情包 39 张 | [PigHub](https://pighub.top)（第三方猪猪图站，按下载量选取） | ❌ 不随仓库；存于运行时 `data/` 卷，仅群内使用 |
| 小猪立绘 / 资源包 | rollpig-plus 内置 165 只 + 其官方资源包（223 只） | ❌ 未复制入本仓库 |
| 舞萌曲目/定数数据 | maimaidx 静态资源（水鱼查分器数据源） | ❌ 不入库，按 README 下载 |

> **表情包版权归原作者/上传者**，本项目仅作群聊娱乐引用；如需对外分发请先确认授权。
> 用户投稿的表情包由投稿者自行保证其可用性。

## 四、许可

- 本仓库原创代码：**MIT**（与 rollpig-plus 的 MIT 许可兼容）
- 第三方组件：遵循其各自许可（见上表）
- 注：`plugins/qqbot_pigverse/` 中的 `rarity.py` 依据 rollpig 的 `pig_rules.json` 分类规则派生，规则数据归 rollpig-plus 所有。
