from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTROLLER_PATH = REPO_ROOT / "frontend" / "js" / "opening_bubble.js"
AVATAR_PATH = REPO_ROOT / "frontend" / "avatar_3d.html"


def test_opening_bubble_state_machine_with_node() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = f"""
const assert = require('assert');
const {{ OpeningBubbleController }} = require({json.dumps(str(CONTROLLER_PATH))});
let now = 1000000;
let nextId = 1;
const timers = new Map();
const actions = [];
const feedback = [];
const controller = new OpeningBubbleController({{
  now: () => now,
  setTimer: (fn, ms) => {{ const id = nextId++; timers.set(id, {{ fn, ms }}); return id; }},
  clearTimer: id => timers.delete(id),
  onAction: (bubble, action) => actions.push({{ bubble, action }}),
  onFeedback: (bubble, reason) => feedback.push({{ id: bubble.bubble_id, reason }}),
}});
const bubble = {{ bubble_id: 'bubble-task-1', kind: 'task', text: '继续任务', action: {{ type: 'open_conversation', label: '打开对话', prompt: '继续任务', source_id: 'signal-1' }}, expires_at: 2000, duration_ms: 5000 }};
assert.equal(controller.present(bubble).reason, 'presented');
assert.equal(controller.snapshot().state, 'entering');
timers.get(1).fn(); timers.delete(1);
assert.equal(controller.snapshot().state, 'visible');
controller.activate();
assert.equal(actions.length, 1);
assert.equal(actions[0].action.type, 'open_conversation');
assert.equal(feedback[0].reason, 'accepted');
for (const [id, timer] of [...timers]) {{ timers.delete(id); timer.fn(); }}
for (const [id, timer] of [...timers]) {{ timers.delete(id); timer.fn(); }}
assert.ok(['cooldown', 'hidden'].includes(controller.snapshot().state));
assert.equal(controller.present(bubble).reason, 'duplicate');

const queued = new OpeningBubbleController({{ now: () => now, setTimer: (fn, ms) => {{ const id = nextId++; timers.set(id, {{ fn, ms }}); return id; }}, clearTimer: id => timers.delete(id) }});
queued.setContext({{ userTyping: true }});
assert.equal(queued.present({{ ...bubble, bubble_id: 'bubble-care-1', kind: 'care' }}).reason, 'queued_context');
assert.equal(queued.snapshot().state, 'hidden');
assert.equal(queued.snapshot().queue.length, 1);
"""
    completed = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=20, check=False)

    assert completed.returncode == 0, completed.stderr


def test_avatar_opening_bubble_has_reduced_motion_and_navigation_only_action() -> None:
    source = AVATAR_PATH.read_text(encoding="utf-8")
    action_start = source.index("function openOpeningBubbleAction")
    action_end = source.index("function renderOpeningBubble", action_start)
    action_source = source[action_start:action_end]

    assert "prefers-reduced-motion:reduce" in source
    assert 'data-avatar-motion="reduced"' in source
    assert 'data-avatar-motion="static"' in source
    assert "opening_bubble.present" in source
    assert "spiritkin.open_suggestion" in action_source
    assert "send(" not in action_source
    assert "postCommand" not in action_source
