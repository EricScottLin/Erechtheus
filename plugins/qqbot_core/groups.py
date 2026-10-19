"""跨群等级：找出 bot 与某用户共同所在的群，并取他们的群等级。

用于 /领金币：同一个人可能在多个群，按"最高群等级 + 多群小加成"发放，
避免每群领一遍导致 N 倍，也避免只能按当前群等级领而吃亏。
"""

from nonebot.log import logger


async def levels_across_groups(bot, user_id: str) -> dict[int, int]:
    """返回 {群号: 群等级}，只包含 bot 在、且该用户也在的群。

    单个群查询失败（用户不在该群等）直接跳过；整体查询失败返回空 dict，
    调用方需自行兜底（例如只用当前群）。
    """
    result: dict[int, int] = {}
    try:
        groups = await bot.get_group_list()
    except Exception as e:  # noqa: BLE001 —— 查询失败降级
        logger.warning(f"[qqbot_core] get_group_list 失败：{e}")
        return result

    for g in groups or []:
        try:
            gid = int(g.get("group_id", 0))
            if gid <= 0:
                continue
            member = await bot.get_group_member_info(
                group_id=gid, user_id=int(user_id)
            )
        except Exception:  # noqa: BLE001 —— 用户不在该群等情况
            continue
        result[gid] = int((member or {}).get("level", 0) or 0)
    return result
