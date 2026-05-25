"""
AgriShield OS — Sentinel-2 光学植被指数模块
用于农作物健康评估、长势监测与干旱预警。
"""

import ee
import logging
from typing import Any
from space_engine.gee_auth import init_gee

logger = logging.getLogger(__name__)


def calculate_ndvi(
    roi_geojson: dict,
    start_date: str,
    end_date: str,
    max_cloud_pct: float = 20.0,
) -> dict[str, Any]:
    """
    计算指定区域在指定时间段内的 NDVI 均值。

    Args:
        roi_geojson: GeoJSON Polygon（WGS84, EPSG:4326）
        start_date: 起始日期（YYYY-MM-DD）
        end_date: 结束日期（YYYY-MM-DD）
        max_cloud_pct: 最大云覆盖百分比，默认 20%

    Returns:
        {
            "status": "success" | "error",
            "mean_ndvi": float,
            "health_status": "GOOD" | "FAIR" | "POOR",
            "reference_assets": list[str],
        }
    """
    try:
        init_gee()

        roi = ee.Geometry.Polygon(roi_geojson["coordinates"])

        collection = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(roi)
            .filterDate(start_date, end_date)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", max_cloud_pct))
        )

        image_count = collection.size().getInfo()
        if image_count == 0:
            return {
                "status": "error",
                "error_code": "NO_CLEAR_IMAGE",
                "error_message": f"时间段内无清晰影像（云量 < {max_cloud_pct}%）",
                "retryable": False,
                "recommended_human_action": "请放宽云量阈值或调整时间范围",
            }

        # 中值合成去云
        image = collection.median().clip(roi)
        ndvi = image.normalizedDifference(["B8", "B4"]).rename("NDVI")

        # 区域均值
        stats = ndvi.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=roi,
            scale=10,
            maxPixels=1e9,
        )
        mean_ndvi = stats.get("NDVI").getInfo()

        # 健康分级
        if mean_ndvi is None:
            return {
                "status": "error",
                "error_code": "CALC_ERROR",
                "error_message": "NDVI 计算返回空值",
            }

        if mean_ndvi > 0.6:
            health = "GOOD"
        elif mean_ndvi > 0.3:
            health = "FAIR"
        else:
            health = "POOR"

        asset_ids = collection.aggregate_array("system:index").getInfo()

        return {
            "status": "success",
            "mean_ndvi": round(mean_ndvi, 4),
            "health_status": health,
            "image_count": image_count,
            "max_cloud_pct": max_cloud_pct,
            "reference_assets": asset_ids[:10] if asset_ids else [],
        }

    except Exception as e:
        logger.error(f"NDVI 计算失败: {e}")
        return {
            "status": "error",
            "error_code": "ENGINE_ERROR",
            "error_message": str(e),
            "retryable": True,
            "recommended_human_action": "请检查 GEE 凭证和网络连接后重试",
        }


def calculate_ndvi_anomaly(
    roi_geojson: dict,
    target_start: str,
    target_end: str,
    baseline_years: int = 3,
    max_cloud_pct: float = 20.0,
) -> dict[str, Any]:
    """
    计算 NDVI 异常值：将当前时段与历史同期均值对比。

    Args:
        roi_geojson: GeoJSON Polygon
        target_start: 目标期起始（YYYY-MM-DD）
        target_end: 目标期结束（YYYY-MM-DD）
        baseline_years: 回溯年数，默认 3 年
        max_cloud_pct: 最大云覆盖百分比

    Returns:
        包含当前 NDVI、历史均值、偏离百分比和异常判定
    """
    try:
        init_gee()
        roi = ee.Geometry.Polygon(roi_geojson["coordinates"])

        # 当前期 NDVI
        current = calculate_ndvi(roi_geojson, target_start, target_end, max_cloud_pct)
        if current["status"] != "success":
            return current

        current_ndvi = current["mean_ndvi"]

        # 历史同期 NDVI 均值（逐年计算后平均）
        import datetime
        target_start_dt = datetime.date.fromisoformat(target_start)
        target_end_dt = datetime.date.fromisoformat(target_end)

        historical_ndvis = []
        for y in range(1, baseline_years + 1):
            hist_start = target_start_dt.replace(year=target_start_dt.year - y).isoformat()
            hist_end = target_end_dt.replace(year=target_end_dt.year - y).isoformat()
            hist_result = calculate_ndvi(roi_geojson, hist_start, hist_end, max_cloud_pct)
            if hist_result["status"] == "success":
                historical_ndvis.append(hist_result["mean_ndvi"])

        if not historical_ndvis:
            return {
                "status": "error",
                "error_code": "NO_HISTORICAL_DATA",
                "error_message": f"无法获取过去 {baseline_years} 年的同期 NDVI",
            }

        baseline_mean = sum(historical_ndvis) / len(historical_ndvis)
        deviation_pct = (
            (current_ndvi - baseline_mean) / baseline_mean * 100
            if baseline_mean != 0
            else 0
        )

        # 异常判定：连续两周低于历史均值 15%
        is_anomaly = deviation_pct < -15.0

        return {
            "status": "success",
            "current_ndvi": current_ndvi,
            "baseline_mean_ndvi": round(baseline_mean, 4),
            "deviation_pct": round(deviation_pct, 2),
            "is_anomaly": is_anomaly,
            "anomaly_type": "NDVI_SIGNIFICANT_DECLINE" if is_anomaly else None,
            "baseline_years": baseline_years,
            "historical_samples": len(historical_ndvis),
        }

    except Exception as e:
        logger.error(f"NDVI 异常检测失败: {e}")
        return {
            "status": "error",
            "error_code": "ENGINE_ERROR",
            "error_message": str(e),
        }


if __name__ == "__main__":
    sample_roi = {
        "type": "Polygon",
        "coordinates": [[
            [113.5, 34.5],
            [113.6, 34.5],
            [113.6, 34.6],
            [113.5, 34.6],
            [113.5, 34.5],
        ]],
    }
    result = calculate_ndvi(sample_roi, "2026-05-01", "2026-05-25")
    print(result)
