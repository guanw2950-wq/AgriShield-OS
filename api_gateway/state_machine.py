"""
AgriShield OS — 案件状态机

控制案件生命周期的合法流转。这是系统最核心的流程约束，
不是"建议"而是硬规则 —— 任何跳步都会被拒绝。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class S(str, Enum):
    """状态枚举"""
    INIT = "INIT"
    MATERIAL_CHECK = "MATERIAL_CHECK"
    PREPROCESS_READY = "PREPROCESS_READY"
    SCREENING_DONE = "SCREENING_DONE"
    UAV_DONE = "UAV_DONE"
    COMPLIANCE_DONE = "COMPLIANCE_DONE"
    RULE_DONE = "RULE_DONE"
    REPORT_DRAFTED = "REPORT_DRAFTED"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    ARCHIVED = "ARCHIVED"


# ── 合法流转关系（单向主链路） ─────────────────────────────
FORWARD_TRANSITIONS: dict[S, list[S]] = {
    S.INIT:              [S.MATERIAL_CHECK],
    S.MATERIAL_CHECK:    [S.PREPROCESS_READY],
    S.PREPROCESS_READY:  [S.SCREENING_DONE, S.UAV_DONE],
    S.SCREENING_DONE:    [S.UAV_DONE, S.COMPLIANCE_DONE],
    S.UAV_DONE:          [S.COMPLIANCE_DONE],
    S.COMPLIANCE_DONE:   [S.RULE_DONE],
    S.RULE_DONE:         [S.REPORT_DRAFTED, S.HUMAN_REVIEW],
    S.REPORT_DRAFTED:    [S.HUMAN_REVIEW],
    S.HUMAN_REVIEW:      [S.ARCHIVED],
    S.ARCHIVED:          [],
}

# ── 允许的回退（仅从 HUMAN_REVIEW 回退） ──────────────────
ROLLBACK_TRANSITIONS: dict[S, list[S]] = {
    S.HUMAN_REVIEW: [
        S.MATERIAL_CHECK,
        S.PREPROCESS_READY,
        S.SCREENING_DONE,
        S.UAV_DONE,
        S.COMPLIANCE_DONE,
        S.RULE_DONE,
        S.REPORT_DRAFTED,
    ],
}

# ── 每个状态允许调用的工具 ────────────────────────────────
STATE_TOOLS: dict[S, list[str]] = {
    S.INIT:              ["create_claim"],
    S.MATERIAL_CHECK:    ["validate_materials"],
    S.PREPROCESS_READY:  ["run_satellite_screening", "run_uav_analysis"],
    S.SCREENING_DONE:    ["run_uav_analysis", "run_compliance_calc"],
    S.UAV_DONE:          ["run_compliance_calc"],
    S.COMPLIANCE_DONE:   ["run_rule_engine"],
    S.RULE_DONE:         ["generate_report"],
    S.REPORT_DRAFTED:    [],
    S.HUMAN_REVIEW:      [],
    S.ARCHIVED:          [],
}

# ── 每个状态的退出条件（用自然语言描述，但实际校验靠业务逻辑） ──
EXIT_CONDITIONS: dict[S, list[str]] = {
    S.INIT: [
        "policy_id 已提供",
        "disaster_type 已提供",
        "loss_date 已提供",
        "crop_type 已提供",
        "plot_id 或 insured_geom 至少有一个",
    ],
    S.MATERIAL_CHECK: [
        "承保边界文件有效",
        "文件可读取",
        "必要影像可追溯",
        "没有阻断性缺失项",
    ],
    S.PREPROCESS_READY: [
        "可以执行卫星初筛或无人机精查",
    ],
    S.SCREENING_DONE: [
        "疑似受灾范围已生成",
        "已决定是否进入 UAV 精查或直接核验",
    ],
    S.UAV_DONE: [
        "quality_flag 为可接受",
        "已生成可用于核验的精查结果",
    ],
    S.COMPLIANCE_DONE: [
        "valid_damage_area 已生成",
        "damage_ratio 已生成",
        "clip_log 已生成",
    ],
    S.RULE_DONE: [
        "risk_level 已生成",
        "review_required 已生成",
        "rule_version 已生成",
    ],
    S.REPORT_DRAFTED: [
        "报告草稿已生成",
        "待审核清单已列出",
    ],
    S.HUMAN_REVIEW: [
        "审核通过 → ARCHIVED",
        "审核驳回 → 回退到指定状态",
    ],
    S.ARCHIVED: [],
}


@dataclass
class TransitionResult:
    """状态流转结果"""
    allowed: bool
    from_state: S
    to_state: S
    reason: str = ""


def can_transition(from_state: S, to_state: S) -> TransitionResult:
    """检查状态流转是否合法。

    支持正向流转和回退流转。
    """
    # 正向
    if to_state in FORWARD_TRANSITIONS.get(from_state, []):
        return TransitionResult(True, from_state, to_state, "正向流转")

    # 回退
    if from_state in ROLLBACK_TRANSITIONS:
        if to_state in ROLLBACK_TRANSITIONS[from_state]:
            return TransitionResult(True, from_state, to_state, "审核回退")

    return TransitionResult(
        False,
        from_state,
        to_state,
        f"不允许从 {from_state.value} 直接流转到 {to_state.value}",
    )


def can_call_tool(state: S, tool_name: str) -> bool:
    """检查在当前状态下是否允许调用指定工具。"""
    allowed_tools = STATE_TOOLS.get(state, [])
    return tool_name in allowed_tools


def get_next_states(state: S) -> list[S]:
    """获取当前状态允许进入的下一状态列表。"""
    forward = FORWARD_TRANSITIONS.get(state, [])
    rollback = ROLLBACK_TRANSITIONS.get(state, [])
    return forward + rollback


def get_allowed_tools(state: S) -> list[str]:
    """获取当前状态允许调用的工具列表。"""
    return STATE_TOOLS.get(state, [])


# ── 关键字段定义 ──────────────────────────────────────────
REQUIRED_FIELDS_FOR_INIT = [
    "policy_id",
    "disaster_type",
    "loss_date",
    "crop_type",
    # plot_id 或 insured_geom 至少一个
]


def check_missing_fields(case_data: dict) -> list[str]:
    """检查建案所需的 5 个关键字段是否缺失。

    plot_id 和 insured_geom 二选一即可。
    """
    missing = []
    for f in ["policy_id", "disaster_type", "loss_date", "crop_type"]:
        if not case_data.get(f):
            missing.append(f)

    has_location = case_data.get("plot_id") or case_data.get("insured_geom")
    if not has_location:
        missing.append("plot_id 或 insured_geom")

    return missing


# ── 禁止的跳步行为检测 ────────────────────────────────────
FORBIDDEN_SKIPS: list[tuple[S, S]] = [
    (S.INIT, S.SCREENING_DONE),
    (S.INIT, S.COMPLIANCE_DONE),
    (S.INIT, S.RULE_DONE),
    (S.INIT, S.REPORT_DRAFTED),
    (S.INIT, S.ARCHIVED),
    (S.MATERIAL_CHECK, S.RULE_DONE),
    (S.MATERIAL_CHECK, S.REPORT_DRAFTED),
    (S.MATERIAL_CHECK, S.ARCHIVED),
    (S.COMPLIANCE_DONE, S.ARCHIVED),
    (S.RULE_DONE, S.ARCHIVED),
    (S.REPORT_DRAFTED, S.ARCHIVED),
]


if __name__ == "__main__":
    # 自我检测
    print("=== 正向流转测试 ===")
    for test in [
        (S.INIT, S.MATERIAL_CHECK),
        (S.MATERIAL_CHECK, S.PREPROCESS_READY),
        (S.RULE_DONE, S.REPORT_DRAFTED),
        (S.INIT, S.SCREENING_DONE),  # 应拒绝
        (S.COMPLIANCE_DONE, S.ARCHIVED),  # 应拒绝
    ]:
        result = can_transition(*test)
        print(f"  {test[0].value} -> {test[1].value}: {'✅' if result.allowed else '❌'} {result.reason}")

    print("\n=== 工具权限测试 ===")
    for test in [
        (S.INIT, "create_claim"),
        (S.INIT, "run_rule_engine"),  # 应拒绝
        (S.COMPLIANCE_DONE, "run_rule_engine"),
        (S.RULE_DONE, "generate_report"),
    ]:
        ok = can_call_tool(*test)
        print(f"  {test[0].value} / {test[1]}: {'✅' if ok else '❌'}")
