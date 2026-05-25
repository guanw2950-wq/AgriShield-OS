"""
AgriShield OS — SAHI 切片辅助超级推理
解决高分辨率无人机影像（如 8000×8000）直接输入 640×640 YOLO 时的失真问题。

核心策略：
    1. 将大图切分为带重叠率的 640×640 小图
    2. 每张小图独立推理
    3. 通过 NMS 在原图坐标系下缝合结果
    4. 保障单株作物像素级面积计算精度
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def run_sahi_inference(
    image_path: str,
    weights_path: str | None = None,
    slice_height: int = 640,
    slice_width: int = 640,
    overlap_ratio: float = 0.2,
    conf_threshold: float = 0.45,
    gsd_cm: float = 5.0,
) -> dict[str, Any]:
    """使用 SAHI 对大尺寸无人机影像执行切片推理。

    Args:
        image_path:     影像文件路径
        weights_path:   权重路径
        slice_height:   切片高度 (px)，默认 640
        slice_width:    切片宽度 (px)，默认 640
        overlap_ratio:  切片间重叠比例，默认 0.2
        conf_threshold: 置信度阈值
        gsd_cm:         地面采样距离 (cm/pixel)

    Returns:
        与 assess_uav_damage 相同的输出结构，但附加切片元信息
    """
    try:
        from sahi import AutoDetectionModel
        from sahi.predict import get_prediction, get_sliced_prediction
        from sahi.utils.file import download_from_url
        import cv2
        import numpy as np
    except ImportError:
        return {
            "status": "error",
            "error_code": "SAHI_NOT_INSTALLED",
            "error_message": "SAHI 未安装，请执行: pip install sahi --break-system-packages",
            "retryable": False,
            "recommended_human_action": "安装 SAHI 依赖或使用 cv_inference.assess_uav_damage 进行标准推理",
        }

    if not weights_path:
        import os
        weights_path = os.path.join(os.path.dirname(__file__), "weights", "best.pt")

    try:
        # 构建 SAHI 检测模型
        detection_model = AutoDetectionModel.from_pretrained(
            model_type="yolov8",
            model_path=weights_path,
            confidence_threshold=conf_threshold,
        )

        # 切片推理
        result = get_sliced_prediction(
            image=image_path,
            detection_model=detection_model,
            slice_height=slice_height,
            slice_width=slice_width,
            overlap_height_ratio=overlap_ratio,
            overlap_width_ratio=overlap_ratio,
        )

        # 统计检测结果
        num_detections = len(result.object_prediction_list)

        logger.info(
            f"SAHI 切片推理完成: {num_detections} 个检测, "
            f"切片={slice_height}×{slice_width}, 重叠率={overlap_ratio}"
        )

        # 读取原图信息用于面积换算
        img = cv2.imread(image_path)
        img_height, img_width = img.shape[:2]
        gsd_m = gsd_cm / 100.0
        sqm_per_pixel = gsd_m * gsd_m
        total_area_mu = (img_width * img_height * sqm_per_pixel) * 0.0015

        return {
            "status": "success",
            "image_width_px": img_width,
            "image_height_px": img_height,
            "gsd_cm": gsd_cm,
            "total_survey_mu": round(total_area_mu, 2),
            "num_detections": num_detections,
            "slice_config": {
                "slice_height": slice_height,
                "slice_width": slice_width,
                "overlap_ratio": overlap_ratio,
            },
            "note": "面积换算需调用 cv_inference.assess_uav_damage 完成",
        }

    except Exception as e:
        logger.error(f"SAHI 推理失败: {e}")
        return {
            "status": "error",
            "error_code": "SAHI_ERROR",
            "error_message": str(e),
            "retryable": True,
            "recommended_human_action": "请降级使用标准推理 (cv_inference.assess_uav_damage) 或降低影像分辨率",
        }


if __name__ == "__main__":
    result = run_sahi_inference("sample_dom_large.tif")
    print(result)
