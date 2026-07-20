from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
AVATAR_PATH = REPO_ROOT / "frontend" / "avatar_3d.html"


def test_avatar_module_script_parses_as_javascript() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    source = AVATAR_PATH.read_text(encoding="utf-8")
    marker = '<script type="module">'
    start = source.index(marker) + len(marker)
    script = source[start : source.index("</script>", start)]
    completed = subprocess.run(
        [node, "--input-type=module", "--check", "-"],
        input=script,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )

    assert completed.returncode == 0, json.dumps(
        {"stdout": completed.stdout, "stderr": completed.stderr}, ensure_ascii=False
    )


def test_avatar_responsive_camera_keeps_wide_margin_without_changing_mobile_limit() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    source = AVATAR_PATH.read_text(encoding="utf-8")
    start = source.index("function responsivePortraitProgress")
    end = source.index("function responsiveCameraTarget", start)
    responsive_source = source[start:end]
    script = f"""
const assert = require('assert');
const THREE = {{ MathUtils: {{ clamp: (value, min, max) => Math.max(min, Math.min(max, value)) }} }};
const cam = {{ aspect: 2 }};
{responsive_source}
assert.equal(responsivePortraitProgress(), 0);
assert.ok(Math.abs(responsiveCameraScale() - 1.32) < 1e-9);
assert.equal(responsiveModelScale(), 1);

cam.aspect = 0.24;
assert.equal(responsivePortraitProgress(), 1);
assert.ok(Math.abs(responsiveCameraScale() - 2.05) < 1e-9);
assert.ok(Math.abs(responsiveModelScale() - 0.56) < 1e-9);

cam.aspect = 390 / 844;
assert.ok(responsiveCameraScale() > 1.32 && responsiveCameraScale() < 2.05);
assert.ok(responsiveModelScale() > 0.56 && responsiveModelScale() < 1);
"""
    completed = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=20, check=False)

    assert completed.returncode == 0, json.dumps(
        {"stdout": completed.stdout, "stderr": completed.stderr}, ensure_ascii=False
    )


def test_avatar_idle_motion_respects_bounds_and_motion_preferences() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    source = AVATAR_PATH.read_text(encoding="utf-8")
    start = source.index("function idleLife(")
    end = source.index("function waveTargetsRole", start)
    idle_life_source = source[start:end]
    script = f"""
const assert = require('assert');
let currentConfig = {{ motion: {{ idle_life: true, idle_life_amp: 1 }} }};
const DEFAULT_CONFIG = currentConfig;
let motionPreference = 'full';
let pointerLook = {{ lastMoveAt: 5000, targetX: 0, targetY: 0, currentX: 0, currentY: 0 }};
{idle_life_source}
const sample = {{ active: false, fade: 0 }};
const idle = idleLife(5000, false, sample);
assert.ok(Math.abs(idle.x) <= 0.014 + 1e-9);
assert.ok(Math.abs(idle.z) <= 0.006 + 1e-9);
assert.ok(Math.abs(idle.y) <= 0.006 + 1e-9);
assert.ok(Math.abs(idle.yaw) <= 0.018 + 1e-9);
assert.ok(Math.abs(idle.roll) <= 0.006 + 1e-9);
assert.ok(Math.abs(idle.pitch) <= 0.004 + 1e-9);
assert.ok(Math.abs(idle.headYaw) <= 0.075 + 1e-9);
assert.ok(Math.abs(idle.headPitch) <= 0.054 + 1e-9);
assert.ok(Math.abs(idle.hipYaw) <= 0.02 + 1e-9);
assert.ok(Math.abs(idle.breathScale) <= 0.012 + 1e-9);
assert.ok(idle.walk >= 0 && idle.walk <= 0.16 + 1e-9);

pointerLook = {{ lastMoveAt: 5000, targetX: 0, targetY: 0, currentX: 0, currentY: 0 }};
const speaking = idleLife(5000, true, sample);
assert.ok(Math.abs(speaking.x) <= Math.abs(idle.x) * 0.321 + 1e-9);
assert.ok(Math.abs(speaking.yaw) <= Math.abs(idle.yaw) * 0.321 + 1e-9);

motionPreference = 'static';
const still = idleLife(5000, false, sample);
assert.deepEqual(still, {{ x: 0, z: 0, y: 0, yaw: 0, roll: 0, pitch: 0, headYaw: 0, headPitch: 0, hipYaw: 0, breathScale: 0, walk: 0 }});
"""
    completed = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=20, check=False)

    assert completed.returncode == 0, json.dumps({"stdout": completed.stdout, "stderr": completed.stderr}, ensure_ascii=False)


