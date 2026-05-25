"""
AgriShield OS — GEE 鉴权模块
负责 Google Earth Engine 的初始化与认证。
"""

import ee
import os
import logging

logger = logging.getLogger(__name__)


def init_gee(project_id: str | None = None) -> bool:
    """
    初始化 Google Earth Engine。

    Args:
        project_id: GEE 项目 ID（可选，用于多项目隔离）

    Returns:
        bool: 初始化是否成功
    """
    try:
        if project_id:
            ee.Initialize(project=project_id)
        else:
            ee.Initialize()
        logger.info("GEE 初始化成功")
        return True
    except Exception:
        logger.warning("未找到 GEE 凭证，尝试重新认证...")
        try:
            ee.Authenticate()
            if project_id:
                ee.Initialize(project=project_id)
            else:
                ee.Initialize()
            logger.info("GEE 认证并初始化成功")
            return True
        except Exception as e:
            logger.error(f"GEE 初始化失败: {e}")
            return False


def get_roi_area_mu(roi_geojson: dict) -> float:
    """
    计算 ROI 区域的面积（亩）。

    Args:
        roi_geojson: GeoJSON 格式的 Polygon

    Returns:
        float: 面积（亩）
    """
    roi = ee.Geometry.Polygon(roi_geojson["coordinates"])
    area_sqm = roi.area().getInfo()
    return round(area_sqm * 0.0015, 2)


if __name__ == "__main__":
    init_gee()
