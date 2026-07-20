"""机械臂高层动作意图：默认面向 OpenClaw 一类抓取执行器。"""

from __future__ import annotations

from backend.devices.openclaw import OpenClawArm


def move_arm_to(x: float, y: float, z: float, arm: OpenClawArm):
    return arm.move_to(x=x, y=y, z=z)


def move_arm_home(arm: OpenClawArm):
    return arm.home()


def open_gripper(arm: OpenClawArm):
    return arm.set_gripper(opened=True)


def close_gripper(arm: OpenClawArm):
    return arm.set_gripper(opened=False)
