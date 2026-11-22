import base64
import io
import json
import unittest
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from deployment import server


class MemoryServerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.memory = Mock()
        self.memory.prepare.return_value = self.plan()
        self.generate = Mock(return_value=json.dumps({
            "reasoning": "Waiting for the next screen.",
            "action": "wait",
            "parameters": {},
        }))
        self.contexts = OrderedDict()
        self.patchers = [
            patch.object(server, "personal_memory", self.memory, create=True),
            patch.object(server, "task_contexts", self.contexts, create=True),
            patch.object(server, "rewrite_cache", {}),
            patch.object(server, "task_refcnt", {}),
            patch.object(server, "cloud_mode", True),
            patch.object(server, "get_model_output", self.generate),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        image = Image.new("RGB", (36, 64), "white")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        self.image = base64.b64encode(buffer.getvalue()).decode("ascii")

    @staticmethod
    def plan(mode="task", **overrides):
        values = {
            "resolved_task": "Use Baidu Maps to navigate to Example College.",
            "app_name": "百度地图",
            "mode": mode,
            "reply": "Using the saved school address.",
            "context": "My school address is Example College.",
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def request(self, *, task="Go to school", session="session-a", history=None,
                image=""):
        return server.RequestBody(
            task=task,
            image=image,
            history=history if history is not None else [],
            sessionId=session,
        )

    def next_request(self, **overrides):
        values = {
            "image": self.image,
            "history": [json.dumps({
                "reasoning": "Opened the maps app.",
                "action": "open_app",
                "parameters": {"package_name": "com.baidu.BaiduMap"},
            })],
        }
        values.update(overrides)
        return self.request(**values)

    async def test_memory_commands_finish_without_a_screenshot_or_screen_call(self):
        for mode in ("memory", "answer", "clarify"):
            with self.subTest(mode=mode):
                self.memory.prepare.return_value = self.plan(
                    mode=mode, reply="The memory request has been handled.")
                reply = await server.v1(self.request(session="command-" + mode))
                self.assertEqual(reply.action, "done")
                self.assertEqual(reply.parameters, {})
                self.assertEqual(reply.reasoning,
                                 "The memory request has been handled.")
        self.generate.assert_not_called()

    async def test_first_request_uses_the_prepared_app(self):
        reply = await server.v1(self.request())
        self.assertEqual(reply.action, "open_app")
        self.assertEqual(reply.parameters["package_name"], "com.baidu.BaiduMap")
        self.assertIn(self.plan().resolved_task, reply.reasoning)
        self.memory.prepare.assert_called_once()
        args = self.memory.prepare.call_args.args
        self.assertEqual(args[0], "Go to school")
        self.assertIs(args[1], server.supported_apps)
        self.assertTrue(callable(args[2]))
        self.generate.assert_not_called()

    async def test_duplicate_first_request_does_not_repeat_memory_writes(self):
        first = await server.v1(self.request())
        retry = await server.v1(self.request())
        self.assertEqual(first.action, "open_app")
        self.assertEqual(retry.action, "open_app")
        self.memory.prepare.assert_called_once()

    async def test_same_text_in_separate_sessions_is_prepared_independently(self):
        await server.v1(self.request(session="first-session"))
        await server.v1(self.request(session="second-session"))
        self.assertEqual(self.memory.prepare.call_count, 2)
        self.assertEqual(len(self.contexts), 2)

    async def test_new_session_with_old_history_is_treated_as_a_new_task(self):
        reply = await server.v1(self.next_request(session="new-session"))
        self.assertEqual(reply.action, "open_app")
        self.memory.prepare.assert_called_once()
        self.generate.assert_not_called()

    async def test_followup_uses_resolved_goal_and_related_memory(self):
        await server.v1(self.request())
        reply = await server.v1(self.next_request())
        self.assertEqual(reply.action, "wait")
        self.memory.prepare.assert_called_once()
        self.generate.assert_called_once()
        prompt = self.generate.call_args.args[1]
        self.assertIn(self.plan().resolved_task, prompt)
        self.assertIn(self.plan().context, prompt)
        self.assertEqual(self.generate.call_args.args[2], self.image)

    async def test_terminal_model_action_clears_the_session_context(self):
        await server.v1(self.request())
        self.assertTrue(self.contexts)
        self.generate.return_value = json.dumps({
            "reasoning": "The destination has been reached.",
            "action": "done",
            "parameters": {},
        })
        reply = await server.v1(self.next_request())
        self.assertEqual(reply.action, "done")
        self.assertFalse(self.contexts)

    async def test_direct_terminate_action_clears_the_session_context(self):
        await server.v1(self.request())
        self.assertTrue(self.contexts)
        self.generate.return_value = json.dumps({
            "reasoning": "Could not continue safely.",
            "action": "terminate",
            "parameters": {},
        })
        reply = await server.v1(self.next_request())
        self.assertEqual(reply.action, "terminate")
        self.assertFalse(self.contexts)

    async def test_memory_failure_does_not_open_an_app_or_make_a_screen_call(self):
        self.memory.prepare.side_effect = ValueError("Invalid memory plan")
        with patch.object(server.traceback, "print_exc"):
            with self.assertRaises(server.HTTPException) as caught:
                await server.v1(self.request())
        self.assertEqual(caught.exception.status_code, 500)
        self.generate.assert_not_called()
        self.assertFalse(self.contexts)

    async def test_memory_can_be_disabled_without_changing_legacy_app_selection(self):
        server.personal_memory = None
        reply = await server.v1(self.request(task="打开百度地图"))
        self.assertEqual(reply.action, "open_app")
        self.assertEqual(reply.parameters["package_name"], "com.baidu.BaiduMap")
        self.memory.prepare.assert_not_called()
        self.generate.assert_not_called()

    async def test_request_without_a_session_keeps_memory_through_followup(self):
        await server.v1(self.request(session=None))
        reply = await server.v1(self.next_request(session=None))
        self.assertEqual(reply.action, "wait")
        self.memory.prepare.assert_called_once()
        self.assertIn(self.plan().resolved_task, self.generate.call_args.args[1])

    async def test_session_context_cache_is_bounded(self):
        for index in range(130):
            await server.v1(self.request(session="session-" + str(index)))
        self.assertLessEqual(len(self.contexts), 128)
        self.assertEqual(self.memory.prepare.call_count, 130)


if __name__ == "__main__":
    unittest.main()
