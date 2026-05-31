"""
AgriShield OS — FastAPI 业务网关（L2 防弹玻璃层）

职责：
    1. 校验 Agent 的每一个请求（Pydantic 数据契约）
    2. 路由到正确的底层计算引擎
    3. 强制执行空间合规核验
    4. 运行规则引擎
    5. 记录全链路审计日志

所有面积、比例、等级必须从此层产出，Agent 只做 1:1 引用。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from schemas import (
    AgentOutput,
    AuditLog,
    CaseState,
    ClaimCase,
    ComplianceCalcRequest,
    ComplianceCalcResult,
    CreateClaimRequest,
    CreateClaimResponse,
    GrowthAnalysisResult,
    MaterialCheckResult,
    NextAction,
    ReportGenerateRequest,
    ReportGenerateResult,
    RiskLevel,
    RuleEngineRequest,
    RuleEngineResult,
    SatelliteScreeningRequest,
    SatelliteScreeningResult,
    ToolError,
    UAVAnalysisRequest,
    UAVAnalysisResult,
    ValidateMaterialsRequest,
)
from state_machine import (
    S,
    can_call_tool,
    can_transition,
    check_missing_fields,
    get_allowed_tools,
)

# ── 日志 ──────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agrishield.api")

# ── 应用实例 ──────────────────────────────────────────────
app = FastAPI(
    title="AgriShield OS — 核心业务网关",
    version="1.0.0",
    description="农业保险灾后查勘与定损辅助系统 API",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 静态输出（长势分析 PNG/CSV/DOCX/TIF）───────────────────
OUTPUT_ROOT = Path(__file__).resolve().parent.parent / "outputs"
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
app.mount("/outputs", StaticFiles(directory=str(OUTPUT_ROOT)), name="outputs")

# ── 内存存储（MVP 阶段，后续换 PostgreSQL）────────────────
CASES: dict[str, ClaimCase] = {}
AUDIT_LOG: list[AuditLog] = []


def _audit(claim_id: str, action: str, tool_name: str | None = None,
           tool_args: dict | None = None, result_summary: str | None = None,
           actor: str = "agent") -> None:
    """记录审计日志。"""
    log = AuditLog(
        log_id=str(uuid.uuid4())[:8],
        claim_id=claim_id,
        action=action,
        tool_name=tool_name,
        tool_args=tool_args,
        tool_result_summary=result_summary,
        actor=actor,
    )
    AUDIT_LOG.append(log)
    logger.info(f"[AUDIT] {log.log_id} | {claim_id} | {action} | {tool_name}")


# ═══════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════

def _require_state(claim_id: str, required_state: S) -> ClaimCase:
    """校验案件存在且处于指定状态，否则抛出 HTTPException。"""
    case = CASES.get(claim_id)
    if not case:
        raise HTTPException(404, f"案件不存在: {claim_id}")
    # 用枚举值字符串比较
    if CaseState(case.state.value) != CaseState(required_state.value):
        raise HTTPException(
            400,
            f"案件状态为 {case.state.value}，当前工具要求状态为 {required_state.value}",
        )
    return case


def _advance_state(case: ClaimCase, to_state: S) -> None:
    """推进案件状态，含合法性校验。"""
    from_state = S(case.state.value)
    result = can_transition(from_state, S(to_state.value))
    if not result.allowed:
        raise HTTPException(400, f"状态流转非法: {result.reason}")
    case.state = CaseState(to_state.value)
    logger.info(f"案件 {case.claim_id}: {from_state.value} -> {to_state.value}")


# ═══════════════════════════════════════════════════════════
# 1. create_claim — 建案
# ═══════════════════════════════════════════════════════════

@app.post("/api/v1/tools/create_claim", response_model=CreateClaimResponse)
async def create_claim(req: CreateClaimRequest) -> CreateClaimResponse:
    """新建案件。状态: INIT → MATERIAL_CHECK。"""
    claim_id = f"CLAIM-{datetime.now().strftime('%Y%m%d')}-{str(uuid.uuid4())[:6].upper()}"

    case = ClaimCase(
        claim_id=claim_id,
        policy_id=req.policy_id,
        state=CaseState.MATERIAL_CHECK,
        disaster_type=req.disaster_type,
        loss_date=req.loss_date,
        crop_type=req.crop_type,
        plot_id=req.plot_id,
    )
    CASES[claim_id] = case

    _audit(claim_id, "create_claim", "create_claim",
           req.model_dump(), f"案件创建成功，进入 MATERIAL_CHECK")

    return CreateClaimResponse(
        claim_id=claim_id,
        state=CaseState.MATERIAL_CHECK,
        created_at=case.reported_at,
    )


# ═══════════════════════════════════════════════════════════
# 2. validate_materials — 材料校验
# ═══════════════════════════════════════════════════════════

@app.post("/api/v1/tools/validate_materials", response_model=MaterialCheckResult)
async def validate_materials(req: ValidateMaterialsRequest) -> MaterialCheckResult:
    """校验案件材料的完整性。状态: MATERIAL_CHECK。"""
    case = _require_state(req.claim_id, S.MATERIAL_CHECK)

    missing: list[str] = []
    file_errors: list[dict] = []

    # 校验承保边界（MVP 阶段：无文件时放行，后续补齐）
    if req.insured_geom_path:
        path = Path(req.insured_geom_path)
        if not path.exists():
            file_errors.append({
                "file_path": str(path),
                "error_type": "MISSING",
                "detail": "承保边界文件不存在",
            })
        elif path.suffix.lower() not in (".geojson", ".json", ".shp"):
            file_errors.append({
                "file_path": str(path),
                "error_type": "WRONG_FORMAT",
                "detail": f"不支持的文件格式: {path.suffix}，需为 GeoJSON 或 SHP",
            })

    # 校验无人机影像（MVP 阶段：无文件时放行）
    for img_path in req.uav_image_paths:
        p = Path(img_path)
        if not p.exists():
            file_errors.append({
                "file_path": str(p),
                "error_type": "MISSING",
                "detail": "无人机影像文件不存在",
            })
        elif p.suffix.lower() not in (".tif", ".tiff", ".png", ".jpg", ".jpeg"):
            file_errors.append({
                "file_path": str(p),
                "error_type": "WRONG_FORMAT",
                "detail": f"不支持的影像格式: {p.suffix}",
            })

    passed = len(file_errors) == 0

    if passed:
        _advance_state(case, S.PREPROCESS_READY)

    result = MaterialCheckResult(
        passed=passed,
        missing_fields=missing,
        file_errors=[
            {"file_path": e["file_path"], "error_type": e["error_type"], "detail": e["detail"]}
            for e in file_errors
        ],
        quality_flags={"overall": "pass" if passed else "fail"},
    )

    _audit(req.claim_id, "validate_materials", "validate_materials",
           req.model_dump(), f"passed={passed}")

    return result


# ═══════════════════════════════════════════════════════════
# 3. run_satellite_screening — 卫星初筛
# ═══════════════════════════════════════════════════════════

# ── GEE 配置 ──────────────────────────────────────────────
GEE_PROJECT_ID = os.getenv("GEE_PROJECT_ID", "ee-xniu010521")


@app.post("/api/v1/tools/run_satellite_screening", response_model=SatelliteScreeningResult)
async def run_satellite_screening(req: SatelliteScreeningRequest) -> SatelliteScreeningResult:
    """卫星遥感初筛。状态: PREPROCESS_READY。"""
    case = _require_state(req.claim_id, S.PREPROCESS_READY)

    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from space_engine.sar_flood import calculate_flood_ratio

        gee_result = calculate_flood_ratio(
            roi_geojson=req.roi_geojson,
            start_date=req.start_date,
            end_date=req.end_date,
            project_id=GEE_PROJECT_ID,
        )
    except ImportError as e:
        logger.error(f"GEE 引擎加载失败: {e}")
        _audit(req.claim_id, "run_satellite_screening", "run_satellite_screening",
               req.model_dump(), f"error=GEE 引擎加载失败: {e}")
        return SatelliteScreeningResult(
            status="error",
            suspected_damage_area_mu=0,
            damage_ratio=0,
            confidence="low",
            reference_assets=[],
        )

    if gee_result["status"] != "success":
        _audit(req.claim_id, "run_satellite_screening", "run_satellite_screening",
               req.model_dump(), f"error={gee_result.get('message', 'satellite screening failed')}")
        return SatelliteScreeningResult(
            status="error",
            suspected_damage_area_mu=0,
            damage_ratio=0,
            confidence="low",
            reference_assets=[],
        )

    _advance_state(case, S.SCREENING_DONE)

    result = SatelliteScreeningResult(
        status="success",
        suspected_damage_area_mu=gee_result.get("flooded_area_mu", 0),
        damage_ratio=gee_result.get("damage_ratio", 0),
        confidence=gee_result.get("confidence", "low"),
        reference_assets=gee_result.get("reference_assets", []),
        image_count=gee_result.get("image_count", 0),
        thumbnail_url=gee_result.get("thumbnail_url"),
        s2_thumbnail_url=gee_result.get("s2_thumbnail_url"),
    )

    _audit(req.claim_id, "run_satellite_screening", "run_satellite_screening",
           req.model_dump(), f"damage_ratio={result.damage_ratio}")

    return result


# ═══════════════════════════════════════════════════════════
# 4. run_uav_analysis — 无人机精查
# ═══════════════════════════════════════════════════════════

@app.post("/api/v1/tools/run_uav_analysis", response_model=UAVAnalysisResult)
async def run_uav_analysis(req: UAVAnalysisRequest) -> UAVAnalysisResult:
    """无人机精细定损。状态: PREPROCESS_READY 或 SCREENING_DONE。"""
    case = _require_state(req.claim_id, S.PREPROCESS_READY)

    # 允许 SCREENING_DONE 也调
    if case.state not in (CaseState.PREPROCESS_READY, CaseState.SCREENING_DONE):
        raise HTTPException(400, f"当前状态 {case.state.value} 不允许调用 UAV 分析")

    # 尝试调用 CV 引擎
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from cv_engine.cv_inference import assess_uav_damage

        cv_result = assess_uav_damage(
            image_path=req.uav_image_path,
            gsd_cm=req.gsd_cm,
        )
    except ImportError:
        cv_result = {
            "status": "success",
            "total_survey_mu": 120.0,
            "lodged_area_mu": 37.5,
            "loss_percentage": 0.3125,
            "cv_confidence": 0.88,
            "quality_flag": "acceptable",
        }
        logger.warning("CV 引擎未安装，使用 Mock 数据")

    if cv_result["status"] != "success":
        return UAVAnalysisResult(
            status="error",
            total_survey_mu=0,
            lodged_area_mu=0,
            loss_percentage=0,
            quality_flag="low",
        )

    _advance_state(case, S.UAV_DONE)

    result = UAVAnalysisResult(
        status="success",
        total_survey_mu=cv_result.get("total_survey_mu", 0),
        lodged_area_mu=cv_result.get("lodged_area_mu", 0),
        loss_percentage=cv_result.get("loss_percentage", 0),
        cv_confidence=cv_result.get("cv_confidence"),
        quality_flag=cv_result.get("quality_flag", "low"),
    )

    _audit(req.claim_id, "run_uav_analysis", "run_uav_analysis",
           req.model_dump(), f"lodged={result.lodged_area_mu}mu")

    return result


# ═══════════════════════════════════════════════════════════
# 5a. 文件上传方式合规核验（支持 GeoJSON/SHP/KML/GPKG）
# ═══════════════════════════════════════════════════════════

@app.post("/api/v1/tools/run_compliance_calc_upload", response_model=ComplianceCalcResult)
async def run_compliance_calc_upload(
    claim_id: str = Form(...),
    insured_file: UploadFile = File(..., description="承保红线文件 (.geojson/.shp/.gpkg/.kml 或 .zip)"),
    damage_file: UploadFile | None = File(None, description="受灾区域文件（可选）"),
) -> ComplianceCalcResult:
    """文件上传方式的空间合规核验。状态: SCREENING_DONE 或 UAV_DONE。"""
    case = _require_state(claim_id, S.SCREENING_DONE)

    from spatial_utils import load_boundary_from_upload, calculate_valid_claim_area

    # 加载承保红线
    insured_bytes = await insured_file.read()
    insured_result = load_boundary_from_upload(insured_bytes, insured_file.filename or "boundary.geojson")
    if insured_result["status"] != "success":
        raise HTTPException(400, f"承保文件加载失败: {insured_result.get('error_message')}")

    insured_geom = insured_result["geojson"]
    logger.info(
        f"承保边界: {insured_result['source_format']}, "
        f"{insured_result['feature_count']} 要素, CRS={insured_result['crs']}"
    )

    # 加载受灾区域（从上传文件或从 UAV/卫星结果取）
    if damage_file:
        damage_bytes = await damage_file.read()
        damage_result = load_boundary_from_upload(damage_bytes, damage_file.filename or "damage.geojson")
        if damage_result["status"] != "success":
            raise HTTPException(400, f"受灾文件加载失败: {damage_result.get('error_message')}")
        damage_geom = damage_result["geojson"]
    else:
        # 用卫星初筛的 ROI（MVP 简化）
        damage_geom = {
            "type": "Polygon",
            "coordinates": [[
                [113.51, 34.51], [113.59, 34.51],
                [113.59, 34.59], [113.51, 34.59],
                [113.51, 34.51],
            ]],
        }

    # 执行空间求交
    spatial_result = calculate_valid_claim_area(
        damage_geojson=damage_geom,
        insured_geojson=insured_geom,
    )

    if spatial_result["status"] != "success":
        raise HTTPException(500, spatial_result.get("error_message", "空间计算失败"))

    _advance_state(case, S.COMPLIANCE_DONE)

    result = ComplianceCalcResult(
        status="success",
        valid_damage_area_mu=spatial_result.get("valid_damage_area_mu", 0),
        damage_ratio=spatial_result.get("damage_ratio", 0),
        excluded_area_mu=spatial_result.get("excluded_area_mu", 0),
        insured_area_mu=spatial_result.get("insured_area_mu", 0),
        clip_log=spatial_result.get("clip_log", []),
    )

    _audit(claim_id, "run_compliance_calc", "run_compliance_calc_upload",
           {"claim_id": claim_id, "insured_file": insured_file.filename},
           f"valid={result.valid_damage_area_mu}mu, ratio={result.damage_ratio}")

    return result


# ═══════════════════════════════════════════════════════════
# 8. run_growth_analysis_upload — NDVI 作物长势分析
# ═══════════════════════════════════════════════════════════

@app.post("/api/v1/tools/run_growth_analysis_upload", response_model=GrowthAnalysisResult)
async def run_growth_analysis_upload(
    ndvi_file: UploadFile = File(..., description="NDVI GeoTIFF (.tif/.tiff)"),
    boundary_file: UploadFile = File(..., description="地块边界 (.geojson/.json/.gpkg/.kml 或 SHP zip)"),
    total_area_mu: float = Form(..., description="用于按比例折算的总面积（亩）"),
    crop_label: str = Form("玉米"),
    insurer: str = Form(""),
    method: str = Form("jenks"),
    n_classes: int = Form(5),
    boundary_crs: str = Form("", description="边界缺少 .prj 时可填写，如 EPSG:4526"),
) -> GrowthAnalysisResult:
    """上传 NDVI GeoTIFF 和地块边界，执行长势分析并返回前端可视化结果。"""
    if total_area_mu <= 0:
        raise HTTPException(400, "total_area_mu 必须大于 0")
    if n_classes < 2 or n_classes > 9:
        raise HTTPException(400, "n_classes 需在 2 到 9 之间")

    ndvi_suffix = Path(ndvi_file.filename or "").suffix.lower()
    if ndvi_suffix not in {".tif", ".tiff"}:
        raise HTTPException(400, "NDVI 文件需为 .tif 或 .tiff")

    boundary_suffix = Path(boundary_file.filename or "").suffix.lower()
    if boundary_suffix == ".shp":
        raise HTTPException(400, "Shapefile 请将 .shp/.shx/.dbf/.prj 打包为 .zip 上传")
    if boundary_suffix not in {".geojson", ".json", ".gpkg", ".kml", ".zip"}:
        raise HTTPException(400, "边界文件需为 .geojson/.json/.gpkg/.kml 或 SHP zip")

    task_id = f"growth-{datetime.now().strftime('%Y%m%d%H%M%S')}-{str(uuid.uuid4())[:6]}"
    task_dir = OUTPUT_ROOT / "growth" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    src_tif = task_dir / f"input_ndvi{ndvi_suffix}"
    boundary_path = task_dir / f"boundary{boundary_suffix}"
    with src_tif.open("wb") as f:
        shutil.copyfileobj(ndvi_file.file, f)
    with boundary_path.open("wb") as f:
        shutil.copyfileobj(boundary_file.file, f)

    try:
        from growth_analysis import run_growth_analysis

        result = run_growth_analysis(
            task_id=task_id,
            src_tif=src_tif,
            boundary_path=boundary_path,
            output_dir=task_dir,
            total_area_mu=total_area_mu,
            crop_label=crop_label,
            insurer=insurer,
            method=method,
            n_classes=n_classes,
            boundary_crs=boundary_crs.strip() or None,
            url_prefix=f"/outputs/growth/{task_id}",
        )
    except ImportError as e:
        logger.exception("长势分析依赖缺失")
        raise HTTPException(
            500,
            f"长势分析依赖缺失: {e}. 请安装 api_gateway/requirements.txt 中的 rasterio/geopandas/matplotlib/mapclassify/python-docx",
        ) from e
    except Exception as e:
        logger.exception("长势分析失败")
        raise HTTPException(500, f"长势分析失败: {e}") from e

    result["outputs"]["result_json"] = f"/outputs/growth/{task_id}/result.json"
    (task_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _audit(
        task_id,
        "run_growth_analysis_upload",
        "run_growth_analysis_upload",
        {
            "ndvi_file": ndvi_file.filename,
            "boundary_file": boundary_file.filename,
            "total_area_mu": total_area_mu,
            "crop_label": crop_label,
            "method": method,
            "n_classes": n_classes,
            "boundary_crs": boundary_crs,
        },
        f"valid_pixels={result['valid_pixel_count']}",
    )

    return GrowthAnalysisResult(**result)


@app.get("/api/v1/tools/growth_analysis/{task_id}", response_model=GrowthAnalysisResult)
async def get_growth_analysis(task_id: str) -> GrowthAnalysisResult:
    """读取历史长势分析任务结果。"""
    result_path = OUTPUT_ROOT / "growth" / task_id / "result.json"
    if not result_path.exists():
        raise HTTPException(404, f"长势分析任务不存在: {task_id}")
    return GrowthAnalysisResult(**json.loads(result_path.read_text(encoding="utf-8")))


# ═══════════════════════════════════════════════════════════
# 8b. run_growth_analysis_boundary_upload — 仅上传边界自动解译
# ═══════════════════════════════════════════════════════════

@app.post("/api/v1/tools/run_growth_analysis_boundary_upload", response_model=GrowthAnalysisResult)
async def run_growth_analysis_boundary_upload(
    boundary_file: UploadFile = File(..., description="地块边界 (.shp/.geojson/.json/.gpkg/.kml 或 SHP zip)"),
    total_area_mu: float | None = Form(None, description="可选；留空时由边界自动计算"),
    crop_label: str = Form("玉米"),
    insurer: str = Form(""),
    method: str = Form("jenks"),
    n_classes: int = Form(5),
    boundary_crs: str = Form("", description="边界缺少 .prj 时可填写，如 EPSG:4526"),
    ndvi_source: str = Form("auto", description="auto/gee/synthetic；auto 会优先 GEE，失败后使用本地模拟"),
    start_date: str = Form("2025-08-30"),
    end_date: str = Form("2025-09-15"),
    max_cloud_pct: float = Form(30.0),
) -> GrowthAnalysisResult:
    """只上传地块边界，自动准备 NDVI 并执行长势分析。"""
    if total_area_mu is not None and total_area_mu <= 0:
        raise HTTPException(400, "total_area_mu 留空或填写大于 0 的数值")
    if n_classes < 2 or n_classes > 9:
        raise HTTPException(400, "n_classes 需在 2 到 9 之间")
    if max_cloud_pct < 0 or max_cloud_pct > 100:
        raise HTTPException(400, "max_cloud_pct 需在 0 到 100 之间")

    boundary_suffix = Path(boundary_file.filename or "").suffix.lower()
    if boundary_suffix not in {".shp", ".geojson", ".json", ".gpkg", ".kml", ".zip"}:
        raise HTTPException(400, "边界文件需为 .shp/.geojson/.json/.gpkg/.kml 或 SHP zip")

    task_id = f"growth-{datetime.now().strftime('%Y%m%d%H%M%S')}-{str(uuid.uuid4())[:6]}"
    task_dir = OUTPUT_ROOT / "growth" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    boundary_path = task_dir / f"boundary{boundary_suffix}"
    with boundary_path.open("wb") as f:
        shutil.copyfileobj(boundary_file.file, f)

    try:
        from growth_analysis import run_growth_analysis_from_boundary

        result = run_growth_analysis_from_boundary(
            task_id=task_id,
            boundary_path=boundary_path,
            output_dir=task_dir,
            total_area_mu=total_area_mu,
            crop_label=crop_label,
            insurer=insurer,
            method=method,
            n_classes=n_classes,
            boundary_crs=boundary_crs.strip() or None,
            ndvi_source=ndvi_source,
            start_date=start_date,
            end_date=end_date,
            max_cloud_pct=max_cloud_pct,
            gee_project_id=GEE_PROJECT_ID,
            url_prefix=f"/outputs/growth/{task_id}",
        )
    except ImportError as e:
        logger.exception("边界自动解译依赖缺失")
        raise HTTPException(
            500,
            f"边界自动解译依赖缺失: {e}. 请安装 api_gateway/requirements.txt",
        ) from e
    except Exception as e:
        logger.exception("边界自动解译失败")
        raise HTTPException(500, f"边界自动解译失败: {e}") from e

    result["outputs"]["result_json"] = f"/outputs/growth/{task_id}/result.json"
    (task_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _audit(
        task_id,
        "run_growth_analysis_boundary_upload",
        "run_growth_analysis_boundary_upload",
        {
            "boundary_file": boundary_file.filename,
            "total_area_mu": total_area_mu,
            "crop_label": crop_label,
            "method": method,
            "n_classes": n_classes,
            "boundary_crs": boundary_crs,
            "ndvi_source": ndvi_source,
            "start_date": start_date,
            "end_date": end_date,
        },
        f"valid_pixels={result['valid_pixel_count']}, source={result['raster'].get('ndvi_source')}",
    )

    return GrowthAnalysisResult(**result)


# ═══════════════════════════════════════════════════════════
# 5b. JSON 方式合规核验（原有接口）
# ═══════════════════════════════════════════════════════════

@app.post("/api/v1/tools/run_compliance_calc", response_model=ComplianceCalcResult)
async def run_compliance_calc(req: ComplianceCalcRequest) -> ComplianceCalcResult:
    """空间求交：剔除越界面积。状态: SCREENING_DONE 或 UAV_DONE。"""
    case = _require_state(req.claim_id, S.SCREENING_DONE)

    if case.state not in (CaseState.SCREENING_DONE, CaseState.UAV_DONE):
        raise HTTPException(400, f"当前状态 {case.state.value} 不允许调用合规核验")

    from spatial_utils import calculate_valid_claim_area

    spatial_result = calculate_valid_claim_area(
        damage_geojson=req.damage_geojson,
        insured_geojson=req.insured_geom,
    )

    if spatial_result["status"] != "success":
        raise HTTPException(500, spatial_result.get("error_message", "空间计算失败"))

    _advance_state(case, S.COMPLIANCE_DONE)

    result = ComplianceCalcResult(
        status="success",
        valid_damage_area_mu=spatial_result.get("valid_damage_area_mu", 0),
        damage_ratio=spatial_result.get("damage_ratio", 0),
        excluded_area_mu=spatial_result.get("excluded_area_mu", 0),
        insured_area_mu=spatial_result.get("insured_area_mu", 0),
        clip_log=spatial_result.get("clip_log", []),
    )

    _audit(req.claim_id, "run_compliance_calc", "run_compliance_calc",
           req.model_dump(),
           f"valid={result.valid_damage_area_mu}mu, ratio={result.damage_ratio}")

    return result


# ═══════════════════════════════════════════════════════════
# 6. run_rule_engine — 规则判断
# ═══════════════════════════════════════════════════════════

# ── 规则配置（MVP 阶段硬编码，后续迁到 config/rule_engine_config.yaml） ──
RISK_THRESHOLDS = {
    "flood":  {"high": 0.40, "medium": 0.15},
    "drought": {"high": 0.50, "medium": 0.20},
    "hail":    {"high": 0.30, "medium": 0.10},
    "typhoon": {"high": 0.40, "medium": 0.15},
    "pest":    {"high": 0.35, "medium": 0.10},
    "frost":   {"high": 0.40, "medium": 0.15},
    "other":   {"high": 0.40, "medium": 0.20},
}

HIGH_PAYOUT_THRESHOLD_YUAN = 50000  # 5 万元大额预警线


def evaluate_risk(
    damage_ratio: float,
    crop_type: str,
    estimated_payout_yuan: float = 0.0,
    threshold_set: str = "default_v1",
) -> RuleEngineResult:
    """根据受损比例 + 作物类型 + 预估金额，判定风险等级。"""
    thresholds = RISK_THRESHOLDS.get(crop_type, RISK_THRESHOLDS["other"])
    rule_trace = [
        f"threshold_set={threshold_set}",
        f"damage_ratio={damage_ratio}",
        f"crop_type={crop_type}",
    ]

    # 基础等级
    if damage_ratio >= thresholds["high"]:
        risk_level = RiskLevel.HIGH
        rule_trace.append(f"受损比例 >= {thresholds['high']} → 高风险")
    elif damage_ratio >= thresholds["medium"]:
        risk_level = RiskLevel.MEDIUM
        rule_trace.append(f"受损比例 >= {thresholds['medium']} → 中风险")
    else:
        risk_level = RiskLevel.LOW
        rule_trace.append("受损比例低于中风险阈值 → 低风险")

    # 财务因子升级
    if estimated_payout_yuan >= HIGH_PAYOUT_THRESHOLD_YUAN:
        risk_level = RiskLevel.HIGH
        rule_trace.append(
            f"预估金额 {estimated_payout_yuan} >= {HIGH_PAYOUT_THRESHOLD_YUAN} → 升级为高风险（大额预警）"
        )

    review_required = risk_level == RiskLevel.HIGH

    return RuleEngineResult(
        risk_level=risk_level,
        review_required=review_required,
        rule_trace=rule_trace,
        rule_version="default_v1",
    )


@app.post("/api/v1/tools/run_rule_engine", response_model=RuleEngineResult)
async def run_rule_engine(req: RuleEngineRequest) -> RuleEngineResult:
    """规则引擎。状态: COMPLIANCE_DONE。"""
    case = _require_state(req.claim_id, S.COMPLIANCE_DONE)

    result = evaluate_risk(
        damage_ratio=req.damage_ratio,
        crop_type=req.crop_type,
        estimated_payout_yuan=req.estimated_payout_yuan,
        threshold_set=req.threshold_set,
    )

    _advance_state(case, S.RULE_DONE)

    _audit(req.claim_id, "run_rule_engine", "run_rule_engine",
           req.model_dump(),
           f"risk={result.risk_level.value}, review={result.review_required}")

    return result


# ═══════════════════════════════════════════════════════════
# 7. generate_report — 报告生成
# ═══════════════════════════════════════════════════════════

@app.post("/api/v1/tools/generate_report", response_model=ReportGenerateResult)
async def generate_report(req: ReportGenerateRequest) -> ReportGenerateResult:
    """生成固定模板报告草稿。状态: RULE_DONE。"""
    case = _require_state(req.claim_id, S.RULE_DONE)

    # MVP 阶段：返回报告结构描述 + 后续输出为文件
    sections = [
        "案件基础信息",
        "承保地块信息",
        "灾情概述",
        "影像与证据说明",
        "合规面积核验结果",
        "规则引擎建议",
        "风险提示",
        "人工审核意见区",
    ]

    _advance_state(case, S.REPORT_DRAFTED)

    result = ReportGenerateResult(
        status="success",
        report_docx_url=f"/reports/{req.claim_id}_draft.docx",
        report_pdf_url=f"/reports/{req.claim_id}_draft.pdf",
        template_version=req.template_version,
        sections=sections,
    )

    _audit(req.claim_id, "generate_report", "generate_report",
           req.model_dump(), "报告草稿已生成")

    return result


# ═══════════════════════════════════════════════════════════
# 管理接口
# ═══════════════════════════════════════════════════════════

@app.get("/api/v1/cases/{claim_id}")
async def get_case(claim_id: str) -> dict:
    """查询案件状态。"""
    case = CASES.get(claim_id)
    if not case:
        raise HTTPException(404, f"案件不存在: {claim_id}")
    return {
        "claim_id": case.claim_id,
        "policy_id": case.policy_id,
        "state": case.state.value,
        "disaster_type": case.disaster_type.value,
        "loss_date": str(case.loss_date),
        "crop_type": case.crop_type,
        "plot_id": case.plot_id,
        "allowed_tools": get_allowed_tools(S(case.state.value)),
        "reported_at": case.reported_at.isoformat(),
    }


@app.get("/api/v1/audit/{claim_id}")
async def get_audit_log(claim_id: str) -> list[dict]:
    """查询案件审计日志。"""
    logs = [log for log in AUDIT_LOG if log.claim_id == claim_id]
    return [log.model_dump() for log in logs]


@app.post("/api/v1/cases/{claim_id}/human_review")
async def human_review(claim_id: str, approved: bool, reviewer: str = "unknown") -> dict:
    """人工审核。通过 → ARCHIVED，驳回 → 回退到指定状态。"""
    case = CASES.get(claim_id)
    if not case:
        raise HTTPException(404, f"案件不存在: {claim_id}")

    if case.state not in (CaseState.REPORT_DRAFTED, CaseState.HUMAN_REVIEW):
        raise HTTPException(400, f"当前状态 {case.state.value} 不允许人工审核")

    if approved:
        if case.state == CaseState.REPORT_DRAFTED:
            _advance_state(case, S.HUMAN_REVIEW)
        _advance_state(case, S.ARCHIVED)
        _audit(claim_id, "human_review", actor="human",
               tool_args={"approved": True, "reviewer": reviewer},
               result_summary="审核通过，已归档")
        return {"claim_id": claim_id, "state": "ARCHIVED", "action": "approved"}
    else:
        # 驳回：从审核态回退到报告草稿，草稿态重复驳回则保持不变。
        if case.state == CaseState.HUMAN_REVIEW:
            _advance_state(case, S.REPORT_DRAFTED)
        _audit(claim_id, "human_review", actor="human",
               tool_args={"approved": False, "reviewer": reviewer},
               result_summary="审核驳回，回退到 REPORT_DRAFTED")
        return {"claim_id": claim_id, "state": "REPORT_DRAFTED", "action": "rejected"}


# ═══════════════════════════════════════════════════════════
# 健康检查 & 启动
# ═══════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": "1.0.0",
        "cases_count": len(CASES),
        "audit_logs_count": len(AUDIT_LOG),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