def test_avatar_expression_transition_uses_300ms_ease_and_static_shortcut() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    source = AVATAR_PATH.read_text(encoding="utf-8")
    start = source.index("const EXPRESSION_TRANSITION_MS")
    end = source.index("function applyExpression", start)
    transition_source = source[start:end]
    script = f"""
const assert = require('assert');
let motionPreference = 'full';
const values = {{ happy: 0, angry: 0, sad: 0, relaxed: 0, surprised: 0, neutral: 1 }};
const currentVRM = {{ expressionManager: {{ getValue: name => values[name] || 0, setValue: (name, value) => values[name] = value }} }};
const influences = [0, 1];
const morphMeshes = [{{ morphTargetDictionary: {{ happy_face: 0, neutral_face: 1 }}, morphTargetInfluences: influences }}];
function keywordList(map) {{ return Object.values(map || {{}}).flatMap(value => value.keywords || []); }}
function matches(value, keys) {{ return keys.some(key => value.includes(key)); }}
function vrmEmotionName(emotion) {{ return emotion; }}
{transition_source}
const map = {{ happy: {{ keywords: ['happy'], intensity: 1 }}, neutral: {{ keywords: ['neutral'], intensity: 1 }} }};
startExpressionTransition('happy', map.happy, map, 1000);
assert.equal(values.neutral, 1);
assert.equal(values.happy, 0);
assert.equal(updateExpressionTransition(1150), true);
assert.ok(values.happy > 0.93 && values.happy < 0.94);
assert.ok(values.neutral > 0.06 && values.neutral < 0.07);
assert.ok(influences[0] > 0.93 && influences[1] < 0.07);
assert.equal(updateExpressionTransition(1300), true);
assert.equal(values.happy, 1);
assert.equal(values.neutral, 0);
assert.equal(expressionTransition, null);

motionPreference = 'static';
values.happy = 0;
values.neutral = 1;
startExpressionTransition('happy', map.happy, map, 2000);
assert.equal(values.happy, 1);
assert.equal(values.neutral, 0);
assert.equal(expressionTransition, null);
"""
    completed = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=20, check=False)

    assert completed.returncode == 0, json.dumps({"stdout": completed.stdout, "stderr": completed.stderr}, ensure_ascii=False)


def test_avatar_theme_lighting_uses_distinct_day_and_night_profiles() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    source = AVATAR_PATH.read_text(encoding="utf-8")
    start = source.index("function resolvedSceneTheme")
    end = source.index("function applyThemeLighting", start)
    lighting_source = source[start:end]
    script = f"""
const assert = require('assert');
const document = {{ documentElement: {{ dataset: {{ theme: 'dark' }} }} }};
function matchMedia() {{ return {{ matches: false }}; }}
const DEFAULT_CONFIG = {{ lights: {{ hemisphere_intensity: 1.8, directional_intensity: 2.4 }} }};
{lighting_source}
const dark = themeLightProfile({{}}, 'dark');
const light = themeLightProfile({{}}, 'light');
assert.equal(dark.theme, 'dark');
assert.equal(dark.hemisphereIntensity, 1.8);
assert.equal(dark.directionalIntensity, 2.4);
assert.equal(light.theme, 'light');
assert.ok(light.hemisphereIntensity > dark.hemisphereIntensity);
assert.ok(light.directionalIntensity < dark.directionalIntensity);
assert.notEqual(light.groundColor, dark.groundColor);
"""
    completed = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=20, check=False)

    assert completed.returncode == 0, json.dumps({"stdout": completed.stdout, "stderr": completed.stderr}, ensure_ascii=False)


def test_avatar_blink_uses_bounded_random_intervals_and_respects_motion_state() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    source = AVATAR_PATH.read_text(encoding="utf-8")
    start = source.index("const BLINK_MIN_INTERVAL_MS")
    end = source.index("const DEFAULT_CONFIG", start)
    blink_source = source[start:end]
    script = f"""
const assert = require('assert');
let motionPreference = 'full';
{blink_source}

for (const sample of [0, 0.01, 0.25, 0.5, 0.75, 0.99, 1]) {{
  const interval = blinkIntervalMs(sample);
  assert.ok(interval >= 2000 && interval <= 6000, `interval out of range: ${{interval}}`);
}}

resetBlinkSchedule(1000, 0);
assert.equal(blinkRuntime.nextAt, 3000);
assert.equal(blinkAmount(2999, false, 0), 0);
assert.equal(blinkAmount(3000, false, 0), 0);
assert.ok(blinkAmount(3070, false, 0) > 0.99);
assert.equal(blinkAmount(3140, false, 1), 0);
assert.ok(blinkRuntime.nextAt >= 5140 && blinkRuntime.nextAt <= 9140);

resetBlinkSchedule(0, 0);
assert.equal(blinkAmount(2000, true, 0), 0);
assert.equal(blinkRuntime.startedAt, -1);
assert.equal(blinkRuntime.nextAt, 4000);

motionPreference = 'static';
assert.equal(blinkAmount(4000, false, 0.5), 0);
assert.equal(blinkRuntime.nextAt, 0);
assert.equal(blinkRuntime.startedAt, -1);
"""
    completed = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=20, check=False)

    assert completed.returncode == 0, json.dumps({"stdout": completed.stdout, "stderr": completed.stderr}, ensure_ascii=False)
