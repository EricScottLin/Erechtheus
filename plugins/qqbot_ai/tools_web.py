"""联网搜索工具实现（ddgs / DuckDuckGo）。

``ddgs`` 是同步库且有反爬限流，这里统一放到线程里跑并加超时；
搜不到或后端缺失时返回明确文字，让模型据此兜底，而不是报错给用户。
"""

import asyncio
from typing import Any, Optional

from nonebot.log import logger

# 问「是什么意思」这类词时，带上这些词能显著提升结果质量
_HINT_SUFFIXES = ("是什么", "什么意思", "含义", "是什么梗", "是什么意思")


def _ddgs_available() -> bool:
    try:
        import ddgs  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def build_search_handler(
    backend: str,
    max_results: int,
    timeout: float,
    region: str = "cn-zh",
) -> Optional[Any]:
    """按配置返回搜索执行函数；后端不可用时返回 ``None``（不暴露工具）。"""
    backend = (backend or "auto").lower()
    if backend == "none":
        logger.info("[qqbot_ai] 搜索后端设置为 none，联网搜索工具已禁用")
        return None
    if backend == "ddgs" and not _ddgs_available():
        logger.warning("[qqbot_ai] 配置了 ddgs 搜索后端但未安装 ddgs，工具已禁用")
        return None
    if backend == "auto" and not _ddgs_available():
        logger.info("[qqbot_ai] 未安装 ddgs，联网搜索工具已禁用（pip install ddgs 可开启）")
        return None

    def _sync_search(query: str, site: str, limit: int) -> list[dict[str, Any]]:
        from ddgs import DDGS

        from ddgs.exceptions import DDGSException

        with DDGS(timeout=int(max(3, timeout))) as ddgs:
            kwargs: dict[str, Any] = {"region": region, "safesearch": "moderate"}
            if site:
                kwargs["site"] = site
            try:
                return list(ddgs.text(query, max_results=limit, **kwargs))
            except DDGSException as e:
                logger.debug(f"[qqbot_ai] 搜索无结果/被限流：{e}")
                return []

    async def handler(args: dict[str, Any]) -> str:
        query = str(args.get("query") or "").strip()
        site = str(args.get("site") or "").strip()
        if not query:
            return "搜索关键词为空，请给出要查的关键词。"
        try:
            results = await asyncio.wait_for(
                asyncio.to_thread(_sync_search, query, site, max_results),
                timeout=timeout + 5,
            )
        except asyncio.TimeoutError:
            logger.warning(f"[qqbot_ai] 搜索超时：{query}")
            return "搜索超时，没有拿到结果。"
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[qqbot_ai] 搜索失败：{type(e).__name__}: {e}")
            return "搜索服务暂时不可用，没有拿到结果。"
        if not results and site:
            # 指定站点的搜索常被 DDG 限流，退回全网再试一次
            try:
                results = await asyncio.wait_for(
                    asyncio.to_thread(_sync_search, query, "", max_results),
                    timeout=timeout + 5,
                )
            except Exception:  # noqa: BLE001
                results = []
        if not results:
            # 去掉「是什么」之类的口头语再搜一次（如「rule34是什么」->「rule34」）
            stripped = query
            for suffix in _HINT_SUFFIXES:
                if stripped.endswith(suffix):
                    stripped = stripped[: -len(suffix)].strip()
                    break
            if stripped and stripped != query:
                try:
                    results = await asyncio.wait_for(
                        asyncio.to_thread(_sync_search, stripped, site, max_results),
                        timeout=timeout + 5,
                    )
                except Exception:  # noqa: BLE001
                    results = []
        if not results:
            return f"没有搜到「{query}」的结果。"

        lines = [f"「{query}」的搜索结果："]
        for i, item in enumerate(results[:max_results], 1):
            title = str(item.get("title") or "").strip()
            body = " ".join(str(item.get("body") or "").split())
            href = str(item.get("href") or item.get("url") or "").strip()
            lines.append(f"{i}. {title}\n   摘要：{body[:300]}\n   链接：{href}")
        return "\n".join(lines)

    return handler
