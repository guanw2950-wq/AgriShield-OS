"""
AgriShield OS — 无人机视觉微观定损引擎
使用 YOLOv8-seg 实例分割模型，从无人机正射影像 (DOM) 中
提取倒伏/受灾作物区域，并换算为物理面积（亩）。
"""

import os
import logging
from typing import Any

import cv2
import numpy as np
from ultralytics import YOLO

logger = logging.getLogger(__name__)

# ── 默认权重路径 ─────────────────────────────────────────────
DEFAULT_WEIGHTS = os.path.join(os.path.dirname(__file__), "weights", "best.pt")


def load_model(weights_path: str | None = None) -> YOLO | None:
    """加载 YOLOv8-seg 模型。

    Args:
        weights_path: 权重文件路径，默认 cv_engine/weights/best.pt

    Returns:
        YOLO 模型实例，失败返回 None
    """
    path = weights_path or DEFAULT_WEIGHTS
    if not os.path.exists(path):
        logger.error(f"权重文件不存在: {path}")
        return None
    try:
        model = YOLO(path)
        logger.info(f"YOLO 模型加载成功: {path}")
        return model
    except Exception as e:
        logger.error(f"模型加载失败: {e}")
        return None


def assess_uav_damage(
    image_path: str,
    weights_path: str | None = None,
    gsd_cm: float = 5.0,
    conf_threshold: float = 0.45,
) -> dict[str, Any]:
    """对无人机正射影像执行倒伏/受灾区域检测，输出物理面积。

    核心流程：
        1. 加载 YOLOv8-seg 模型
        2. 对输入影像执行实例分割
        3. 将分割掩膜像素数 × 地面采样距离 (GSD) 换算为物理面积

    Args:
        image_path:   无人机 DOM 影像文件路径
        weights_path: 模型权重路径（默认 cv_engine/weights/best.pt）
        gsd_cm:       地面采样距离 (cm/pixel)，默认 5.0
        conf_threshold: 置信度阈值，默认 0.45

    Returns:
        {
            "status":            "success" | "error",
            "image_width_px":    int,
            "image_height_px":   int,
            "gsd_cm":            float,
            "total_survey_mu":   float,        # 影像覆盖总面积（亩）
            "lodged_area_mu":    float,         # 检测到的倒伏面积（亩）
            "loss_percentage":   float,         # 受损比例
            "num_detections":    int,           # 检测到的受灾斑块数
            "cv_confidence":     float | None,  # 模型平均置信度
            "recommendation":    str,
        }
    """
    # ── 1. 校验输入 ──────────────────────────────────────────
    if not os.path.exists(image_path):
        return {
            "status": "error",
            "error_code": "IMAGE_NOT_FOUND",
            "error_message": f"影像文件不存在: {image_path}",
            "retryable": False,
            "recommended_human_action": "请检查上传路径并重新上传",
        }

    # ── 2. 加载模型 ──────────────────────────────────────────
    model = load_model(weights_path)
    if model is None:
        return {
            "status": "error",
            "error_code": "MODEL_MISSING",
            "error_message": f"YOLO 权重文件缺失，已检查路径: {weights_path or DEFAULT_WEIGHTS}",
            "retryable": False,
            "recommended_human_action": "请确认模型已训练完毕并将 best.pt 放入 cv_engine/weights/",
        }

    # ── 3. 读取影像 ──────────────────────────────────────────
    try:
        img = cv2.imread(image_path)
        if img is None:
            return {
                "status": "error",
                "error_code": "IMAGE_UNREADABLE",
                "error_message": f"无法读取影像文件: {image_path}",
                "retryable": False,
                "recommended_human_action": "请确认文件格式正确（支持 tif/png/jpg）",
            }
        img_height, img_width = img.shape[:2]
    except Exception as e:
        return {
            "status": "error",
            "error_code": "IMAGE_LOAD_ERROR",
            "error_message": str(e),
            "retryable": False,
        }

    # ── 4. 面积基准计算 ─────────────────────────────────────
    gsd_m = gsd_cm / 100.0
    sqm_per_pixel = gsd_m * gsd_m
    total_area_mu = (img_width * img_height * sqm_per_pixel) * 0.0015

    # ── 5. 实例分割推理 ─────────────────────────────────────
    try:
        results = model.predict(source=img, save=False, conf=conf_threshold, verbose=False)
    except Exception as e:
        return {
            "status": "error",
            "error_code": "INFERENCE_ERROR",
            "error_message": f"模型推理失败: {e}",
            "retryable": True,
            "recommended_human_action": "请检查 GPU 显存或尝试降低影像分辨率",
        }

    if not results or results[0].masks is None:
        return {
            "status": "success",
            "image_width_px": img_width,
            "image_height_px": img_height,
            "gsd_cm": gsd_cm,
            "total_survey_mu": round(total_area_mu, 2),
            "lodged_area_mu": 0.0,
            "loss_percentage": 0.0,
            "num_detections": 0,
            "cv_confidence": None,
            "recommendation": "未检测到倒伏/受灾区域，建议人工复核影像质量或调整拍摄时段",
        }

    result = results[0]
    masks = result.masks.data.cpu().numpy()

    # ── 6. 像素统计与面积换算 ───────────────────────────────
    total_lodged_pixels = 0
    confidences = []

    for i in range(len(masks)):
        mask = masks[i]
        mask_resized = cv2.resize(
            mask, (img_width, img_height), interpolation=cv2.INTER_NEAREST
        )
        total_lodged_pixels += np.sum(mask_resized > 0)
        # 置信度（如果有 boxes）
        if result.boxes is not None and i < len(result.boxes.conf):
            confidences.append(float(result.boxes.conf[i]))

    lodged_area_mu = (total_lodged_pixels * sqm_per_pixel) * 0.0015
    loss_percentage = lodged_area_mu / total_area_mu if total_area_mu > 0 else 0.0
    avg_confidence = sum(confidences) / len(confidences) if confidences else None

    # ── 7. 质量判定 ─────────────────────────────────────────
    if avg_confidence is not None and avg_confidence < 0.6:
        quality_flag = "low"
    elif avg_confidence is not None and avg_confidence < 0.8:
        quality_flag = "medium"
    else:
        quality_flag = "acceptable"

    recommendation = "可进入合规核验" if quality_flag != "low" else "建议人工复核或重新采集影像"

    return {
        "status": "success",
        "image_width_px": img_width,
        "image_height_px": img_height,
        "gsd_cm": gsd_cm,
        "total_survey_mu": round(total_area_mu, 2),
        "lodged_area_mu": round(lodged_area_mu, 2),
        "loss_percentage": round(loss_percentage, 4),
        "num_detections": len(masks),
        "cv_confidence": round(avg_confidence, 4) if avg_confidence else None,
        "quality_flag": quality_flag,
        "recommendation": recommendation,
    }


