"""qqbot_toolbox.connectivity 的离线单测（不联网）。

覆盖：目标解析（无参 / 关键词 / 直给 URL / 非法协议 / 未知关键词）、
网关 URL 补全、探活结果判定（2xx / 401 / 404 / 重定向）、超时与连接错误。

真实的网络探测在容器里跑 `/连通` 验证，这里只测纯逻辑与分支。
"""

import asyncio
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _stub(name: str, **attrs) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


# --- 依赖打桩：只测纯逻辑，不需要真实 NoneBot / httpx 运行时 ---------------
class _FakeResponse:
    def __init__(self, status_code: int, location: str = "") -> None:
        self.status_code = status_code
        self.headers = {"location": location} if location else {}


class _FakeAsyncClient:
    """按 URL 返回预置响应的假客户端。"""

    responses: dict[str, object] = {}

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url: str, headers=None):
        result = _FakeAsyncClient.responses.get(url, _FakeResponse(200))
        if isinstance(result, Exception):
            raise result
        return result


def _install_stubs() -> None:
    httpx_stub = _stub(
        "httpx",
        AsyncClient=_FakeAsyncClient,
        Timeout=lambda *a, **k: object(),
        HTTPError=type("HTTPError", (Exception,), {}),
    )
    httpx_stub.TimeoutException = type("TimeoutException", (Exception,), {})
    httpx_stub.ConnectError = type("ConnectError", (Exception,), {})
    httpx_stub.HTTPError = type("HTTPError", (Exception,), {})

    nb = _stub("nonebot")
    nb.on_command = lambda *a, **k: _FakeMatcher()
    nb.get_driver = lambda: types.SimpleNamespace(
        config=types.SimpleNamespace(qqbot_ai_api_base="http://gw.local:3000/v1")
    )
    # 需要真正的「子包」结构，否则 `from nonebot.adapters.onebot.v11 import ...` 失败
    adapters = _stub("nonebot.adapters")
    adapters.__path__ = []
    onebot = _stub("nonebot.adapters.onebot")
    onebot.__path__ = []
    _stub("nonebot.adapters.onebot.v11", Message=object, MessageEvent=object)
    _stub("nonebot.params", CommandArg=lambda: object())


class _FakeMatcher:
    """NoneBot Matcher 的最小替身：handle() 既是装饰器又是实例方法。"""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def handle(self, *args, **kwargs):
        # @matcher.handle() -> 返回装饰器；装饰器收到函数后原样返回
        def decorator(fn):
            return fn

        return decorator

    async def finish(self, *args, **kwargs):
        return None


_install_stubs()

# 包结构打桩：connectivity 通过 `from . import plugin_config` 拿配置
_pkg = types.ModuleType("plugins.qqbot_toolbox")
_pkg.__path__ = [str(ROOT / "plugins" / "qqbot_toolbox")]
_pkg.plugin_config = types.SimpleNamespace(qqbot_toolbox_conn_timeout=8.0)
sys.modules.setdefault("plugins", types.ModuleType("plugins"))
sys.modules["plugins"].__path__ = [str(ROOT / "plugins")]
sys.modules["plugins.qqbot_toolbox"] = _pkg

import plugins.qqbot_toolbox.connectivity as conn  # noqa: E402


