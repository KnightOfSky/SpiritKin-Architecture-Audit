import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.skills import SkillRegistry, SkillRunner, SkillSpec, SkillStepSpec
from backend.skills.workflow import build_workflow_skill_specs, workflow_skill_name
from backend.tools import ExecutionTool, ToolRegistry, ToolSpec


class SkillLayerTests(unittest.TestCase):
    def test_registry_can_find_skills_by_trigger_intent(self):
        registry = SkillRegistry([
            SkillSpec(name="browser.search", description="打开浏览器并搜索", trigger_intents=("browser_search",))
        ])

        matches = registry.find_by_intent("browser_search")

        self.assertEqual([skill.name for skill in matches], ["browser.search"])

    def test_runner_builds_dry_run_plan_with_input_substitution(self):
        skill = SkillSpec(
            name="browser.search",
            description="打开浏览器并搜索",
            trigger_intents=("browser_search",),
            input_schema={"query": "str"},
            steps=(SkillStepSpec("app.launch", {"app_name": "browser"}), SkillStepSpec("browser.search", {"query": "{{query}}"})),
            tool_allowlist=("app.launch", "browser.search"),
            eval_cases=("打开浏览器搜索 SpiritKin",),
        )
        runner = SkillRunner(SkillRegistry([skill]), ToolRegistry())

        result = runner.run("browser.search", {"query": "SpiritKin"}, dry_run=True)

        self.assertTrue(result.success)
        self.assertEqual(result.metadata["planned_steps"][1]["arguments"], {"query": "SpiritKin"})

    def test_runner_persists_dry_run_trajectory(self):
        skill = SkillSpec(
            name="browser.search",
            description="打开浏览器并搜索",
            input_schema={"query": "str"},
            steps=(SkillStepSpec("browser.search", {"query": "{{query}}"}),),
            tool_allowlist=("browser.search",),
        )
        runner = SkillRunner(SkillRegistry([skill]), ToolRegistry())

        with TemporaryDirectory() as temp_dir:
            trajectory_path = Path(temp_dir) / "trajectories.jsonl"
            with patch.dict("os.environ", {"SPIRITKIN_TRAJECTORY_LOG": str(trajectory_path)}, clear=False):
                result = runner.run("browser.search", {"query": "SpiritKin", "actor": "unit"}, dry_run=True)

            records = [json.loads(line) for line in trajectory_path.read_text(encoding="utf-8").splitlines()]

        self.assertTrue(result.success)
        self.assertEqual(result.metadata["trajectory_record"]["metadata"]["source"], "skill_runner.run")
        self.assertEqual(records[0]["metadata"]["skill_name"], "browser.search")
        self.assertEqual(records[0]["overall_success"], True)
        self.assertEqual(records[0]["agent_id"], "unit")

    def test_runner_enforces_tool_allowlist_before_invocation(self):
        skill = SkillSpec(
            name="unsafe.skill",
            description="错误示例",
            steps=(SkillStepSpec("file.delete", {"path": "demo.txt"}),),
            tool_allowlist=("app.launch",),
        )
        tool_registry = ToolRegistry([
            ExecutionTool(ToolSpec(name="file.delete", description="删除文件", target="local_pc", operation="file_delete"))
        ])
        runner = SkillRunner(SkillRegistry([skill]), tool_registry)

        result = runner.run("unsafe.skill")

        self.assertFalse(result.success)
        self.assertEqual(result.metadata["error_code"], "tool_not_allowed")

    def test_runner_persists_failed_skill_trajectory(self):
        skill = SkillSpec(
            name="unsafe.skill",
            description="错误示例",
            steps=(SkillStepSpec("file.delete", {"path": "demo.txt"}),),
            tool_allowlist=("app.launch",),
        )
        runner = SkillRunner(SkillRegistry([skill]), ToolRegistry())

        with TemporaryDirectory() as temp_dir:
            trajectory_path = Path(temp_dir) / "trajectories.jsonl"
            with patch.dict("os.environ", {"SPIRITKIN_TRAJECTORY_LOG": str(trajectory_path)}, clear=False):
                result = runner.run("unsafe.skill", {"user_input": "删除 demo"})

            records = [json.loads(line) for line in trajectory_path.read_text(encoding="utf-8").splitlines()]

        self.assertFalse(result.success)
        self.assertEqual(result.metadata["trajectory_record"]["bottleneck_stage"], "skill")
        self.assertEqual(records[0]["metadata"]["source"], "skill_runner.run")
        self.assertEqual(records[0]["metadata"]["skill_name"], "unsafe.skill")
        self.assertEqual(records[0]["overall_success"], False)

    def test_workflow_candidates_can_be_converted_to_candidate_skills(self):
        tools = [ToolSpec(name="browser.open_url", description="打开网页", target="local_pc", operation="browser_open_url", risk_level="medium", schema={"url": "string"})]
        candidates = [
            {
                "target": "local_pc",
                "operation": "browser_open_url",
                "success_count": 3,
                "total_count": 4,
                "success_rate": 0.75,
                "example_params": {"url": "https://example.com"},
            }
        ]

        skills = build_workflow_skill_specs(candidates, tools)

        self.assertEqual(skills[0].name, workflow_skill_name("local_pc", "browser_open_url"))
        self.assertEqual(skills[0].steps[0].tool_name, "browser.open_url")
        self.assertEqual(skills[0].tool_allowlist, ("browser.open_url",))
        self.assertEqual(skills[0].metadata["status"], "candidate")


if __name__ == "__main__":
    unittest.main()
