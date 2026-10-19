# HoshinoBot 个人改动存档

> **这是什么**：`Erechtheus` 仓库 `hoshino` 分支中，属于你自己的改动。
> `hoshino` 分支整体是对上游 [HoshinoBot](https://github.com/Ice-Cirno/HoshinoBot)
> 的 fork，大部分代码来自上游且已过时；这里只抽出**你亲手改过的部分**，
> 与上游代码解耦，便于日后更新上游后重新应用。

## 背景

`hoshino` 分支的提交历史：

| 提交 | 日期 | 内容 |
|---|---|---|
| `5dc855f` | 2024-10-17 | Initial commit on hoshino branch（导入 HoshinoBot） |
| `0ae483c` | 2024-10-17 | 添加上海时区设置和未知名字的占位处理 |
| `0970003` | 2024-11-28 | refactor(HoshinoBot)：重构 maimaiDX 模块并添加新功能 |
| `38e1f85` | 2024-12-12 | feat(maimaiDX)：优化机厅人数管理并添加自动清零功能 |

你的改动 = 后三个提交，全部集中在 **maimaiDX 模块**。

## 文件清单

代码改动（7 个 `.py` + 1 个 `.txt`）：

| 文件 | 改动量 | 说明 |
|---|---|---|
| `hoshino/modules/maimaiDX/maimai_arcade.py` | +58 | **机厅人数管理 + 自动清零**（核心功能） |
| `hoshino/modules/maimaiDX/libraries/maimaidx_api_data.py` | +129 | API 数据层 |
| `hoshino/modules/maimaiDX/libraries/maimaidx_music.py` | +119 | 曲目查询 |
| `hoshino/modules/maimaiDX/libraries/maimaidx_arcade.py` | +14 | 机厅数据层 |
| `hoshino/modules/maimaiDX/command/mai_alias.py` | +6 | 别名命令 |
| `hoshino/modules/maimaiDX/command/mai_search.py` | +5 | 搜索命令 |
| `hoshino/modules/maimaiDX/__init__.py` | +4 | 模块入口 |
| `hoshino/util/textfilter/sensitive_words.txt` | +1 | 敏感词 |

另有你维护的数据文件（体积较大，未放入本目录，仍在 `hoshino` 分支中）：

- `static/arcades.json`（25,907 行）与 `static/arcades_backup.json` —— 机厅数据
- `static/music_chart.json`（137,896 行）、`static/music_data.json`（78,260 行） —— 曲目数据
- `static/group_alias_switch.json`、`group_guess_switch.json`、`chart_stats.json`
- 字体：`HanYi.ttf`(11M)、`SourceHanSansSC-Bold.otf`(17M)、`meiryo.ttc`(9.5M)

## 目录结构

```
legacy/hoshino-user-changes/
├── README.md                        ← 本文件
├── maimaiDX-user-changes.patch      ← 代码改动 patch（795 行，unified diff）
└── src/                             ← 改动后的源文件快照（路径相对于 HoshinoBot/）
    └── hoshino/...
```

## 如何重新应用

### 方式一：打 patch

以上游 HoshinoBot 对应版本为基准：

```bash
cd /path/to/HoshinoBot
git apply --check /path/to/maimaiDX-user-changes.patch   # 先检查能否干净应用
git apply /path/to/maimaiDX-user-changes.patch
```

若上游已变动导致冲突，用 `git apply --3way` 或手动合并
（`src/` 下的快照可直接作为对照）。

### 方式二：对照源码快照

直接查看 `src/` 下的文件，把差异部分移植到新版对应文件。

## 注意

- patch 的基准是 `5dc855f`（你 fork 时的 HoshinoBot 快照），
  不是上游某个正式 release，所以直接 `git apply` 到最新上游可能冲突。
- `maimai_arcade.py` 的「自动清零」与当前 NoneBot 机器人的 QQ 昵称一致，
  应是同一套需求的不同实现，移植时可参考
  `plugins/qqbot_economy` 的做法（见 `docs/CURRENCY_SPEC.md`）。
- 字体与大体积 JSON 未入库（`.gitignore` 已排除 `*.7z` 等大文件），
  需要时从 `hoshino` 分支取：`git show hoshino:HoshinoBot/hoshino/modules/maimaiDX/static/arcades.json`
