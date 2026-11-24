"""插件配置。"""
from __future__ import annotations

from nonebot import get_plugin_config
from pydantic import BaseModel


class Config(BaseModel):
    """DJMAX 插件配置。

    默认在所有群和私聊都响应，与其他查分插件一致；如需限制，
    把 ``djmax_group_ids`` 填成白名单群号，其他群完全不响应。
    """

    #: 允许使用的群号白名单；为空表示不限。
    djmax_group_ids: list[int] = []
    #: 是否允许私聊使用；私聊默认只支持绑定与查询。
    djmax_private: bool = True
    #: 单曲成绩缓存秒数。
    djmax_record_ttl: int = 180
    #: 出图素材目录；留空则使用环境变量 DJMAX_ASSETS_DIR 或从 CDN 下载。
    djmax_assets_dir: str = ""


config = get_plugin_config(Config)
