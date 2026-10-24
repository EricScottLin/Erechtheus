# 舞萌直连写操作测试计划（P3）

> 状态：**计划**，代码尚未实现。当前线上只有只读能力（资料/成绩/预览/导出/登出）。
> 目标读者：维护者本人。任何一条没通过，都不进入下一条。

## 0. 为什么写操作要单独立计划

国服舞萌的写接口只有 **`UpsertUserAllApi`**：一次提交整个账号状态
（`userData` / `userOption` / `userMusicDetailList` / `userItemList` / `userMapList` /
`userCharacterList` / `userKaleidxScopeList` / `userChargeList` / …）。

- **没有** `UnlockItemApi` 这种单点接口，"解锁一个收藏品"= 读全量 → 改一个字段 → 整体回写。
- 漏字段不是报错，是**把漏掉的字段清空**。参考脚本 `reference/maquitous/music.py`
  就把 `userCharacterList/userMapList/userItemList` 直接传空数组。
- 国服 1.56 的字段集与参考脚本（1.53，日服/国际服）不同，`compatibleCmVersion`、
  `lastRomVersion` 等必须来自真实读取，不能照抄。
- 写操作要 `UserLoginApi → (UploadUserPlaylogApi) → UpsertUserAllApi → UserLogoutApi`
  一整套，**每次 60–65 秒并独占账号**。

结论：写功能的本质是"构造一个正确的整号快照"，所以测试重点不是"解锁成功了吗"，
而是**"除了我想改的那个字段，其它字段一个都没变"**。

## 1. 三条铁律

1. **先备份再动手**：写之前必须先完整读取一次并落盘（`导出` 的 JSON 就是备份）。
2. **no-op 先行**：先证明"读出来原样写回去"不会改坏账号，再谈改字段。
3. **一次只改一个字段**，改完立刻重新读取对比，确认后再做下一个。

## 2. 环境矩阵

| 环境 | 用途 | 真实账号 | 默认开关 |
|---|---|---|---|
| `mock_server`（离线） | payload 组装、字段守恒、幂等、失败注入 | 否 | 测试中默认开 |
| 测试小号（real） | no-op 回写、单字段修改、读回验证 | **是（专用小号）** | `MAIMAI_DX_WRITE_ENABLED=false` |
| 主号 / 生产 | **不测试** | — | 永久关闭 |

生产配置里写操作必须默认关闭，且只在 `MAIMAI_DX_WRITE_ALLOW` 列出的 QQ 上生效。
即使打开，也只允许在测试群里对测试小号执行。

## 3. Phase 0：离线（不需要二维码，现在就能做）

在 MaimaiDXCHN 里补：

- `mock_server` 增加 `UpsertUserAllApi` / `UploadUserPlaylogApi`，把提交内容存进内存，
  支持再次读取时回放，便于"写完立刻读回"。
- 新增 `upsert.py`：`build_payload(read_result, *, patches=None)` —— 把读取结果映射成
  `upsertUserAll`，`patches` 只允许白名单里的字段/列表项。
- 新增测试 `tests/test_upsert.py`。

用例（全部离线）：

| # | 用例 | 验收 |
|---|---|---|
| P0-1 | 读 → build → 回写 → 再读 | 除白名单字段外逐字段相等（深比较，含列表顺序） |
| P0-2 | 空 `patches` | 与读取结果完全一致（no-op 保证） |
| P0-3 | 只改一个 `userItemList` 项 | 该项变化，其余列表条目数量与内容不变 |
| P0-4 | 传入未知字段 | 抛 `ValueError`，不静默丢弃 |
| P0-5 | 读取结果不完整（`complete=false`） | 拒绝构造，返回明确错误 |
| P0-6 | 同一 payload 提交两次（幂等） | 第二次不新增条目（按复合键去重） |
| P0-7 | 服务器返回业务错误 | 不重试、不降级、返回错误码原文（脱敏后） |
| P0-8 | 网络超时 | 明确标记"结果未确认"，禁止自动重试 |

## 4. Phase 1：真机 no-op（需要测试小号）

前置：

