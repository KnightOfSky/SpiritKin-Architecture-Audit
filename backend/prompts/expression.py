"""Prompt for the local avatar expression (emotion/action) classifier."""

from __future__ import annotations

from string import Template

EXPRESSION_CLASSIFIER_PROMPT = Template(
    "You are an emotion/action classifier for a virtual avatar. "
    "Read the assistant reply below and pick the single best emotion and action.\n"
    "emotion must be one of: neutral, happy, thinking, confused, speechless, waiting, alert, error, listening.\n"
    "action must be one of: idle, nod, shake, wave_hand, walk, tap_chin, tilt_head, listen.\n"
    'Output ONLY compact JSON: {"emotion":"happy","action":"wave_hand"}\n\n'
    "Assistant reply: $text\n"
    "JSON:"
)
