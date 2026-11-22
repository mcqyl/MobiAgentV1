import json
import tempfile
import unittest
from pathlib import Path

from deployment.personal_memory import PersonalMemory


APPS = {"百度地图": "com.baidu.BaiduMap", "淘宝": "com.taobao.taobao", "微信": "com.tencent.mm"}


def plan(**changes):
    value = {"upserts": [], "forget_keys": [], "clear_all": False,
             "resolved_task": "", "app_name": "", "mode": "memory", "reply": "已记住。", "used_keys": []}
    value.update(changes)
    return json.dumps(value, ensure_ascii=False)


def fact(key="学校", value="北京信息职业技术学院东坝", evidence=None, aliases=None):
    return {"key": key, "value": value, "aliases": aliases or [key],
            "evidence": evidence or f"回学校（{value}）"}


class PersonalMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.memory = PersonalMemory(self.temp.name)

    def remember(self, entry=None):
        entry = entry or fact()
        return self.memory.prepare(entry["evidence"], APPS, lambda _: plan(upserts=[entry]))

    def test_empty_store_does_not_seed_example(self):
        self.assertEqual(self.memory.snapshot()["facts"], [])
        self.assertNotIn("学校", self.memory.markdown_path.read_text(encoding="utf-8"))

    def test_explicit_parenthetical_fact_is_persisted_and_recalled_after_reload(self):
        self.remember()
        reloaded = PersonalMemory(self.temp.name)
        captured = []
        def generate(prompt):
            captured.append(prompt)
            return plan(mode="task", app_name="百度地图", resolved_task="导航到北京信息职业技术学院东坝", used_keys=["学校"], reply="")
        prepared = reloaded.prepare("回学校", APPS, generate)
        self.assertIn("北京信息职业技术学院东坝", prepared.resolved_task)
        self.assertIn("北京信息职业技术学院东坝", prepared.context)
        self.assertIn("北京信息职业技术学院东坝", captured[0])
        self.assertEqual(len(reloaded.snapshot()["facts"]), 1)

    def test_correction_replaces_old_value_and_duplicate_does_not_grow(self):
        self.remember()
        self.remember()
        correction = fact(value="上海交通大学", evidence="我的学校改成上海交通大学")
        self.remember(correction)
        facts = self.memory.snapshot()["facts"]
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["value"], "上海交通大学")
        self.assertNotIn("北京信息", self.memory.markdown_path.read_text(encoding="utf-8"))

    def test_generic_preference_and_contact_fields(self):
        self.remember(fact(key="饮料甜度", value="少糖", evidence="以后我的饮料默认少糖", aliases=["饮料", "奶茶"]))
        self.remember(fact(key="老婆", value="小李", evidence="给老婆（小李）发微信", aliases=["老婆"]))
        self.assertEqual(len(self.memory.snapshot()["facts"]), 2)

    def test_forget_removes_json_and_markdown_without_old_fact_backup(self):
        self.remember()
        self.memory.prepare("忘掉学校", APPS, lambda _: plan(forget_keys=["学校"], reply="已忘记。"))
        self.assertEqual(self.memory.snapshot()["facts"], [])
        for path in Path(self.temp.name).iterdir():
            self.assertNotIn("北京信息", path.read_text(encoding="utf-8"))

    def test_clear_requires_explicit_instruction(self):
        self.remember()
        with self.assertRaises(ValueError):
            self.memory.prepare("打开百度地图", APPS, lambda _: plan(clear_all=True))
        self.assertEqual(len(self.memory.snapshot()["facts"]), 1)
        self.memory.prepare("清空所有记忆", APPS, lambda _: plan(clear_all=True, reply="记忆已清空。"))
        self.assertEqual(self.memory.snapshot()["facts"], [])

    def test_ordinary_search_cannot_create_a_personal_fact(self):
        prepared = self.remember(fact(value="天安门", evidence="搜索天安门"))
        self.assertEqual(prepared.mode, "clarify")
        self.assertIn("未保存", prepared.reply)
        self.assertNotIn("已记住", prepared.reply)
        self.assertEqual(self.memory.snapshot()["facts"], [])

    def test_hypotheticals_and_quoted_search_are_not_personal_facts(self):
        for evidence in ["比如我的学校是天安门", "搜索“我的学校是天安门”"]:
            with self.subTest(evidence=evidence):
                prepared = self.remember(fact(value="天安门", evidence=evidence))
                self.assertEqual(prepared.mode, "clarify")
                self.assertIn("未保存", prepared.reply)
                self.assertNotIn("已记住", prepared.reply)
        self.assertEqual(self.memory.snapshot()["facts"], [])

    def test_invented_value_is_not_saved_or_reported_as_remembered(self):
        entry = fact(value="不存在地点")
        prepared = self.memory.prepare("回学校（北京信息职业技术学院东坝）", APPS, lambda _: plan(upserts=[entry]))
        self.assertEqual(prepared.mode, "clarify")
        self.assertIn("未保存", prepared.reply)
        self.assertNotIn("已记住", prepared.reply)
        self.assertEqual(self.memory.snapshot()["facts"], [])

    def test_paraphrased_evidence_is_repaired_only_from_explicit_original_task(self):
        task = "回学校（北京信息职业技术学院东坝）"
        entry = fact(evidence="我的学校是上海交通大学")
        prepared = self.memory.prepare(task, APPS, lambda _: plan(upserts=[entry]))
        self.assertEqual(prepared.mode, "memory")
        facts = self.memory.snapshot()["facts"]
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["value"], "北京信息职业技术学院东坝")
        self.assertEqual(facts[0]["evidence"], task)
        self.assertNotIn("上海交通大学", self.memory.markdown_path.read_text(encoding="utf-8"))

    def test_batch_commits_valid_subset_without_invalid_candidate_pollution(self):
        valid = fact()
        invalid = fact(key="住处", value="不存在地点")
        prepared = self.memory.prepare(valid["evidence"], APPS, lambda _: plan(upserts=[valid, invalid]))
        self.assertEqual(prepared.mode, "memory")
        self.assertIn("未保存", prepared.reply)
        facts = self.memory.snapshot()["facts"]
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["key"], "学校")
        self.assertEqual(facts[0]["value"], valid["value"])
        self.assertNotIn("不存在地点", self.memory.markdown_path.read_text(encoding="utf-8"))

    def test_corrupt_file_is_not_reset(self):
        self.memory.path.write_text("not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            PersonalMemory(self.temp.name)
        self.assertEqual(self.memory.path.read_text(encoding="utf-8"), "not json")

    def test_unrelated_facts_are_not_injected(self):
        self.remember()
        self.remember(fact(key="饮料甜度", value="少糖", evidence="以后我的饮料默认少糖", aliases=["奶茶"]))
        prompts = []
        def generate(prompt):
            prompts.append(prompt)
            return plan(mode="task", app_name="百度地图", resolved_task="回学校", used_keys=["学校"], reply="")
        prepared = self.memory.prepare("回学校", APPS, generate)
        self.assertIn("少糖", prompts[0])
        self.assertNotIn("少糖", prepared.context)

    def test_unknown_reference_clarifies_without_writing(self):
        prepared = self.memory.prepare("去公司", APPS, lambda _: plan(mode="clarify", reply="公司地址是哪里？"))
        self.assertEqual(prepared.mode, "clarify")
        self.assertEqual(self.memory.snapshot()["facts"], [])

    def test_markdown_is_mirror_and_json_remains_authoritative(self):
        self.remember()
        data = self.memory.snapshot()
        data["facts"][0]["value"] = "上海交通大学"
        self.memory.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        reloaded = PersonalMemory(self.temp.name)
        self.assertIn("上海交通大学", reloaded.markdown_path.read_text(encoding="utf-8"))

    def test_fenced_json_supported(self):
        prepared = self.memory.prepare("你记住了什么", APPS, lambda _: "```json\n" + plan(mode="answer", reply="尚未保存个人记忆。") + "\n```")
        self.assertEqual(prepared.mode, "answer")

    def test_unknown_used_key_and_delete_key_are_rejected(self):
        with self.assertRaises(ValueError):
            self.memory.prepare("打开淘宝", APPS, lambda _: plan(mode="task", app_name="淘宝", resolved_task="打开淘宝", used_keys=["不存在"], reply=""))
        with self.assertRaises(ValueError):
            self.memory.prepare("忘掉不存在的记忆", APPS, lambda _: plan(forget_keys=["不存在"]))

    def test_deleting_one_fact_cannot_delete_an_unmentioned_fact(self):
        self.remember()
        self.remember(fact(key="公司", value="中关村", evidence="我的公司在中关村", aliases=["公司"]))
        with self.assertRaises(ValueError):
            self.memory.prepare("忘掉学校", APPS, lambda _: plan(forget_keys=["公司"]))
        self.assertEqual(len(self.memory.snapshot()["facts"]), 2)

    def test_service_app_defaults_are_not_saved_as_personal_facts(self):
        memory = PersonalMemory(self.temp.name, default_apps={"navigation": "百度地图", "unsupported": "不存在的应用"})
        prompts = []
        def generate(prompt):
            prompts.append(prompt)
            return plan(mode="task", resolved_task="导航到天安门", app_name="百度地图", reply="")
        memory.prepare("去天安门", APPS, generate)
        self.assertIn('"navigation": "百度地图"', prompts[0])
        self.assertNotIn("不存在的应用", prompts[0])
        self.assertEqual(memory.snapshot()["facts"], [])

    def test_fact_limit_rejects_entire_update(self):
        data = {"version": 1, "facts": [{"key": f"fact-{index}", "value": "old", "aliases": []} for index in range(256)]}
        self.memory.path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.remember()
        self.assertEqual(len(self.memory.snapshot()["facts"]), 256)

    def test_listing_uses_recent_subset_with_total_count(self):
        data = {"version": 1, "facts": [{"key": f"fact-{index}", "value": str(index), "aliases": []} for index in range(20)]}
        self.memory.path.write_text(json.dumps(data), encoding="utf-8")
        prompts = []
        def generate(prompt):
            prompts.append(prompt)
            return plan(mode="answer", reply="共有20条记忆，这是最近16条。")
        self.memory.prepare("你记住了我哪些信息", APPS, generate)
        self.assertIn('"fact-19"', prompts[0])
        self.assertNotIn('"fact-0"', prompts[0])
        self.assertIn("TOTAL_SAVED_FACTS:\n20", prompts[0])

    def test_example_elsewhere_does_not_invalidate_real_fact_evidence(self):
        entry = fact(evidence="我的学校是北京信息职业技术学院东坝")
        self.memory.prepare(entry["evidence"] + "。比如下次我会说回学校。", APPS, lambda _: plan(upserts=[entry]))
        self.assertEqual(len(self.memory.snapshot()["facts"]), 1)

    def test_old_single_character_home_alias_is_recalled_in_large_store(self):
        facts = [{"key": "住处", "value": "紫竹院南路8号", "aliases": ["家"]}]
        facts.extend({"key": f"unrelated-{index}", "value": str(index), "aliases": []} for index in range(20))
        self.memory.path.write_text(json.dumps({"version": 1, "facts": facts}, ensure_ascii=False), encoding="utf-8")
        prompts = []
        def generate(prompt):
            prompts.append(prompt)
            return plan(mode="task", resolved_task="导航到紫竹院南路8号", app_name="百度地图", used_keys=["住处"], reply="")
        prepared = self.memory.prepare("回家", APPS, generate)
        provided = json.loads(prompts[0].split("\nSAVED_FACTS:\n", 1)[1].split("\nSUPPORTED_APPS:\n", 1)[0])
        self.assertLessEqual(len(provided), 16)
        self.assertIn("住处", [item["key"] for item in provided])
        self.assertIn("紫竹院南路8号", prepared.context)

    def test_app_precedence_instructions_include_saved_preference_and_service_default(self):
        memory = PersonalMemory(self.temp.name, default_apps={"navigation": "百度地图"})
        apps = {**APPS, "高德地图": "com.autonavi.minimap"}
        preference = fact(key="导航软件", value="高德地图", evidence="以后导航默认用高德地图", aliases=["导航"])
        memory.prepare(preference["evidence"], apps, lambda _: plan(upserts=[preference]))
        home = fact(key="住处", value="紫竹院南路8号", evidence="我的住处是紫竹院南路8号", aliases=["家"])
        memory.prepare(home["evidence"], apps, lambda _: plan(upserts=[home]))
        for task, app_name in [("回家", "高德地图"), ("用百度地图回家", "百度地图")]:
            with self.subTest(task=task):
                prompts = []
                def generate(prompt):
                    prompts.append(prompt)
                    return plan(mode="task", resolved_task=f"用{app_name}导航到紫竹院南路8号", app_name=app_name, used_keys=["住处", "导航软件"], reply="")
                prepared = memory.prepare(task, apps, generate)
                self.assertEqual(prepared.app_name, app_name)
                self.assertIn("if the user names an app, prefer it.", prompts[0])
                self.assertIn("no relevant saved preference provides one", prompts[0])
                self.assertIn('"navigation": "百度地图"', prompts[0])
                self.assertIn('"value": "高德地图"', prompts[0])
        self.assertEqual(len(memory.snapshot()["facts"]), 2)


if __name__ == "__main__":
    unittest.main()
