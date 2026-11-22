import json
import tempfile
import unittest

from deployment.personal_memory import PersonalMemory


APPS = {"百度地图": "com.baidu.BaiduMap"}
SCHOOL = "北京信息职业技术学院东坝"


def memory_plan(task, **changes):
    result = {
        "upserts": [],
        "forget_keys": [],
        "clear_all": False,
        "resolved_task": task,
        "app_name": "百度地图",
        "mode": "task",
        "reply": "",
        "used_keys": [],
    }
    result.update(changes)
    return json.dumps(result, ensure_ascii=False)


def school_fact(evidence=None):
    return {
        "key": "school",
        "value": SCHOOL,
        "aliases": ["学校"],
        "evidence": evidence or "回学校 " + SCHOOL,
    }


def temporary_transport_fact(task):
    return {
        "key": "transport_preference",
        "value": "骑车",
        "aliases": ["交通偏好"],
        "evidence": task,
    }


class MemoryEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.memory = PersonalMemory(self.temp.name)

    def prepare(self, task, **changes):
        return self.memory.prepare(
            task, APPS, lambda _: memory_plan(task, **changes))

    def test_school_alias_can_be_bound_without_parentheses(self):
        for task in (
            "导航回学校 " + SCHOOL + " 骑车",
            "打开百度地图 导航回学校 " + SCHOOL + " 骑车",
        ):
            with self.subTest(task=task):
                prepared = self.prepare(
                    task, upserts=[school_fact()], used_keys=["school"])
                self.assertEqual(prepared.mode, "task")
                saved = self.memory.snapshot()["facts"]
                self.assertEqual(len(saved), 1)
                self.assertEqual(saved[0]["key"], "school")
                self.assertEqual(saved[0]["value"], SCHOOL)
                self.assertIn(SCHOOL, prepared.context)

    def test_invalid_preference_does_not_discard_valid_alias(self):
        task = "打开百度地图 导航回学校 " + SCHOOL + " 骑车"
        prepared = self.prepare(
            task,
            upserts=[school_fact(), temporary_transport_fact(task)],
            used_keys=["school", "transport_preference"],
        )
        self.assertEqual(prepared.mode, "task")
        facts = self.memory.snapshot()["facts"]
        self.assertEqual([fact["key"] for fact in facts], ["school"])
        self.assertIn(SCHOOL, prepared.context)
        self.assertNotIn("transport_preference", prepared.context)
        self.assertNotIn("交通偏好", prepared.context)

    def test_todays_transport_is_not_a_preference_in_an_explicit_memory_command(self):
        task = "记住，我的学校是" + SCHOOL + "，今天骑车"
        for key in ("交通方式", "navigation_mode"):
            with self.subTest(key=key):
                self.memory = PersonalMemory(self.temp.name + "/" + key)
                prepared = self.prepare(
                    task,
                    mode="memory",
                    app_name="",
                    resolved_task="",
                    reply="已记住学校和交通方式。",
                    upserts=[school_fact(evidence=task), {
                        "key": key,
                        "value": "骑车",
                        "aliases": ["交通方式"],
                        "evidence": task,
                    }],
                )
                self.assertEqual(prepared.mode, "memory")
                facts = self.memory.snapshot()["facts"]
                self.assertEqual([fact["key"] for fact in facts], ["school"])
                self.assertEqual(facts[0]["value"], SCHOOL)

    def test_rejected_candidates_preserve_the_full_explicit_mobile_task(self):
        task = "打开百度地图 骑车导航到天安门"
        prepared = self.prepare(
            task,
            upserts=[temporary_transport_fact(task)],
            used_keys=["transport_preference"],
            resolved_task="打开百度地图，步行导航到颐和园",
        )
        self.assertEqual(prepared.mode, "task")
        self.assertEqual(prepared.resolved_task, task)
        self.assertIn("骑车", prepared.resolved_task)
        self.assertNotIn("颐和园", prepared.resolved_task)
        self.assertEqual(prepared.context, "")
        self.assertEqual(self.memory.snapshot()["facts"], [])

    def test_unsupported_pure_memory_candidate_is_not_acknowledged_as_saved(self):
        task = "记住我的学校地址"
        prepared = self.prepare(
            task,
            mode="memory",
            app_name="",
            resolved_task="",
            reply="已记住你的学校地址。",
            upserts=[school_fact(evidence="我的学校地址是" + SCHOOL)],
            used_keys=["school"],
        )
        self.assertEqual(prepared.mode, "clarify")
        self.assertRegex(prepared.reply, "未|没有|不能|无法|未能")
        self.assertEqual(prepared.context, "")
        self.assertEqual(self.memory.snapshot()["facts"], [])

    def test_unsupported_repeat_of_existing_value_is_a_noop(self):
        original = "记住，我的学校是" + SCHOOL
        self.prepare(
            original,
            mode="memory",
            app_name="",
            resolved_task="",
            reply="已记住。",
            upserts=[school_fact(evidence=original)],
        )
        before = self.memory.snapshot()
        prepared = self.prepare(
            "回学校",
            upserts=[school_fact(evidence=original)],
            used_keys=["school"],
            resolved_task="用百度地图导航到" + SCHOOL,
        )
        self.assertEqual(prepared.mode, "task")
        self.assertEqual(self.memory.snapshot(), before)
        self.assertIn(SCHOOL, prepared.context)

    def test_summarized_evidence_is_repaired_from_explicit_original_input(self):
        task = "记住，我的公司地址是北京市朝阳区示例路8号。"
        address = "北京市朝阳区示例路8号"
        prepared = self.prepare(
            task,
            mode="memory",
            app_name="",
            resolved_task="",
            reply="已记住公司地址。",
            upserts=[{
                "key": "company_address",
                "value": address,
                "aliases": ["公司地址", "公司"],
                "evidence": "用户明确说明公司位于" + address,
            }],
        )
        self.assertEqual(prepared.mode, "memory")
        saved = self.memory.snapshot()["facts"]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["value"], address)
        self.assertIn(saved[0]["evidence"], task)
        self.assertIn(address, saved[0]["evidence"])

    def test_rejected_candidate_key_is_removed_from_recalled_context(self):
        task = "打开百度地图导航到天安门"
        prepared = self.prepare(
            task,
            upserts=[{
                "key": "invented_address",
                "value": "不存在的私人地址",
                "aliases": ["学校"],
                "evidence": "我的学校是不存在的私人地址",
            }],
            used_keys=["invented_address"],
            resolved_task="导航到不存在的私人地址",
        )
        self.assertEqual(prepared.mode, "task")
        self.assertEqual(prepared.resolved_task, task)
        self.assertEqual(prepared.context, "")
        self.assertEqual(self.memory.snapshot()["facts"], [])

    def test_unrelated_rejected_school_does_not_block_an_explicit_destination(self):
        task = "打开百度地图导航到天安门"
        prepared = self.prepare(
            task,
            upserts=[{
                "key": "school",
                "value": "虚构地方",
                "aliases": ["学校"],
                "evidence": "我的学校是虚构地方",
            }],
            used_keys=["school"],
            resolved_task="打开百度地图导航到虚构地方",
        )
        self.assertEqual(prepared.mode, "task")
        self.assertEqual(prepared.resolved_task, task)
        self.assertEqual(prepared.app_name, "百度地图")
        self.assertEqual(prepared.context, "")
        self.assertEqual(self.memory.snapshot()["facts"], [])

    def test_memory_mode_with_no_changes_does_not_repeat_a_false_saved_reply(self):
        prepared = self.prepare(
            "记住我的学校地址",
            mode="memory",
            app_name="",
            resolved_task="",
            reply="已记住你的学校地址。",
        )
        self.assertEqual(self.memory.snapshot()["facts"], [])
        self.assertNotEqual(prepared.reply, "已记住你的学校地址。")
        self.assertRegex(prepared.reply, "未|没有|无.*(?:变更|更改|修改)")
        self.assertEqual(prepared.context, "")

    def test_missing_personal_address_cannot_navigate_to_a_fabricated_value(self):
        task = "打开百度地图导航回学校"
        prepared = self.prepare(
            task,
            upserts=[school_fact(evidence="我的学校是" + SCHOOL)],
            used_keys=["school"],
            resolved_task="用百度地图导航到" + SCHOOL,
        )
        self.assertEqual(prepared.mode, "clarify")
        self.assertEqual(prepared.app_name, "")
        self.assertNotIn(SCHOOL, prepared.resolved_task)
        self.assertEqual(prepared.context, "")
        self.assertEqual(self.memory.snapshot()["facts"], [])


if __name__ == "__main__":
    unittest.main()
