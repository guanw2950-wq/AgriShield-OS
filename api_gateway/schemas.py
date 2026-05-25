"""
AgriShield OS — Pydantic 数据契约（防幻觉"防弹玻璃"）

所有 API 的输入输出都通过这里的 Schema 强制校验。
大模型传什么参数、引擎返回什么数据，都必须在这些框里。
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════
# 枚举
# ═══════════════════════════════════════════════════════════

class CaseState(str, Enum):
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


class NextAction(str, Enum):
    ASK_USER = "ASK_USER"
    CALL_TOOL = "CALL_TOOL"
    WAIT_REVIEW = "WAIT_REVIEW"
    GENERATE_REPORT = "GENERATE_REPORT"
    STOP = "STOP"


class DisasterType(str, Enum):
    FLOOD = "flood"
    DROUGHT = "drought"
    HAIL = "hail"
    TYPHOON = "typhoon"
    PEST = "pest"
    FROST = "frost"
    OTHER = "other"


class RiskLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ReviewStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


# ═══════════════════════════════════════════════════════════
# 1. 建案
# ═══════════════════════════════════════════════════════════

class CreateClaimRequest(BaseModel):
    """建案请求 —— 5 个关键字段缺一不可"""
    policy_id: str = Field(..., description="承保保单唯一 ID")
    disaster_type: DisasterType = Field(..., description="灾害类型")
    loss_date: date = Field(..., description="灾害发生日期")
    crop_type: str = Field(..., description="作物类型，如 rice, wheat, corn")
    plot_id: Optional[str] = Field(None, description="地块编号")
    insured_geom: Optional[dict] = Field(None, description="承保地块 GeoJSON")


class CreateClaimResponse(BaseModel):
    claim_id: str
    state: CaseState = CaseState.MATERIAL_CHECK
    created_at: datetime


# ═══════════════════════════════════════════════════════════
# 2. 材料校验
# ═══════════════════════════════════════════════════════════

class ValidateMaterialsRequest(BaseModel):
    claim_id: str = Field(..., description="案件编号")
    insured_geom_path: Optional[str] = Field(None, description="承保边界文件路径")
    uav_image_paths: list[str] = Field(default_factory=list, description="无人机影像路径列表")
    photo_paths: list[str] = Field(default_factory=list, description="现场照片路径列表")


class FileError(BaseModel):
    file_path: str
    error_type: str  # MISSING / UNREADABLE / WRONG_FORMAT / DATE_OUT_OF_RANGE
    detail: str


class MaterialCheckResult(BaseModel):
    passed: bool
    missing_fields: list[str] = Field(default_factory=list)
    file_errors: list[FileError] = Field(default_factory=list)
    quality_flags: dict[str, str] = Field(default_factory=dict)


# ═══════════════════════════════════════════════════════════
# 3. 卫星初筛
# ═══════════════════════════════════════════════════════════

class SatelliteScreeningRequest(BaseModel):
    claim_id: str = Field(..., description="案件编号")
    roi_geojson: dict = Field(..., description="兴趣区域 GeoJSON (EPSG:4326)")
    start_date: str = Field(..., description="起始日期 YYYY-MM-DD")
    end_date: str = Field(..., description="结束日期 YYYY-MM-DD")


class SatelliteScreeningResult(BaseModel):
    status: str
    suspected_damage_area_mu: float = 0.0
    damage_ratio: float = 0.0
    confidence: str = "low"  # high / medium / low
    reference_assets: list[str] = Field(default_factory=list)
    image_count: int = 0
    thumbnail_url: str | None = None
    s2_thumbnail_url: str | None = None


# ═══════════════════════════════════════════════════════════
# 4. 无人机精查
# ═══════════════════════════════════════════════════════════

class UAVAnalysisRequest(BaseModel):
    claim_id: str = Field(..., description="案件编号")
    uav_image_path: str = Field(..., description="无人机 DOM 影像路径")
    gsd_cm: float = Field(5.0, description="地面采样距离 (cm/pixel)")


class UAVAnalysisResult(BaseModel):
    status: str
    total_survey_mu: float = 0.0
    lodged_area_mu: float = 0.0
    loss_percentage: float = 0.0
    cv_confidence: Optional[float] = None
    quality_flag: str = "low"  # acceptable / medium / low


# ═══════════════════════════════════════════════════════════
# 5. 合规面积核验
# ═══════════════════════════════════════════════════════════

class ComplianceCalcRequest(BaseModel):
    claim_id: str = Field(..., description="案件编号")
    damage_geojson: dict = Field(..., description="受灾多边形 GeoJSON（来自 CV 引擎）")
    insured_geom: dict = Field(..., description="承保红线 GeoJSON（来自保单数据库）")


class ComplianceCalcResult(BaseModel):
    status: str
    valid_damage_area_mu: float = 0.0       # 合规受灾面积（亩）
    damage_ratio: float = 0.0                # 合规受灾比例
    excluded_area_mu: float = 0.0            # 被剔除的越界面积（亩）
    insured_area_mu: float = 0.0             # 承保面积（亩）
    clip_log: list[str] = Field(default_factory=list)  # 剔除日志


# ═══════════════════════════════════════════════════════════
# 6. 规则引擎
# ═══════════════════════════════════════════════════════════

class RuleEngineRequest(BaseModel):
    claim_id: str
    damage_ratio: float = Field(..., ge=0.0, le=1.0, description="合规受灾比例")
    crop_type: str = Field(..., description="作物类型")
    threshold_set: str = Field("default_v1", description="规则集版本标识")
    estimated_payout_yuan: float = Field(0.0, description="预估赔付金额（元）")


class RuleEngineResult(BaseModel):
    risk_level: RiskLevel = RiskLevel.LOW
    review_required: bool = False
    rule_trace: list[str] = Field(default_factory=list)
    rule_version: str = "default_v1"


# ═══════════════════════════════════════════════════════════
# 7. 报告生成
# ═══════════════════════════════════════════════════════════

class ReportGenerateRequest(BaseModel):
    claim_id: str
    template_version: str = "v1.0"


class ReportGenerateResult(BaseModel):
    status: str
    report_docx_url: Optional[str] = None
    report_pdf_url: Optional[str] = None
    template_version: str = "v1.0"
    sections: list[str] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════
# Agent 统一输出壳
# ═══════════════════════════════════════════════════════════

class AgentOutput(BaseModel):
    """Agent 每次响应都必须套在这个结构里"""
    case_id: str = ""
    state: CaseState = CaseState.INIT
    missing_fields: list[str] = Field(default_factory=list)
    next_action: NextAction = NextAction.ASK_USER
    need_human_review: bool = False
    tool_name: Optional[str] = None
    tool_args: Optional[dict[str, Any]] = None
    result_summary: str = ""
    result_source: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    notes: str = ""


# ═══════════════════════════════════════════════════════════
# 错误返回
# ═══════════════════════════════════════════════════════════

class ToolError(BaseModel):
    error_code: str
    error_message: str
    retryable: bool = False
    recommended_human_action: str = ""


# ═══════════════════════════════════════════════════════════
# 数据模型（最小集合）
# ═══════════════════════════════════════════════════════════

class ClaimCase(BaseModel):
    """案件主表"""
    claim_id: str
    policy_id: str
    state: CaseState = CaseState.INIT
    disaster_type: DisasterType
    loss_date: date
    crop_type: str
    plot_id: Optional[str] = None
    reported_at: datetime = Field(default_factory=datetime.now)


class AuditLog(BaseModel):
    """审计日志"""
    log_id: str
    claim_id: str
    action: str
    tool_name: Optional[str] = None
    tool_args: Optional[dict] = None
    tool_result_summary: Optional[str] = None
    prompt_version: Optional[str] = None
    actor: str  # "agent" | "human"
    created_at: datetime = Field(default_factory=datetime.now)