1. 你用测试小号的微信准备一条新二维码，私聊发「绑定」+ 图片；
2. 该小号荣耀 ≥ Lv.10（不够就先签到几天）；
3. `.env.prod` 打开 `MAIMAI_DX_WRITE_ENABLED=true` 且把测试小号 QQ 加进 `MAIMAI_DX_WRITE_ALLOW`；
4. 先发「更新」+「导出」，把当前完整快照存一份。

流程：

```
更新（读全量）→ 导出（本地备份）→ [测试命令] no-op 回写 → 更新（再读）→ 对比
```

验收基线（**允许变化的字段白名单**，其余必须完全一致）：

| 允许变化 | 原因 |
|---|---|
| `playCount` / `currentPlayCount` | 回写会被服务器算一次游玩 |
| `lastPlayDate` / `userPlayDate` / `loginDateTime` | 时间戳 |
| `playerRating` / `playerOldRating` / `playerNewRating` | 由成绩重算 |
| `lastRomVersion` / `lastDataVersion` / `compatibleCmVersion` | 版本号回填 |

白名单之外的任何差异 → **立即中止**，用备份对比定位，不再做单字段测试。

通过标准：连续 3 次 no-op，白名单外的 diff 均为 0。

## 5. Phase 2：单字段修改（需要测试小号）

按风险从低到高，一次只做一个，做完重新读取并对比：

| 顺序 | 功能 | 改什么 | 读回验收 |
|---|---|---|---|
| 1 | 门解锁 | `userKaleidxScopeList` 里 gate 8 的 `isGateFound/isKeyFound = true` | 重新读，门 8 出现在列表且字段为 true；其它门不变 |
| 2 | 收藏品解锁 | `userItemList` 增加一条 `{itemKind, itemId, stock:1, isValid:true}` | 读回该 item 存在，其它 kind 的数量不变 |
| 3 | 歌曲解锁 | `userItemList` itemKind 5/6/7 增加一条 | 读回存在；成绩列表不受影响 |
| 4 | 看板娘升级 | `userCharacterList` 里某个 character 的觉醒/等级字段 | 先做一次真实读取，确认国服字段名后再改 |
| 5 | 跑图 | `userMapList` 的 `distance/isClear/unlockFlag` | 依赖地图宝藏表，最后做 |

每个功能的测试用例（都跑一遍）：

| 检查 | 内容 |
|---|---|
| C1 前置拒绝 | 未绑定 / 会话未确认 / 快照不完整 / 未开启写开关 → 必须拒绝 |
| C2 单点生效 | 目标字段按预期变化 |
| C3 守恒 | 除目标字段外，所有列表长度与内容不变 |
| C4 幂等 | 同一操作连发两次，结果不翻倍、不报错 |
| C5 失败安全 | 服务器报错 / 网络超时 → 不改数据、不重试、提示人工核对 |
| C6 恢复 | 用备份重跑一次写操作能回到原始状态（至少门/收藏品两项） |

## 6. 中止条件（任一条命中就停）

- 白名单外字段出现差异；
- 写入后无法重新读取（登录/登出异常）；
- 服务器返回未知错误码，或同一操作两次结果不一致；
- 账号出现在别处登录（`isLogin=true`）；
- 任何一次操作耗时异常（>150 秒）。

## 7. 回滚与恢复

- 每次写之前自动落盘 `data/qqbot_maimai_dx/snapshots/<uid>/<时间>.json`（P1 的备份功能）；
- 回滚 = 用备份的 `upsertUserAll` 原样再提交一次（这就是先做 no-op 的意义）；
- 回滚同样受 §6 约束，连续两次失败就停手，人工介入。

## 8. 需要你配合的

| 事项 | 说明 |
|---|---|
| 测试小号 | 一个专用小号，愿意承担数据风险；不要用主号 |
| 二维码 | 每次真机测试前给一条新的（绑定一次即可多次读，失效再给） |
| 时间窗 | Phase 1 约 10 分钟（含多次 65 秒会话）；Phase 2 每项约 5 分钟 |
| 决策 | 门解锁做完确认无误后，再决定要不要继续 2–5 项 |

## 9. 结论口径

在 Phase 0 + Phase 1 通过之前，接口对外只能说"只读已接入，写操作在验证中"。
写功能即使实现，也保持 `MAIMAI_DX_WRITE_ENABLED=false` 默认关闭。