class TestResolveTargets(unittest.TestCase):
    def test_no_arg_returns_all_with_gateway_filled(self):
        targets, error = conn.resolve_targets("")
        self.assertEqual(error, "")
        self.assertGreater(len(targets), 3)
        # 所有项都必须有 url（网关项的空 URL 要被补全）
        for t in targets:
            self.assertTrue(t.url.startswith("http"), f"{t.key} 没有 url")
        keys = [t.key for t in targets]
        self.assertIn("mistral", keys)
        self.assertIn("qqbot-gw", keys)

    def test_keyword_filter(self):
        targets, error = conn.resolve_targets("mistral")
        self.assertEqual(error, "")
        self.assertTrue(all("mistral" in t.key or "mistral" in t.name.lower() for t in targets))

    def test_keyword_case_insensitive(self):
        targets, error = conn.resolve_targets("MISTRAL")
        self.assertEqual(error, "")
        self.assertTrue(targets)

    def test_direct_url(self):
        targets, error = conn.resolve_targets("https://api.mistral.ai/v1/models")
        self.assertEqual(error, "")
        self.assertEqual(targets[0].url, "https://api.mistral.ai/v1/models")

    def test_rejects_non_http_scheme(self):
        for bad in ("ftp://example.com", "file:///etc/passwd", "javascript:alert(1)"):
            targets, error = conn.resolve_targets(bad)
            if "://" in bad:
                self.assertEqual(targets, [])
                self.assertTrue(error)

    def test_unknown_keyword(self):
        targets, error = conn.resolve_targets("不存在的服务")
        self.assertEqual(targets, [])
        self.assertIn("没找到", error)
        self.assertIn("mistral", error)  # 提示里给出可用关键词

    def test_gateway_url_uses_config(self):
        self.assertEqual(conn._gateway_url(), "http://gw.local:3000/v1/models")


class TestProbe(unittest.TestCase):
    def run_probe(self, url):
        return asyncio.run(conn.probe(url))

    def setUp(self):
        _FakeAsyncClient.responses = {}

    def test_ok_200(self):
        ok, detail = self.run_probe("https://a.example")
        self.assertTrue(ok)
        self.assertIn("HTTP 200", detail)

    def test_401_counts_as_reachable(self):
        """401/403 说明服务可达，只是要鉴权 —— 应判为通。"""
        _FakeAsyncClient.responses["https://auth.example"] = _FakeResponse(401)
        ok, detail = self.run_probe("https://auth.example")
        self.assertTrue(ok)
        self.assertIn("401", detail)

    def test_403_counts_as_reachable(self):
        _FakeAsyncClient.responses["https://forbidden.example"] = _FakeResponse(403)
        ok, _ = self.run_probe("https://forbidden.example")
        self.assertTrue(ok)

    def test_404_is_failure(self):
        _FakeAsyncClient.responses["https://missing.example"] = _FakeResponse(404)
        ok, detail = self.run_probe("https://missing.example")
        self.assertFalse(ok)
        self.assertIn("404", detail)

    def test_redirect_reported_not_followed(self):
        _FakeAsyncClient.responses["https://redir.example"] = _FakeResponse(
            302, "https://elsewhere.example"
        )
        ok, detail = self.run_probe("https://redir.example")
        self.assertTrue(ok)
        self.assertIn("302", detail)
        self.assertIn("elsewhere", detail)

    def test_timeout(self):
        _FakeAsyncClient.responses["https://slow.example"] = conn.httpx.TimeoutException()
        ok, detail = self.run_probe("https://slow.example")
        self.assertFalse(ok)
        self.assertIn("超时", detail)

    def test_connect_error(self):
        _FakeAsyncClient.responses["https://down.example"] = conn.httpx.ConnectError()
        ok, detail = self.run_probe("https://down.example")
        self.assertFalse(ok)
        self.assertIn("连不上", detail)


class TestMaterialize(unittest.TestCase):
    def test_materialize_fills_gateway_and_drops_empty(self):
        raw = [
            conn.Target("a", "A", "https://a.example"),
            conn.Target("qqbot-gw", "网关", ""),
            conn.Target("broken", "坏的", ""),
        ]
        out = conn._materialize(raw)
        keys = [t.key for t in out]
        self.assertEqual(keys, ["a", "qqbot-gw"])
        self.assertEqual(out[1].url, "http://gw.local:3000/v1/models")

    def test_materialize_keeps_explicit_url(self):
        raw = [conn.Target("qqbot-gw", "网关", "http://explicit.local/models")]
        out = conn._materialize(raw)
        self.assertEqual(out[0].url, "http://explicit.local/models")


if __name__ == "__main__":
    unittest.main(verbosity=2)
