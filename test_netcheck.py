"""qqbot_toolbox.netcheck 的离线单测。

netcheck 不依赖 NoneBot，直接按文件路径加载即可（包里其他模块会初始化插件配置，
这里刻意不走 ``import plugins.qqbot_toolbox``，避免拖进 NoneBot 运行时）。

覆盖：输入净化（含各种注入尝试）、curl 结果解析（用假 curl 脚本）、
ICMP 校验和、TCP 探测的判定分支。**不真的联网**。
"""

import asyncio
import importlib.util
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location(
    "qqbot_netcheck", ROOT / "plugins" / "qqbot_toolbox" / "netcheck.py"
)
nc = importlib.util.module_from_spec(_SPEC)
sys.modules["qqbot_netcheck"] = nc  # dataclass 需要模块已注册
_SPEC.loader.exec_module(nc)  # type: ignore[union-attr]


class TestParseEndpoint(unittest.TestCase):
    def test_bare_domain(self):
        ep, err = nc.parse_endpoint("api.mistral.ai")
        self.assertEqual(err, "")
        self.assertEqual(ep.host, "api.mistral.ai")
        self.assertIsNone(ep.port)
        self.assertIsNone(ep.url)

    def test_ipv4(self):
        ep, err = nc.parse_endpoint("8.8.8.8")
        self.assertEqual(err, "")
        self.assertEqual(ep.host, "8.8.8.8")

    def test_domain_with_port(self):
        ep, err = nc.parse_endpoint("example.com:8443")
        self.assertEqual(err, "")
        self.assertEqual(ep.host, "example.com")
        self.assertEqual(ep.port, 8443)

    def test_ip_with_port(self):
        ep, err = nc.parse_endpoint("1.1.1.1:53")
        self.assertEqual(err, "")
        self.assertEqual(ep.port, 53)

    def test_url(self):
        ep, err = nc.parse_endpoint("https://api.mistral.ai/v1/models")
        self.assertEqual(err, "")
        self.assertTrue(ep.is_url)
        self.assertEqual(ep.host, "api.mistral.ai")

    def test_url_with_credentials_is_stripped(self):
        ep, _ = nc.parse_endpoint("https://user:pass@example.com/x")
        self.assertEqual(ep.host, "example.com")

    def test_ipv6_url(self):
        ep, err = nc.parse_endpoint("http://[::1]:8080/x")
        self.assertEqual(err, "")
        self.assertEqual(ep.host, "::1")
        self.assertEqual(ep.port, 8080)

    def test_empty(self):
        ep, err = nc.parse_endpoint("")
        self.assertIsNone(ep)
        self.assertIn("用法", err)

    def test_rejects_other_schemes(self):
        for bad in ("ftp://x.com", "file:///etc/passwd", "javascript:alert(1)"):
            ep, err = nc.parse_endpoint(bad)
            self.assertIsNone(ep, bad)
            self.assertTrue(err, bad)

    def test_rejects_shell_metacharacters(self):
        """绝不把带 shell 元字符的东西放进来（子进程也不经 shell，双重保险）。"""
        for bad in (
            "rm -rf /",
            "example.com;id",
            "example.com|id",
            "example.com`id`",
            "example.com&&id",
            "http://x.com/$(whoami)",
            "http://x.com/a;b",
        ):
            ep, err = nc.parse_endpoint(bad)
            self.assertIsNone(ep, f"{bad} 不该被接受")
            self.assertTrue(err, bad)

    def test_rejects_chinese_and_plain_words(self):
        for bad in ("你好", "ping", "api", "a"):
            ep, err = nc.parse_endpoint(bad)
            self.assertIsNone(ep, bad)

    def test_rejects_bad_port(self):
        for bad in ("example.com:0", "example.com:99999", "example.com:abc"):
            ep, err = nc.parse_endpoint(bad)
            self.assertIsNone(ep, bad)


class TestChecksum(unittest.TestCase):
    def test_known_vector(self):
        # RFC 1071 §3 的例子：和为 0x21CF，取反得 0x220D
        self.assertEqual(nc._checksum(b"\x00\x01\xf2\x03\xf4\xf5\xf6\xf7"), 0x220D)

    def test_odd_length(self):
        self.assertIsInstance(nc._checksum(b"abc"), int)


class TestCurlProbeWithFakeBinary(unittest.TestCase):
    """用一个假的 curl 可执行文件，测结果解析与错误翻译（不联网）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self._old_path = os.environ.get("PATH", "")

    def tearDown(self):
        os.environ["PATH"] = self._old_path
        self._tmp.cleanup()

    def _install_fake_curl(self, script: str) -> None:
        p = self.dir / "curl"
        p.write_text("#!/bin/sh\n" + script, encoding="utf-8")
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
        os.environ["PATH"] = f"{self.dir}{os.pathsep}{self._old_path}"

    def run_curl(self, url="https://x.example"):
        return asyncio.run(nc.curl_probe(url, timeout=5))

    def test_parses_fields(self):
        self._install_fake_curl(
            'printf "%s" "200|0.123|0.045|0.010|1.2.3.4|512|"\n'
        )
        r = self.run_curl()
        self.assertTrue(r["ok"])
        self.assertEqual(r["http_code"], "200")
        self.assertAlmostEqual(r["time_total"], 0.123)
        self.assertEqual(r["remote_ip"], "1.2.3.4")

    def test_dns_failure_translated(self):
        self._install_fake_curl(
            'printf "%s" "000|0|0|0||0|"\n'
            'echo "curl: (6) Could not resolve host: x.example" >&2\n'
            "exit 6\n"
        )
        r = self.run_curl()
        self.assertFalse(r["ok"])
        self.assertIn("域名解析失败", r["error"])

    def test_connect_failure_translated(self):
        self._install_fake_curl('echo "curl: (7) Failed to connect" >&2\nexit 7\n')
        r = self.run_curl()
        self.assertFalse(r["ok"])
        self.assertIn("连不上", r["error"])

    def test_timeout_translated(self):
        self._install_fake_curl('echo "curl: (28) timeout" >&2\nexit 28\n')
        r = self.run_curl()
        self.assertFalse(r["ok"])
        self.assertIn("超时", r["error"])

    def test_missing_curl_binary(self):
        os.environ["PATH"] = str(self.dir)  # 空目录，没有 curl
        r = self.run_curl()
        self.assertFalse(r["ok"])
        self.assertIn("没有 curl", r["error"])


class TestTCPPing(unittest.TestCase):
    def test_open_local_port_ok(self):
        import socket

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            r = asyncio.run(nc._tcp_ping("127.0.0.1", socket.AF_INET, 3.0, port))
        finally:
            srv.close()
        self.assertTrue(r["ok"])
        self.assertEqual(r["method"], "tcp")

    def test_closed_port_still_means_host_alive(self):
        """端口拒绝连接 = 主机在线，应判「通」并注明端口未开。"""
        import socket

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()  # 立刻释放，端口不可连
        r = asyncio.run(nc._tcp_ping("127.0.0.1", socket.AF_INET, 2.0, port))
        self.assertTrue(r["ok"])
        self.assertIn("拒绝", r.get("note", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
