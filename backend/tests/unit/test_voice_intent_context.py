from __future__ import annotations

import unittest

from backend.agents.base import AgentContext
from backend.orchestrator.intent_resolver import IntentResolver
from backend.tools.base import ToolSpec


class VoiceIntentContextTests(unittest.TestCase):
    def test_intent_prompt_includes_asr_diagnostics(self):
        context = AgentContext(
            user_input="打开默认浏览器",
            metadata={
                "raw_voice_text": "打开默认游览器",
                "asr_original_text": "打开默认游览器",
                "asr_corrected_text": "打开默认浏览器",
                "asr_metrics": {
                    "rejected_segments": 1,
                    "segments": [{"text": "打开默认游览器", "accepted": True, "avg_logprob": -0.2, "no_speech_prob": 0.01}],
                },
            },
        )

        prompt = IntentResolver._build_prompt(
            context,
            [ToolSpec(name="app.launch", description="open app", target="local_pc", operation="launch_app")],
        )

        self.assertIn("Raw ASR text: 打开默认游览器", prompt)
        self.assertIn("Rule-corrected ASR", prompt)
        self.assertIn("Rejected low-confidence ASR segments: 1", prompt)


if __name__ == "__main__":
    unittest.main()
