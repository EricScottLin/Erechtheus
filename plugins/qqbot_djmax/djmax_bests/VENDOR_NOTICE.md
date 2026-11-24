# Vendored: djmax_bests

Source: https://github.com/SoreHait/djmax_bests_generate

License: MIT (see upstream LICENSE). Copyright (c) 2026 SoreHait.

本目录的图片绘制代码来自上述 MIT 项目，按许可原样保留版权声明。

## 相对上游的改动

- `generators/_paths.py` 新增：把素材路径从「包内固定目录」改为运行时解析，
  以便素材从 CDN / 本地目录加载、不随仓库分发。
- `generators/bests_generator/components.py`、`generators/scorelist_generator/*`：
  路径常量改为惰性求值（`_paths` 函数调用），字体改为首次使用时加载。
- `api_handler.py`：缓存与曲绘目录改为可写位置（环境变量 → localstore → 包内 → 临时目录）。
  NoneBot 部署中插件目录通常是只读挂载，写包内目录会直接失败。
- 新增 `single_query.py` / `song_index.py`：单曲信息组装与曲库检索（本项目原创）。

## 未随仓库分发的内容

以下素材约 56MB，运行时获取（见根目录 README「素材」一节）：

```
fonts/Respect_bd.ttf  fonts/Respect_rg.ttf  fonts/Respect_lt.ttf
bests_generator/assets/
scorelist_generator/assets/
```

上游将其与代码一同分发；本项目改为不随仓库分发、由使用方自行获取。
其中 `Respect*` 为 DJMAX 游戏字体，版权归其权利人所有。
