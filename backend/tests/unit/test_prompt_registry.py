"""Guards for the central prompt resource layer (backend/prompts)."""

from __future__ import annotations

import unittest
from string import Template

from backend.prompts import PROMPT_REGISTRY, list_prompt_keys, render_prompt

EXPECTED_KEYS = {
    "agent.programming",
    "agent.ecommerce",
    "agent.game_development",
    "agent.video_animation",
    "execution.retry",
    "expression.classifier",
    "review.skill",
    "review.jury",
    "review.ecosystem",
    "review.skill_assist_fallback",
    "voice.intent_resolver",
    "voice.asr_initial_yue",
    "voice.asr_initial_auto",
    "voice.asr_initial_zh",
}


class PromptRegistryTests(unittest.TestCase):
    def test_registry_keys_are_exactly_the_known_set(self):
        self.assertEqual(set(PROMPT_REGISTRY), EXPECTED_KEYS)
        self.assertEqual(list_prompt_keys(), sorted(EXPECTED_KEYS))

    def test_all_entries_are_templates(self):
        for key, template in PROMPT_REGISTRY.items():
            self.assertIsInstance(template, Template, key)

    def test_all_templates_render_without_leftover_placeholders(self):
        for key, template in PROMPT_REGISTRY.items():
            identifiers = template.get_identifiers()
            rendered = template.substitute({name: f"<{name}>" for name in identifiers})
            self.assertNotIn("$$", rendered, key)
            for name in identifiers:
                self.assertNotIn(f"${name}", rendered, key)
                self.assertIn(f"<{name}>", rendered, key)

    def test_render_prompt_substitutes_params(self):
        rendered = render_prompt("expression.classifier", text="你好")
        self.assertIn("Assistant reply: 你好", rendered)
        self.assertIn('{"emotion":"happy","action":"wave_hand"}', rendered)

    def test_render_prompt_unknown_key_raises(self):
        with self.assertRaises(KeyError):
            render_prompt("nonexistent.key")

    def test_prompts_layer_has_no_upward_imports(self):
        import backend.prompts as prompts_pkg

        for module_name in ("agent_roles", "execution", "expression", "review", "voice"):
            module = __import__(f"backend.prompts.{module_name}", fromlist=[module_name])
            source_file = module.__file__ or ""
            with open(source_file, encoding="utf-8") as handle:
                source = handle.read()
            for forbidden in ("backend.app", "backend.orchestrator", "backend.agents", "backend.services"):
                self.assertNotIn(f"from {forbidden}", source, module_name)
                self.assertNotIn(f"import {forbidden}", source, module_name)
        self.assertTrue(prompts_pkg.__file__)


if __name__ == "__main__":
    unittest.main()
