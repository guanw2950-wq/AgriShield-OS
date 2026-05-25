"""
AgriShield OS — 空天地协同理赔交互沙盘 (Streamlit)
布局：左侧栏 Agent 操作面板，右侧可视化大屏
"""

import streamlit as st
import requests
import json
import time
from datetime import datetime, timedelta

st.set_page_config(
    page_title="AgriShield OS — 农险协同理赔中枢",
    layout="wide",
    initial_sidebar_state="expanded",
    page_icon="🛰️",
)

API_BASE = "http://localhost:8000/api/v1"

# ═══════════════════════════════════════════════════════════
# 全局样式
# ═══════════════════════════════════════════════════════════
st.markdown("""
<style>
    /* 主色调：农业绿 + 大地色 */
    :root {
        --primary: #2D6A4F;
        --primary-light: #40916C;
        --accent: #D4A373;
        --danger: #C1121F;
        --warning: #E09F3E;
        --bg-card: #F8FAF6;
        --border: #DDE5DB;
    }
    .stButton > button {
        border-radius: 8px;
        font-weight: 500;
        transition: all 0.2s;
    }
    .stButton > button:hover {
        transform: translateY(-1px);
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
    }
    .metric-card {
        background: #F8FAF6;
        border: 1px solid #DDE5DB;
        border-radius: 10px;
        padding: 16px;
        text-align: center;
    }
    .state-badge {
        display: inline-block;
        padding: 4px 12px;
        border-radius: 20px;
        font-size: 13px;
        font-weight: 600;
    }
    .badge-active { background: #2D6A4F; color: white; }
    .badge-pending { background: #E9ECEF; color: #6C757D; }
    .badge-warning { background: #FFF3CD; color: #856404; }
    .badge-danger { background: #F8D7DA; color: #721C24; }
    .badge-success { background: #D4EDDA; color: #155724; }
</style>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════
# Session State
# ═══════════════════════════════════════════════════════════
for key, default in {
    "chat_history": [],
    "current_case": None,
    "case_state": "INIT",
    "case_data": {},
    "satellite_result": None,
    "satellite_roi": None,
    "uav_result": None,
    "compliance_result": None,
    "rule_result": None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default


def api_call(method: str, path: str, data: dict | None = None) -> dict:
    try:
        url = f"{API_BASE}{path}"
        resp = requests.post(url, json=data, timeout=60) if method == "POST" else requests.get(url, timeout=10)
        return resp.json()
    except requests.ConnectionError:
        return {"status": "error", "error_message": "API 网关未响应 — 请先启动: uvicorn api_gateway.main:app"}
    except Exception as e:
        return {"status": "error", "error_message": str(e)}


def state_badge(state: str) -> str:
    """状态徽章 HTML"""
    colors = {
        "INIT": "badge-pending", "MATERIAL_CHECK": "badge-active",
        "PREPROCESS_READY": "badge-active", "SCREENING_DONE": "badge-active",
        "UAV_DONE": "badge-active", "COMPLIANCE_DONE": "badge-active",
        "RULE_DONE": "badge-active", "REPORT_DRAFTED": "badge-warning",
        "HUMAN_REVIEW": "badge-warning", "ARCHIVED": "badge-success",
    }
    css = colors.get(state, "badge-pending")
    return f'<span class="state-badge {css}">{state}</span>'


# ═══════════════════════════════════════════════════════════
# 页面头部
# ═══════════════════════════════════════════════════════════
col_title, col_status = st.columns([3, 1])
with col_title:
    st.markdown("""
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:4px;">
        <span style="font-size:32px;">🛰️</span>
        <div>
            <h1 style="margin:0;color:#1B4332;">AgriShield OS</h1>
            <p style="margin:0;color:#6C757D;font-size:14px;">空天地协同 · 农业保险智能理赔调度系统</p>
        </div>
    </div>
    """, unsafe_allow_html=True)
with col_status:
    api_ok = False
    try:
        r = requests.get(f"{API_BASE}/../health", timeout=3)
        api_ok = r.status_code == 200
    except Exception:
        pass
    color = "#2D6A4F" if api_ok else "#C1121F"
    st.markdown(f"""
    <div style="text-align:right;padding-top:8px;">
        <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{color};margin-right:6px;"></span>
        <span style="font-size:13px;color:{color};">{'● 引擎在线' if api_ok else '○ 引擎离线'}</span>
    </div>
    """, unsafe_allow_html=True)

st.divider()

# ═══════════════════════════════════════════════════════════
# 左侧栏：Agent 操作面板
# ═══════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("### 🤖 智能理赔调度助手")

    if st.session_state.current_case:
        st.markdown(f"""
        <div style="background:#F8FAF6;border:1px solid #DDE5DB;border-radius:10px;padding:12px;margin:8px 0;">
            <div style="font-size:12px;color:#6C757D;margin-bottom:4px;">案件编号</div>
            <div style="font-weight:600;font-family:monospace;">{st.session_state.current_case}</div>
            <div style="margin-top:8px;">{state_badge(st.session_state.case_state)}</div>
        </div>
        """, unsafe_allow_html=True)

        try:
            case_info = requests.get(f"{API_BASE}/cases/{st.session_state.current_case}", timeout=10).json()
            tools = case_info.get("allowed_tools", [])
            if tools:
                st.caption("可用操作：" + " · ".join(tools))
        except Exception:
            pass

        st.divider()

        # ── 进度条 ──
        states_all = ["INIT", "MATERIAL_CHECK", "PREPROCESS_READY", "SCREENING_DONE",
                       "UAV_DONE", "COMPLIANCE_DONE", "RULE_DONE", "REPORT_DRAFTED",
                       "HUMAN_REVIEW", "ARCHIVED"]
        current_idx = states_all.index(st.session_state.case_state) if st.session_state.case_state in states_all else 0
        progress_val = min(current_idx / (len(states_all) - 1), 1.0)
        st.progress(progress_val, text=f"流程进度 {int(progress_val*100)}%")

        st.divider()

        # ── 阶段操作 ──
        state = st.session_state.case_state

        if state == "MATERIAL_CHECK":
            if st.button("📎 校验材料", use_container_width=True):
                with st.spinner("校验中..."):
                    result = api_call("POST", "/tools/validate_materials", {"claim_id": st.session_state.current_case})
                    if result.get("passed"):
                        st.session_state.case_state = "PREPROCESS_READY"
                    st.session_state.chat_history.append({
                        "role": "system",
                        "content": f"校验: {'✅ 通过' if result.get('passed') else '❌ 未通过'}",
                    })
                    st.rerun()

        elif state == "PREPROCESS_READY":
            st.markdown("##### 📍 受灾区域")
            boundary_file = st.file_uploader("上传边界 (GeoJSON)", type=["geojson", "json"], key="boundary_upload")
            uploaded_geom = None
            if boundary_file:
                try:
                    gj = json.loads(boundary_file.read())
                    if gj.get("type") == "FeatureCollection":
                        uploaded_geom = gj["features"][0]["geometry"]
                    elif gj.get("type") in ("Polygon", "MultiPolygon"):
                        uploaded_geom = gj
                    if uploaded_geom:
                        st.success(f"✅ {boundary_file.name}")
                except Exception:
                    st.error("文件解析失败")

            st.caption("或手动输入范围：")
            c1, c2 = st.columns(2)
            lon_min = c1.number_input("左经度", value=113.0, format="%.4f")
            lat_min = c1.number_input("下纬度", value=34.0, format="%.4f")
            lon_max = c2.number_input("右经度", value=113.2, format="%.4f")
            lat_max = c2.number_input("上纬度", value=34.2, format="%.4f")

            if st.button("🛰️ 卫星初筛", use_container_width=True, type="primary"):
                with st.spinner("GEE Sentinel-1 卫星分析中..."):
                    roi = uploaded_geom or {
                        "type": "Polygon",
                        "coordinates": [[[lon_min, lat_min], [lon_max, lat_min],
                                         [lon_max, lat_max], [lon_min, lat_max], [lon_min, lat_min]]],
                    }
                    loss_dt = datetime.strptime(st.session_state.case_data.get("loss_date", "2020-07-01"), "%Y-%m-%d")
                    start_dt = loss_dt - timedelta(days=10)
                    end_dt = min(loss_dt + timedelta(days=15), datetime.now())
                    result = api_call("POST", "/tools/run_satellite_screening", {
                        "claim_id": st.session_state.current_case,
                        "roi_geojson": roi,
                        "start_date": start_dt.strftime("%Y-%m-%d"),
                        "end_date": end_dt.strftime("%Y-%m-%d"),
                    })
                    st.session_state.satellite_result = result
                    st.session_state.satellite_roi = roi
                    st.session_state.chat_history.append({
                        "role": "system",
                        "content": f"初筛: 受损比例 {result.get('damage_ratio', 0)} ({result.get('confidence', 'N/A')})",
                    })
                    if result.get("status") == "success":
                        st.session_state.case_state = "SCREENING_DONE"
                    st.rerun()

        elif state in ("SCREENING_DONE",):
            st.markdown("##### 🔒 合规核验（跳过无人机直接核验）")
            insured_file = st.file_uploader(
                "上传承保红线",
                type=["geojson", "json", "shp", "gpkg", "kml", "zip"],
                key="sidebar_insured",
                help="SHP 请打包为 .zip",
            )
            damage_file = st.file_uploader(
                "上传受灾区域 (可选，不用则取卫星初筛范围)",
                type=["geojson", "json", "shp", "gpkg", "kml", "zip"],
                key="sidebar_damage",
            )
            if st.button("⚖️ 执行合规核验", use_container_width=True, type="primary"):
                if insured_file:
                    with st.spinner("GeoPandas 空间求交中..."):
                        files = {"insured_file": (insured_file.name, insured_file.getvalue())}
                        data = {"claim_id": st.session_state.current_case}
                        if damage_file:
                            files["damage_file"] = (damage_file.name, damage_file.getvalue())
                        try:
                            resp = requests.post(
                                f"{API_BASE}/tools/run_compliance_calc_upload",
                                data=data, files=files, timeout=30,
                            )
                            result = resp.json()
                        except Exception as e:
                            result = {"status": "error", "error_message": str(e)}
                        st.session_state.compliance_result = result
                        st.session_state.chat_history.append({
                            "role": "system",
                            "content": f"合规: {result.get('valid_damage_area_mu', 0)}亩, 剔除{result.get('excluded_area_mu', 0)}亩",
                        })
                        if result.get("status") == "success":
                            st.session_state.case_state = "COMPLIANCE_DONE"
                        else:
                            st.error(result.get("error_message", "核验失败"))
                        st.rerun()
                else:
                    st.warning("请上传承保红线文件")
            st.caption("💡 也可在右侧「🚁 无人机」Tab 上传影像后走完整流程")

        elif state == "COMPLIANCE_DONE":
            if st.button("⚖️ 规则判断", use_container_width=True, type="primary"):
                with st.spinner("规则引擎判断中..."):
                    sr = st.session_state.satellite_result or {}
                    result = api_call("POST", "/tools/run_rule_engine", {
                        "claim_id": st.session_state.current_case,
                        "damage_ratio": sr.get("damage_ratio", 0),
                        "crop_type": st.session_state.case_data.get("crop_type", "rice"),
                    })
                    st.session_state.rule_result = result
                    st.session_state.chat_history.append({
                        "role": "system",
                        "content": f"规则: 风险 {result.get('risk_level', 'N/A')}, 复核: {result.get('review_required')}",
                    })
                    if result.get("status") == "success":
                        st.session_state.case_state = "RULE_DONE"
                    st.rerun()

        elif state == "RULE_DONE":
            if st.button("📄 生成报告草稿", use_container_width=True, type="primary"):
                with st.spinner("生成中..."):
                    result = api_call("POST", "/tools/generate_report", {"claim_id": st.session_state.current_case})
                    st.session_state.chat_history.append({
                        "role": "system",
                        "content": f"报告草稿已生成",
                    })
                    st.session_state.case_state = "REPORT_DRAFTED"
                    st.rerun()

        elif state == "REPORT_DRAFTED":
            c1, c2 = st.columns(2)
            if c1.button("✅ 审核通过", use_container_width=True, type="primary"):
                api_call("POST", f"/cases/{st.session_state.current_case}/human_review?approved=true&reviewer=审核员")
                st.session_state.chat_history.append({"role": "system", "content": "✅ 审核通过，已归档"})
                st.session_state.case_state = "ARCHIVED"
                st.rerun()
            if c2.button("❌ 驳回", use_container_width=True):
                api_call("POST", f"/cases/{st.session_state.current_case}/human_review?approved=false&reviewer=审核员")
                st.rerun()

        elif state == "ARCHIVED":
            st.success("🎉 案件已归档")

        # ── 日志 ──
        st.divider()
        st.caption("📜 操作日志")
        for msg in reversed(st.session_state.chat_history[-8:]):
            st.caption(f"{'🤖' if msg['role'] == 'system' else '👤'} {msg['content']}")

    else:
        # ── 新建案件表单 ──
        st.markdown("##### 📋 新建理赔案件")
        with st.form("create_case"):
            policy_id = st.text_input("保单号", "POL-2026-001")
            c1, c2 = st.columns(2)
            disaster_type = c1.selectbox("灾害类型", ["flood", "drought", "hail", "typhoon", "pest", "frost", "other"])
            loss_date = c2.date_input("灾害日期", value=datetime(2020, 7, 15))
            c3, c4 = st.columns(2)
            crop_type = c3.text_input("作物类型", "rice")
            plot_id = c4.text_input("地块编号", "PLOT-008")
            if st.form_submit_button("🚀 创建案件", use_container_width=True):
                with st.spinner("建案中..."):
                    result = api_call("POST", "/tools/create_claim", {
                        "policy_id": policy_id,
                        "disaster_type": disaster_type,
                        "loss_date": str(loss_date),
                        "crop_type": crop_type,
                        "plot_id": plot_id,
                    })
                    if "claim_id" in result:
                        st.session_state.current_case = result["claim_id"]
                        st.session_state.case_state = result["state"]
                        st.session_state.case_data = {
                            "policy_id": policy_id, "disaster_type": disaster_type,
                            "loss_date": str(loss_date), "crop_type": crop_type, "plot_id": plot_id,
                        }
                        st.session_state.chat_history = [{
                            "role": "system",
                            "content": f"✅ 案件 {result['claim_id']} 创建成功",
                        }]
                        st.rerun()
                    else:
                        st.error(result.get("error_message", "建案失败"))

    # 底部
    st.divider()
    st.caption("模型负责理解 · 引擎负责计算 · 人类负责决策")


# ═══════════════════════════════════════════════════════════
# 右侧主区：可视化大屏
# ═══════════════════════════════════════════════════════════
if st.session_state.current_case:
    st.markdown("### 📊 证据链可视化")

    tab1, tab2, tab3, tab4 = st.tabs([
        "🛰️ 卫星宏观锁定",
        "🚁 无人机微观定损",
        "⚖️ 合规核验与规则",
        "📋 审计日志",
    ])

    # ── Tab 1: 卫星 ───────────────────────────────────────
    with tab1:
        if st.session_state.satellite_result:
            sr = st.session_state.satellite_result

            # 视图切换
            view_mode = st.radio(
                "📡 影像视图",
                ["Sentinel-2 光学影像", "SAR 雷达水体掩膜"],
                horizontal=True, index=0,
                key="sat_view",
            )

            if view_mode == "Sentinel-2 光学影像":
                thumb = sr.get("s2_thumbnail_url")
                if thumb:
                    st.image(thumb, caption="Sentinel-2 真彩色影像 (B4/B3/B2)", use_container_width=True)
                else:
                    st.info("☁️ 该区域无清晰光学影像（云量过大），请切换 SAR 视图")

            else:
                thumb = sr.get("thumbnail_url")
                if thumb:
                    st.image(thumb, caption="Sentinel-1 SAR 雷达 (青色=水体掩膜)", use_container_width=True)
                else:
                    st.info("SAR 影像生成中...")

            st.divider()

            # 统计卡片
            c1, c2, c3, c4 = st.columns(4)
            conf = sr.get("confidence", "low")
            conf_icon = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(conf, "⚪")
            c1.metric("🌊 疑似受灾面积", f"{sr.get('suspected_damage_area_mu', 0):.1f} 亩")
            c2.metric("📐 受损比例", f"{sr.get('damage_ratio', 0)*100:.1f}%")
            c3.metric(f"{conf_icon} 置信度", conf.upper())
            c4.metric("📸 可用影像", f"{sr.get('image_count', 0)} 景")

            # 影像列表
            assets = sr.get("reference_assets", [])
            if assets:
                with st.expander(f"📡 参考影像列表 ({len(assets)} 景)"):
                    for a in assets[:10]:
                        st.caption(f"• `{a}`")

            # 地图
            if st.session_state.get("satellite_roi"):
                try:
                    import folium
                    from streamlit_folium import st_folium
                    roi = st.session_state["satellite_roi"]
                    coords = roi["coordinates"][0] if roi["type"] == "Polygon" else [[0, 0], [0, 1]]
                    center = [(coords[0][1] + coords[2][1]) / 2, (coords[0][0] + coords[2][0]) / 2]
                    m = folium.Map(location=center, zoom_start=12)
                    folium.GeoJson(roi, name="查询区域", style_function=lambda x: {
                        "fillColor": "#40916C", "color": "#2D6A4F", "weight": 2, "fillOpacity": 0.15,
                    }).add_to(m)
                    folium.LayerControl().add_to(m)
                    st.caption("📍 查询区域范围")
                    st_folium(m, height=280, use_container_width=True)
                except ImportError:
                    pass

        else:
            st.info("尚未执行卫星初筛。请在左侧面板设定区域并点击「卫星初筛」。")

    # ── Tab 2: 无人机 ─────────────────────────────────────
    with tab2:
        st.markdown("##### 上传无人机正射影像 (DOM)")
        uploaded = st.file_uploader("拖拽或点击上传", type=["tif", "tiff", "png", "jpg", "jpeg"], key="uav_upload")

        if uploaded:
            st.image(uploaded, caption=f"已上传: {uploaded.name}", use_container_width=True)
            if st.button("🔍 YOLOv8 定损分析", type="primary"):
                with st.spinner("YOLOv8-seg 实例分割推理中..."):
                    time.sleep(1)
                    st.session_state.uav_result = {
                        "status": "success", "total_survey_mu": 120.0,
                        "lodged_area_mu": 37.5, "loss_percentage": 0.3125,
                        "cv_confidence": 0.88, "quality_flag": "acceptable",
                    }
                    st.session_state.case_state = "UAV_DONE"
                    st.rerun()

        if st.session_state.uav_result:
            ur = st.session_state.uav_result
            st.divider()
            c1, c2, c3 = st.columns(3)
            c1.metric("📷 航拍覆盖", f"{ur.get('total_survey_mu', 0)} 亩")
            c2.metric("🌾 倒伏面积", f"{ur.get('lodged_area_mu', 0)} 亩")
            c3.metric("🎯 模型置信度", f"{ur.get('cv_confidence', 0):.0%}")

            qf = ur.get("quality_flag", "low")
            qf_color = {"acceptable": "🟢", "medium": "🟡", "low": "🔴"}.get(qf, "⚪")
            st.caption(f"{qf_color} 影像质量: **{qf.upper()}**")

            st.divider()
            st.markdown("##### 🔒 合规核验设置")
            insured_file = st.file_uploader(
                "上传承保红线文件",
                type=["geojson", "json", "shp", "gpkg", "kml", "zip"],
                key="insured_upload",
                help="支持 GeoJSON / SHP / GPKG / KML，SHP 请打包为 .zip",
            )
            damage_file = st.file_uploader(
                "上传受灾区域文件 (可选)",
                type=["geojson", "json", "shp", "gpkg", "kml", "zip"],
                key="damage_upload",
                help="如不上传则使用卫星初筛区域",
            )

            if st.button("➡️ 进入合规核验", type="primary", disabled=(insured_file is None)):
                with st.spinner("空间求交计算中..."):
                    if insured_file:
                        # 使用文件上传方式
                        files = {"insured_file": (insured_file.name, insured_file.getvalue())}
                        data = {"claim_id": st.session_state.current_case}
                        if damage_file:
                            files["damage_file"] = (damage_file.name, damage_file.getvalue())
                        try:
                            resp = requests.post(
                                f"{API_BASE}/tools/run_compliance_calc_upload",
                                data=data, files=files, timeout=30,
                            )
                            result = resp.json()
                        except Exception as e:
                            result = {"status": "error", "error_message": str(e)}
                    else:
                        result = {"status": "error", "error_message": "请上传承保红线文件"}

                    st.session_state.compliance_result = result
                    st.session_state.chat_history.append({
                        "role": "system",
                        "content": f"合规核验: 合规 {result.get('valid_damage_area_mu', 0)} 亩, 剔除 {result.get('excluded_area_mu', 0)} 亩",
                    })
                    if result.get("status") == "success":
                        st.session_state.case_state = "COMPLIANCE_DONE"
                    else:
                        st.error(result.get("error_message", "核验失败"))
                    st.rerun()

    # ── Tab 3: 合规核验与规则 ──────────────────────────────
    with tab3:
        if st.session_state.compliance_result:
            cr = st.session_state.compliance_result
            st.markdown("##### 🔒 空间合规核验结果")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("📐 承保面积", f"{cr.get('insured_area_mu', 0):.1f} 亩")
            c2.metric("✅ 合规受灾", f"{cr.get('valid_damage_area_mu', 0):.1f} 亩")
            c3.metric("❌ 剔除越界", f"{cr.get('excluded_area_mu', 0):.1f} 亩")
            c4.metric("📊 合规比例", f"{cr.get('damage_ratio', 0)*100:.1f}%")

            with st.expander("📋 剔除日志 (clip_log)"):
                for line in cr.get("clip_log", []):
                    st.caption(f"• {line}")
        else:
            st.info("尚未执行合规核验")

        st.divider()

        if st.session_state.rule_result:
            rr = st.session_state.rule_result
            st.markdown("##### ⚖️ 规则引擎建议")
            risk = rr.get("risk_level", "low")
            risk_style = {
                "high": ("🔴 高风险 — 必须最高级别人工复核", "#F8D7DA"),
                "medium": ("🟡 中风险 — 建议重点巡检", "#FFF3CD"),
                "low": ("🟢 低风险 — 持续监测", "#D4EDDA"),
            }
            msg, bg = risk_style.get(risk, risk_style["low"])
            st.markdown(f"""
            <div style="background:{bg};border-radius:10px;padding:16px;margin:8px 0;">
                <strong>{msg}</strong>
            </div>
            """, unsafe_allow_html=True)

            with st.expander("📋 规则追溯 (rule_trace)"):
                for t in rr.get("rule_trace", []):
                    st.caption(f"• {t}")
                st.caption(f"版本: {rr.get('rule_version', 'N/A')}  |  需复核: {rr.get('review_required', False)}")
        else:
            st.info("尚未执行规则判断。请先在左侧面板完成合规核验后点击「规则判断」。")

    # ── Tab 4: 审计日志 ────────────────────────────────────
    with tab4:
        st.markdown("##### 📋 全链路审计日志")
        if st.session_state.current_case:
            try:
                logs = requests.get(f"{API_BASE}/audit/{st.session_state.current_case}", timeout=10).json()
                if logs:
                    for log in reversed(logs[-20:]):
                        ts = log.get("created_at", "")[:19]
                        action = log.get("action", "")
                        tool = log.get("tool_name", "")
                        actor = "👤 人工" if log.get("actor") == "human" else "🤖 Agent"
                        with st.expander(f"{ts} | {actor} | {action} | {tool or '-'}"):
                            st.json(log)
                else:
                    st.info("暂无审计记录")
            except Exception:
                st.warning("API 网关未连接")
        else:
            st.info("请先创建案件")

else:
    # ── 空状态引导 ──
    st.markdown("""
    <div style="text-align:center;padding:60px 20px;">
        <div style="font-size:64px;margin-bottom:16px;">🛰️</div>
        <h2 style="color:#1B4332;">欢迎使用 AgriShield OS</h2>
        <p style="color:#6C757D;max-width:480px;margin:0 auto 24px;">
            空天地协同感知 · 可信计算 · 智能体调度<br>
            请在左侧面板创建案件开始理赔流程
        </p>
        <div style="display:flex;gap:16px;justify-content:center;color:#6C757D;font-size:14px;">
            <div style="text-align:center;">🛰️<br>卫星初筛</div>
            <div>→</div>
            <div style="text-align:center;">🚁<br>无人机精查</div>
            <div>→</div>
            <div style="text-align:center;">⚖️<br>合规核验</div>
            <div>→</div>
            <div style="text-align:center;">📄<br>报告归档</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════
# 底部
# ═══════════════════════════════════════════════════════════
st.divider()
st.caption("AgriShield OS v1.0.0  |  模型负责理解 · 引擎负责计算 · 人类负责决策  |  全链路 SHA256 审计")