def inference_to_geojson(
    image_path: str,
    weights_path: str | None = None,
    conf_threshold: float = 0.45,
    output_path: str | None = None,
) -> dict[str, Any]:
    """将 YOLO 分割结果转换为 GeoJSON 多边形。

    注意：此函数假设影像已做好地理配准（有 TFW/World File），
    实际使用需结合正射影像的 GeoTransform 进行像素→经纬度转换。

    Args:
        image_path:    影像路径
        weights_path:  模型权重路径
        conf_threshold: 置信度阈值
        output_path:   GeoJSON 输出路径（可选）

    Returns:
        {"status": "success", "geojson": {...}, "polygon_count": int}
    """
    # 先跑推理
    assess_result = assess_uav_damage(image_path, weights_path, conf_threshold=conf_threshold)
    if assess_result["status"] != "success":
        return assess_result

    # 此处为占位 —— 实际需结合 TFW 文件或 GCP 做地理配准
    # 返回的 GeoJSON 坐标系假定已映射到 EPSG:4326
    placeholder_geojson = {
        "type": "FeatureCollection",
        "features": [],
        "metadata": {
            "source": "cv_engine.inference_to_geojson",
            "note": "需要 TFW/World File 完成像素→经纬度转换",
            "lodged_area_mu": assess_result["lodged_area_mu"],
            "loss_percentage": assess_result["loss_percentage"],
        },
    }

    logger.warning(
        "inference_to_geojson 返回占位 GeoJSON —— 实际部署需结合 TFW 完成地理配准"
    )

    return {
        "status": "success",
        "geojson": placeholder_geojson,
        "lodged_area_mu": assess_result["lodged_area_mu"],
        "loss_percentage": assess_result["loss_percentage"],
        "polygon_count": assess_result["num_detections"],
    }


if __name__ == "__main__":
    import json
    # 示例：需要实际影像文件
    result = assess_uav_damage("sample_dom.tif")
    print(json.dumps(result, ensure_ascii=False, indent=2))
