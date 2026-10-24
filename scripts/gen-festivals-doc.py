#!/usr/bin/env python3
"""从 plugins/qqbot_core/festivals.py 生成 docs/FESTIVALS.md。

改完节日表后跑一次：
    .venv/bin/python scripts/gen-festivals-doc.py
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "festivals", ROOT / "plugins/qqbot_core/festivals.py"
)
f = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(f)

L: list[str] = []
add = L.append

add("# 节日与活动倍率清单")
add("")
add("> 版本 v1.5 · 自动生成，勿手改 · 数据源：`plugins/qqbot_core/festivals.py`")
add("")
add("生成方式：`.venv/bin/python scripts/gen-festivals-doc.py`")
add("")
add("荣耀倍率分五类节日。查找优先级：")
add("")
add("```")
add("自定义日期（/倍率 2026-11-11 10 生日）")
add("  > 自定义区间（/倍率 区间 …）")
add("    > 内置节日日历（本文件）")
add("      > 周末（默认 ×2）")
add("```")
add("")
add("## 分类")
add("")
add("| 分类 | 倍率 | 包含 | 2026 代表日子 |")
add("| --- | --- | --- | --- |")
add(
    "| 传统 | ×10（除夕 ×20） | 春节、除夕、元宵、清明、端午、七夕、中元、中秋、重阳、腊八、小年"
    " | 02-17 春节、04-05 清明、10-18 重阳 |"
)
add(
    "| 法定 | ×10（国庆首日 ×20） | 国务院放假安排里的每一天：元旦、春节、清明、劳动节、端午、中秋、国庆"
    " | 10-02~10-07 国庆、09-25 中秋 |"
)
add(
    "| 西方 | ×5（平安夜 ×10、十三号星期五 ×13） | 情人节、白色情人节、愚人节、骄傲日、万圣夜、感恩节、"
    "黑色星期五、十三号星期五、平安夜、圣诞节、复活节、母亲节、父亲节、跨年夜 | 11-27 黑色星期五、12-25 圣诞 |"
)
add(
    "| 公益 | ×5 | 地球日、世界读书日、世界环境日、世界水日、世界献血日、联合国日、世界粮食日、"
    "教师节、记者节、建党节、建军节…… | 04-22 地球日、10-24 联合国日 |"
)
add(
    "| 互联网 | ×5 | 1024 程序员日、世界程序员节（第 256 天）、软件自由日、Ada 洛芙莱斯日、π Day、"
    "520、618、世界表情包日、88 会员节、双11、双12、女生节、系统管理员日 | 10-24 程序员日、09-13 世界程序员节 |"
)
add("")
add("档位：")
add("")
add("| 倍率 | 档位 |")
add("| --- | --- |")
add("| ×20 及以上 | 红日 |")
add("| ×13 | 玄学（十三号星期五） |")
add("| ×10 ~ ×19 | 大节 |")
add("| ×5 ~ ×9 | 小节 |")
add("| ×2 | 周末（不含节日时） |")
add("")
add("## 撞节叠加规则")
add("")
add("同一天有多个节日 → **倍率相加**（不叠乘，免得一天刷出天文数字）：")
add("")
add("| 日期 | 撞节 | 计算 | 倍率 |")
add("| --- | --- | --- | --- |")
for iso, info in f.year_entries(2026):
    if len(info["tags"]) > 1:
        calc = " + ".join(f"{m:g}" for _, m in info["parts"])
        add(f"| {iso} | {' + '.join(info['tags'])} | {calc} | ×{info['mult']:g} |")
add("")
add("两个例外：")
add("")
add('1. **同一个节日登记两次**（法定假期表 + 农历表都有"端午"）→ 只算一次，取高不叠。')
add("2. **红日替掉同一个假期的普通档**：除夕 ×20 替代春节 ×10，国庆首日 ×20 替代国庆节 ×10。")
add("")
add(f"上限 `MAX_STACK = {f.MAX_STACK:g}`。")
add("")
add("## 十三号星期五")
add("")
add("不是固定日期：只要 13 号当天是周五就触发，一年 1~3 次，倍率 ×13（玄学档）。")
add("")
add("| 年份 | 十三号星期五 |")
add("| --- | --- |")
for y in range(2026, 2031):
    hits = [iso for iso, info in f.year_entries(y) if "十三号星期五" in info["tags"]]
    add(f"| {y} | {'、'.join(h[5:] for h in hits)} |")
add("")
add("## 随机倍率日：1024 程序员日")
add("")
add("10-24 的倍率不吃固定值，而是**荣耀和月币一起翻倍**：n 用 `uid + 日期` 做种子抽，")
add("每人一个签、同一个人当天固定、重启不变；抽到哪个数，科普就解释哪个数。")
add("")
add("荣耀倍率 = `5 × 2^n`，月币奖励 = `5 × 2^n`：")
add("")
add("| n | 2^n | 概率 | 荣耀倍率 | 月币 | 出处 |")
add("| --- | --- | --- | --- | --- | --- |")
_pool = f.DICE_DAYS["10-24"]
_unit = _pool.get("unit", 5)
for n, w in sorted(_pool["weights"].items()):
    add(f"| {n} | {2**n} | {w:g}% | ×{_pool['fixed'] * 2**n:g} | +{_unit * 2**n:,} | {f.DICE_NOTES[n]} |")
add("")
add("期望 ≈ ×42 荣耀倍率、+42 月币；中位数都是 ×20 / +20。")
add("")
add("**封顶说明**：签到荣耀与月币都不设额外上限（抽到 2¹⁰ 就是 ×5120 和 +5120）；")
add("捕鱼吃倍数但**日上限钉死**（100~680，随星级），倍数只让「几条鱼就吃满」，不抬天花板。")
add("这样全年捕鱼上限仍是 248,200，满级时间维持在 ~2.8 年。")
add("")
add("## 科普卡片")
add("")
add("`NOTES` 里的冷知识会随 `/倍率` 当日播报和 `/签到` 一起出现（当天有节日时）：")
add("")
add("| 节日 | 科普 |")
add("| --- | --- |")
for name, note in f.NOTES.items():
    add(f"| {name} | {note} |")
add("")
add("## 2026 全年日历")
add("")
add("法定假期以《国务院办公厅关于2026年部分节假日安排的通知》（国办发明电〔2025〕7号）为准。")
add("")
rows = f.year_entries(2026)
by_month: dict[int, list] = {}
for iso, info in rows:
    by_month.setdefault(int(iso[5:7]), []).append((iso, info))
wd = "一二三四五六日"
for m in sorted(by_month):
    add(f"### {m} 月")
    add("")
    add("| 日期 | 星期 | 节日 | 分类 | 倍率 | 档位 |")
    add("| --- | --- | --- | --- | --- | --- |")
    for iso, info in by_month[m]:
        d = date.fromisoformat(iso)
        add(
            f"| {iso[5:]} | 周{wd[d.weekday()]} | {info['name']} | {'/'.join(info['cats'])} "
            f"| ×{info['mult']:g} | {info['tier']} |"
        )
    add("")

add("## 2027–2028 已知农历节日")
add("")
add("国务院一般在前一年 11 月公布次年放假安排；2027 的法定假期区间公布后补进 `STATUTORY`，")
add("在此之前只有农历节日日单独 ×10（除夕 ×20），假期其余天数暂不覆盖。")
add("")
add("| 日期 | 节日 | 倍率 |")
add("| --- | --- | --- |")
for y in (2027, 2028):
    for iso, name, mult in f.YEARLY[y]:
        add(f"| {iso} | {name} | ×{mult:g} |")
add("")
add("## 冷门程序员节日")
add("")
add("| 日子 | 名称 | 来历 |")
add("| --- | --- | --- |")
add('| 第 256 天（9-13，闰年 9-12） | 世界程序员节 | 256 = 2⁸ = 0x100，俄罗斯 2009 年定的程序员节，一年里唯一"整字节"的一天 |')
add("| 9 月第三个周六 | 软件自由日 | 开源运动全球纪念日，GNU / Linux / Wikipedia 都沾这场运动的光 |")
add("| 10 月第二个周二 | Ada 洛芙莱斯日 | 纪念 1843 年写出史上第一个算法的 Ada，第一位程序员 |")
add("| 10-24 | 程序员日 | 2¹⁰ = 1024，中国程序员节；和联合国日同一天，撞出 ×10 |")
add("")
add("## 特别说明")
add("")
add("- **平安夜 ×10 / 圣诞节 ×5**：按当前要求，平安夜算传统档，圣诞节归西方小节。")
add("- **周末不参与叠加**：节日当天撞上周六日，只按节日倍率算（周末 ×2 仅在无节日时生效）。")
add("- **红日默认不取消捕鱼上限**，想放开用 `/倍率 2026-10-01 20 国庆首日 uncap`。")
add("- **法定假期调休**：放假日按通知算，调休上班的周末照常吃 ×2 周末倍数（未单独打标）。")
add("- 农历节日每年日期都变，需按年抄一行；跨年的腊八/小年是次年 1 月，已收进对应公历年的表里。")
add("")
add("## 维护方式")
add("")
add("```python")
add("# 加一年：在 YEARLY / STATUTORY 里补一段")
add('YEARLY[2027] = [("2027-02-05", "除夕", 20), ...]')
add('STATUTORY[2027] = [("2027-01-01", "2027-01-03", "元旦", 10), ...]')
add("```")
add("")
add("- 公历固定节日 → `SOLAR_FIXED`，元组为 `(月, 日, 名称, 倍率, 分类)`")
add("- 某月第 N 个周几 → `NTH_WEEKDAY`；某月最后一个周几 → `LAST_WEEKDAY`")
add("- 黑色星期五 = 感恩节 +1，复活节用 Computus 现算，世界程序员节 = 年初 +255 天，十三号星期五 = 逐月判断 13 号是否周五")
add("- 红日 → `RED_DAYS`，`{日期: (显示名, 被替代的节日名, 分类)}`")
add("- 科普文案 → `NOTES`，按节日名索引")
add("- 节日名要和法定假期表里的名字一致（`端午` 不是 `端午节`），否则会被当成两个节日叠起来")
add("")
add("## 命令")
add("")
add("```")
add("/倍率            今天倍率 + 近期节日（周末不铺屏）")
add("/倍率 日历 400    未来 400 天的节日表")
add('/签到            签到末尾播报"下一个活动：日期 节日 ×倍率（N 天后）"，当天有节日会附科普')
add("```")
add("")

out = ROOT / "docs/FESTIVALS.md"
out.write_text("\n".join(L), encoding="utf-8")
print(f"wrote {out} ({len(L)} lines)")
