import base64
import io
import json
import ssl
import tempfile
import unittest
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx2
from PIL import Image

from deployment import server
from deployment.personal_memory import PersonalMemory


class CloudClientTests(unittest.TestCase):
    def check_factory(self, use_env_proxy=None):
        transport = object()
        with patch.object(server, "DefaultHttpxClient", return_value=transport) as http_client:
            with patch.object(server, "OpenAI") as openai:
                args = ("test-key", "https://example.com/v1")
                result = server.create_cloud_client(*args) if use_env_proxy is None else server.create_cloud_client(
                    *args, use_env_proxy=use_env_proxy)
        self.assertIs(result, openai.return_value)
        self.assertEqual(http_client.call_args.kwargs["trust_env"], bool(use_env_proxy))
        self.assertNotEqual(http_client.call_args.kwargs.get("verify", True), False)
        openai.assert_called_once_with(
            api_key="test-key", base_url="https://example.com/v1", timeout=25,
            max_retries=0, http_client=transport,
        )

    def test_factory_disables_inherited_proxy_by_default(self):
        self.check_factory()

    def test_factory_can_explicitly_use_environment_proxy(self):
        self.check_factory(True)

    def test_default_transport_keeps_certificate_and_hostname_verification(self):
        with patch.object(server, "OpenAI") as openai:
            server.create_cloud_client("test-key", "https://example.com/v1")
        transport = openai.call_args.kwargs["http_client"]
        self.addCleanup(transport.close)
        pool = getattr(getattr(transport, "_transport", None), "_pool", None)
        context = getattr(pool, "_ssl_context", None)
        if context is None:
            self.skipTest("This HTTP transport does not expose its TLS context")
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)


class CloudFailureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.memory = PersonalMemory(self.temp.name)
        statement = "记住，我的公司是海淀科技大厦。"
        initial_plan = {
            "upserts": [{"key": "公司", "value": "海淀科技大厦", "aliases": ["公司"], "evidence": statement}],
            "forget_keys": [], "clear_all": False, "resolved_task": "", "app_name": "",
            "mode": "memory", "reply": "已记住。", "used_keys": [],
        }
        self.memory.prepare(statement, server.supported_apps, lambda _: json.dumps(initial_plan, ensure_ascii=False))
        self.before_json = self.memory.path.read_bytes()
        self.before_markdown = self.memory.markdown_path.read_bytes()
        self.contexts = OrderedDict()
        self.generate = Mock()
        for patcher in (
            patch.object(server, "personal_memory", self.memory),
            patch.object(server, "task_contexts", self.contexts),
            patch.object(server, "rewrite_cache", {}),
            patch.object(server, "task_refcnt", {}),
            patch.object(server, "cloud_mode", True),
            patch.object(server, "get_model_output", self.generate),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def api_error(error_type):
        request = httpx2.Request("POST", "https://example.com/v1/responses")
        return error_type(request=request)

    def assert_saved_memory_unchanged(self):
        self.assertEqual(self.memory.path.read_bytes(), self.before_json)
        self.assertEqual(self.memory.markdown_path.read_bytes(), self.before_markdown)
        self.assertEqual(len(self.memory.snapshot()["facts"]), 1)

    async def check_prepare_failure(self, error_type, expected_reason):
        self.generate.side_effect = self.api_error(error_type)
        request = server.RequestBody(task="回公司", image="", history=[], sessionId="failed-prepare")
        with patch.object(server, "cleanup_task", wraps=server.cleanup_task) as cleanup:
            reply = await server.v1(request)
        self.assertEqual(reply.action, "terminate")
        self.assertEqual(reply.parameters, {})
        self.assertIn(expected_reason, reply.reasoning)
        self.assertIn("记忆会保留", reply.reasoning)
        self.generate.assert_called_once()
        self.assertEqual(len(self.generate.call_args.args), 2)
        cleanup.assert_called_once_with("回公司", "failed-prepare")
        self.assertFalse(self.contexts)
        self.assert_saved_memory_unchanged()

    async def test_connection_failure_during_memory_preparation_is_non_destructive(self):
        await self.check_prepare_failure(server.APIConnectionError, "use_env_proxy")

    async def test_timeout_during_memory_preparation_has_a_specific_message(self):
        await self.check_prepare_failure(server.APITimeoutError, "响应超时")

    async def test_connection_errors_during_screenshot_decision_clear_cached_context(self):
        buffer = io.BytesIO()
        Image.new("RGB", (36, 64), "white").save(buffer, format="PNG")
        image = base64.b64encode(buffer.getvalue()).decode("ascii")
        for error_type in (server.APIConnectionError, server.APITimeoutError):
            with self.subTest(error=error_type.__name__):
                self.contexts[("active-task", "回公司")] = SimpleNamespace(
                    mode="task", resolved_task="导航去海淀科技大厦", app_name="百度地图", reply="", context="公司：海淀科技大厦")
                self.generate.reset_mock()
                self.generate.side_effect = self.api_error(error_type)
                request = server.RequestBody(
                    task="回公司", image=image, sessionId="active-task",
                    history=[json.dumps({"action": "open_app", "parameters": {}, "reasoning": "Opened app"})],
                )
                reply = await server.v1(request)
                self.assertEqual(reply.action, "terminate")
                self.assertFalse(self.contexts)
                self.generate.assert_called_once()
                self.assertEqual(self.generate.call_args.args[2], image)
                self.assert_saved_memory_unchanged()


if __name__ == "__main__":
    unittest.main()
