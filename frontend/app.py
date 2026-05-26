"""
AgriShield OS — 空天地协同理赔交互沙盘 (Streamlit)
布局：左侧栏 完整手动操作面板 + Agent 对话，右侧可视化大屏
"""

import streamlit as st
import json, time, subprocess
from datetime import datetime, timedelta

st.set_page_config(page_title="AgriShield OS", layout="wide", initial_sidebar_state="expanded", page_icon="🛰️")

API_BASE = "http://localhost:8000/api/v1"
MIMO_API_KEY = "tp-clifn0nvccpqy89no5nxrmcjcmsc55qf0lxy5ubd4y0ge2wb"
MIMO_BASE_URL = "https://token-plan-cn.xiaomimimo.com/v1"
MIMO_MODEL = "mimo-v2.5"

# ═══════════════════════════════════════════════════════════
# Agent
# ═══════════════════════════════════════════════════════════
SYSTEM_PROMPT = """你是 AgriShield Agent，农业保险灾后查勘与定损辅助系统调度 Agent。

规则：
1. 只调用白名单工具，不得估算面积/比例/金额
2. 状态机：INIT→MATERIAL_CHECK→PREPROCESS_READY→SCREENING_DONE→COMPLIANCE_DONE→RULE_DONE→REPORT_DRAFTED→HUMAN_REVIEW→ARCHIVED
3. 缺失关键字段先追问
4. 数值必须原样引用工具结果并标注来源
5. 未经人工审核不得输出最终结论"""

TOOLS = [
    {"type":"function","function":{"name":"create_claim","description":"新建理赔案件","parameters":{"type":"object","properties":{"policy_id":{"type":"string"},"disaster_type":{"type":"string","enum":["flood","drought","hail","typhoon","pest","frost","other"]},"loss_date":{"type":"string"},"crop_type":{"type":"string"},"plot_id":{"type":"string"}},"required":["policy_id","disaster_type","loss_date","crop_type"]}}},
    {"type":"function","function":{"name":"validate_materials","description":"校验材料完整性","parameters":{"type":"object","properties":{"claim_id":{"type":"string"}},"required":["claim_id"]}}},
    {"type":"function","function":{"name":"run_satellite_screening","description":"卫星遥感初筛","parameters":{"type":"object","properties":{"claim_id":{"type":"string"},"lon_min":{"type":"number"},"lat_min":{"type":"number"},"lon_max":{"type":"number"},"lat_max":{"type":"number"},"start_date":{"type":"string"},"end_date":{"type":"string"}},"required":["claim_id","lon_min","lat_min","lon_max","lat_max","start_date","end_date"]}}},
    {"type":"function","function":{"name":"run_rule_engine","description":"规则判断输出风险等级","parameters":{"type":"object","properties":{"claim_id":{"type":"string"},"damage_ratio":{"type":"number","minimum":0,"maximum":1},"crop_type":{"type":"string"}},"required":["claim_id","damage_ratio","crop_type"]}}},
    {"type":"function","function":{"name":"generate_report","description":"生成报告草稿","parameters":{"type":"object","properties":{"claim_id":{"type":"string"}},"required":["claim_id"]}}},
]

def _curl(url, headers=None, body=None):
    try:
        cmd = ["curl.exe","-s","-X","POST",url,"-H","Content-Type: application/json"]
        if headers:
            for k,v in (headers or {}).items(): cmd += ["-H",f"{k}: {v}"]
        if body: cmd += ["-d",json.dumps(body,ensure_ascii=False)]
        cmd += ["--connect-timeout","30","--max-time","120"]
        r = subprocess.run(cmd, capture_output=True, timeout=130)
        return json.loads(r.stdout.decode("utf-8"))
    except: return {"status":"error"}

def api(path, data=None):
    return _curl(f"{API_BASE}{path}", body=data)

def exec_tool(name, args):
    if name == "run_satellite_screening":
        roi = {"type":"Polygon","coordinates":[[[args["lon_min"],args["lat_min"]],[args["lon_max"],args["lat_min"]],[args["lon_max"],args["lat_max"]],[args["lon_min"],args["lat_max"]],[args["lon_min"],args["lat_min"]]]]}
        return api("/tools/run_satellite_screening",{"claim_id":args["claim_id"],"roi_geojson":roi,"start_date":args["start_date"],"end_date":args["end_date"]})
    m = {"create_claim":"/tools/create_claim","validate_materials":"/tools/validate_materials","run_rule_engine":"/tools/run_rule_engine","generate_report":"/tools/generate_report"}
    return api(m[name], args)

def mimo_chat(msgs):
    return _curl(f"{MIMO_BASE_URL}/chat/completions",{"Authorization":f"Bearer {MIMO_API_KEY}"},{"model":MIMO_MODEL,"messages":msgs,"tools":TOOLS,"temperature":0})

def agent_run(user_input, conv):
    conv.append({"role":"user","content":user_input})
    for _ in range(5):
        r = mimo_chat(conv)
        if "choices" not in r: return f"⚠️ {r.get('error',str(r))}", conv
        msg = r["choices"][0]["message"]
        if msg.get("tool_calls"):
            conv.append(msg)
            for tc in msg["tool_calls"]:
                name = tc["function"]["name"]
                args = json.loads(tc["function"]["arguments"])
                tr = exec_tool(name, args)
                # sync state
                if name=="create_claim" and "claim_id" in tr:
                    st.session_state.current_case = tr["claim_id"]; st.session_state.case_state = tr.get("state","MATERIAL_CHECK"); st.session_state.case_data = args
                elif name=="validate_materials" and tr.get("passed"): st.session_state.case_state = "PREPROCESS_READY"
                elif name=="run_satellite_screening":
                    st.session_state.satellite_result = tr
                    if tr.get("status")=="success": st.session_state.case_state = "SCREENING_DONE"
                elif name=="run_rule_engine":
                    st.session_state.rule_result = tr
                    if tr.get("status")=="success": st.session_state.case_state = "RULE_DONE"
                elif name=="generate_report" and tr.get("status")=="success": st.session_state.case_state = "REPORT_DRAFTED"
                conv.append({"role":"tool","tool_call_id":tc["id"],"content":json.dumps(tr,ensure_ascii=False)})
            continue
        content = msg.get("content","")
        conv.append({"role":"assistant","content":content})
        return content, conv
    return "⚠️ 达到最大轮次", conv

# ═══════════════════════════════════════════════════════════
# Session State
# ═══════════════════════════════════════════════════════════
for k,v in {"chat_history":[],"chat_conv":[{"role":"system","content":SYSTEM_PROMPT}],"current_case":None,"case_state":"INIT","case_data":{},"satellite_result":None,"satellite_roi":None,"compliance_result":None,"rule_result":None}.items():
    if k not in st.session_state: st.session_state[k] = v

def badge(s):
    c = {"INIT":"badge-pending","MATERIAL_CHECK":"badge-active","PREPROCESS_READY":"badge-active","SCREENING_DONE":"badge-active","UAV_DONE":"badge-active","COMPLIANCE_DONE":"badge-active","RULE_DONE":"badge-active","REPORT_DRAFTED":"badge-warning","HUMAN_REVIEW":"badge-warning","ARCHIVED":"badge-success"}.get(s,"badge-pending")
    return f'<span class="state-badge {c}">{s}</span>'

st.markdown("""<style>
.stButton>button{border-radius:8px;font-weight:500;transition:all .2s}
.stButton>button:hover{transform:translateY(-1px);box-shadow:0 2px 8px rgba(0,0,0,.1)}
.state-badge{display:inline-block;padding:4px 12px;border-radius:20px;font-size:13px;font-weight:600}
.badge-active{background:#2D6A4F;color:white}.badge-pending{background:#E9ECEF;color:#6C757D}
.badge-warning{background:#FFF3CD;color:#856404}.badge-danger{background:#F8D7DA;color:#721C24}.badge-success{background:#D4EDDA;color:#155724}
</style>""",unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════
# Header
# ═══════════════════════════════════════════════════════════
c1,c2=st.columns([3,1])
with c1: st.markdown('<div style="display:flex;align-items:center;gap:12px"><span style="font-size:32px">🛰️</span><div><h1 style="margin:0;color:#1B4332">AgriShield OS</h1><p style="margin:0;color:#6C757D;font-size:14px">空天地协同 · 农业保险智能理赔调度系统</p></div></div>',unsafe_allow_html=True)
with c2:
    try: api_ok = "ok" in subprocess.run(["curl.exe","-s",f"{API_BASE}/../health"],capture_output=True,timeout=5).stdout.decode()
    except: api_ok = False
    cc = "#2D6A4F" if api_ok else "#C1121F"
    st.markdown(f'<div style="text-align:right;padding-top:8px"><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{cc};margin-right:6px"></span><span style="font-size:13px;color:{cc}">{"● 引擎在线" if api_ok else "○ 引擎离线"}</span></div>',unsafe_allow_html=True)
st.divider()

# ═══════════════════════════════════════════════════════════
# SIDEBAR
# ═══════════════════════════════════════════════════════════
with st.sidebar:
    # ── 1. 手动操作面板 ──
    if not st.session_state.current_case:
        st.markdown("##### 📋 新建理赔案件")
        with st.form("create_case"):
            policy_id = st.text_input("保单号", "POL-2026-001")
            c1, c2 = st.columns(2)
            disaster_type = c1.selectbox("灾害类型", ["flood","drought","hail","typhoon","pest","frost","other"])
            loss_date = c2.date_input("灾害日期", datetime(2020, 7, 15))
            c3, c4 = st.columns(2)
            crop_type = c3.text_input("作物类型", "rice")
            plot_id = c4.text_input("地块编号", "PLOT-008")
            if st.form_submit_button("🚀 创建案件", use_container_width=True):
                r = api("/tools/create_claim",{"policy_id":policy_id,"disaster_type":disaster_type,"loss_date":str(loss_date),"crop_type":crop_type,"plot_id":plot_id})
                if "claim_id" in r:
                    st.session_state.current_case = r["claim_id"]; st.session_state.case_state = r["state"]
                    st.session_state.case_data = {"policy_id":policy_id,"disaster_type":disaster_type,"loss_date":str(loss_date),"crop_type":crop_type,"plot_id":plot_id}
                    st.rerun()

    else:
        # 状态卡片 + 进度条
        st.markdown(f'<div style="background:#F8FAF6;border:1px solid #DDE5DB;border-radius:10px;padding:12px;margin:8px 0"><div style="font-size:12px;color:#6C757D">案件编号</div><div style="font-weight:600;font-family:monospace">{st.session_state.current_case}</div><div style="margin-top:8px">{badge(st.session_state.case_state)}</div></div>',unsafe_allow_html=True)
        states = ["INIT","MATERIAL_CHECK","PREPROCESS_READY","SCREENING_DONE","UAV_DONE","COMPLIANCE_DONE","RULE_DONE","REPORT_DRAFTED","HUMAN_REVIEW","ARCHIVED"]
        idx = states.index(st.session_state.case_state) if st.session_state.case_state in states else 0
        st.progress(idx/(len(states)-1), text=f"流程进度 {int(idx/(len(states)-1)*100)}%")

        # 手动按钮
        st.divider()
        st.caption("🛠️ 手动操作")
        s = st.session_state.case_state

        # 返回上一步
        if s not in ("INIT","ARCHIVED"):
            prev_idx = {"MATERIAL_CHECK":"INIT","PREPROCESS_READY":"MATERIAL_CHECK","SCREENING_DONE":"PREPROCESS_READY","UAV_DONE":"SCREENING_DONE","COMPLIANCE_DONE":"SCREENING_DONE","RULE_DONE":"COMPLIANCE_DONE","REPORT_DRAFTED":"RULE_DONE","HUMAN_REVIEW":"REPORT_DRAFTED"}
            prev = prev_idx.get(s)
            if prev and st.button(f"↩ 返回上一步 ({s} → {prev})", use_container_width=True, type="secondary"):
                st.session_state.case_state = prev; st.rerun()

        if s == "MATERIAL_CHECK":
            if st.button("📎 校验材料", use_container_width=True):
                r = api("/tools/validate_materials",{"claim_id":st.session_state.current_case})
                if r.get("passed"): st.session_state.case_state = "PREPROCESS_READY"
                st.rerun()

        elif s == "PREPROCESS_READY":
            bf = st.file_uploader("上传边界 GeoJSON", type=["geojson","json"], key="m_b")
            c1,c2 = st.columns(2)
            lon_min = c1.number_input("左经度", 113.0, format="%.4f"); lat_min = c1.number_input("下纬度", 34.0, format="%.4f")
            lon_max = c2.number_input("右经度", 113.2, format="%.4f"); lat_max = c2.number_input("上纬度", 34.2, format="%.4f")
            if st.button("🛰️ 卫星初筛", use_container_width=True):
                with st.spinner("GEE 分析中..."):
                    if bf:
                        roi = json.loads(bf.read())
                        if roi.get("type")=="FeatureCollection": roi = roi["features"][0]["geometry"]
                    else:
                        roi = {"type":"Polygon","coordinates":[[[lon_min,lat_min],[lon_max,lat_min],[lon_max,lat_max],[lon_min,lat_max],[lon_min,lat_min]]]}
                    ld = datetime.strptime(st.session_state.case_data.get("loss_date","2020-07-01"),"%Y-%m-%d")
                    sd = (ld-timedelta(days=10)).strftime("%Y-%m-%d")
                    ed = min(ld+timedelta(days=15),datetime.now()).strftime("%Y-%m-%d")
                    r = api("/tools/run_satellite_screening",{"claim_id":st.session_state.current_case,"roi_geojson":roi,"start_date":sd,"end_date":ed})
                    st.session_state.satellite_result = r; st.session_state.satellite_roi = roi
                    if r.get("status")=="success": st.session_state.case_state = "SCREENING_DONE"
                    st.rerun()

        elif s in ("SCREENING_DONE",):
            import requests as req
            ins = st.file_uploader("承保红线", type=["geojson","json","shp","gpkg","kml","zip"], key="m_ins")
            dmg = st.file_uploader("受灾区域(可选)", type=["geojson","json","shp","gpkg","kml","zip"], key="m_dmg")
            if st.button("⚖️ 合规核验", use_container_width=True):
                if ins:
                    files = {"insured_file":(ins.name,ins.getvalue())}; data = {"claim_id":st.session_state.current_case}
                    if dmg: files["damage_file"] = (dmg.name,dmg.getvalue())
                    r = req.post(f"{API_BASE}/tools/run_compliance_calc_upload",data=data,files=files,timeout=30).json()
                    st.session_state.compliance_result = r
                    if r.get("status")=="success": st.session_state.case_state = "COMPLIANCE_DONE"
                    st.rerun()

        elif s == "COMPLIANCE_DONE":
            if st.button("⚖️ 规则判断", use_container_width=True):
                sr = st.session_state.satellite_result or {}
                r = api("/tools/run_rule_engine",{"claim_id":st.session_state.current_case,"damage_ratio":sr.get("damage_ratio",0),"crop_type":st.session_state.case_data.get("crop_type","rice")})
                st.session_state.rule_result = r
                if r.get("status")=="success": st.session_state.case_state = "RULE_DONE"
                st.rerun()

        elif s == "RULE_DONE":
            if st.button("📄 生成报告草稿", use_container_width=True):
                api("/tools/generate_report",{"claim_id":st.session_state.current_case})
                st.session_state.case_state = "REPORT_DRAFTED"
                st.rerun()

        elif s == "REPORT_DRAFTED":
            c1,c2 = st.columns(2)
            if c1.button("✅ 审核通过", use_container_width=True):
                api(f"/cases/{st.session_state.current_case}/human_review?approved=true&reviewer=审核员")
                st.session_state.case_state = "ARCHIVED"; st.rerun()
            if c2.button("❌ 驳回", use_container_width=True):
                api(f"/cases/{st.session_state.current_case}/human_review?approved=false&reviewer=审核员"); st.rerun()

        elif s == "ARCHIVED":
            st.success("🎉 案件已归档")

    # ── 2. Agent 对话（始终可见） ──
    st.divider()

    # 快捷操作区：根据当前状态显示可用操作的入口
    if not st.session_state.current_case:
        with st.expander("⚡ 快捷创建案件", expanded=True):
            with st.form("quick_create"):
                c1, c2, c3 = st.columns(3)
                pid = c1.text_input("保单号", "POL-001", key="qc_pid")
                dt = c2.selectbox("灾害", ["flood","drought","hail","typhoon"], key="qc_dt")
                ct = c3.text_input("作物", "rice", key="qc_ct")
                c4, c5 = st.columns(2)
                ld = c4.date_input("灾害日期", datetime(2020,7,15), key="qc_ld")
                pl = c5.text_input("地块", "PLOT-008", key="qc_pl")
                col_a, col_b = st.columns([1,2])
                submitted = col_a.form_submit_button("🚀 一键建案", use_container_width=True)
                if submitted:
                    r = api("/tools/create_claim",{"policy_id":pid,"disaster_type":dt,"loss_date":str(ld),"crop_type":ct,"plot_id":pl})
                    if "claim_id" in r:
                        st.session_state.current_case = r["claim_id"]; st.session_state.case_state = r["state"]
                        st.session_state.case_data = {"policy_id":pid,"disaster_type":dt,"loss_date":str(ld),"crop_type":ct,"plot_id":pl}
                        st.rerun()
                col_b.caption("填完必填项，一键建案，后续用按钮或对话操作")

    st.caption("💬 Agent 对话（自然语言操作）")
    for msg in st.session_state.chat_history[-8:]:
        bg = "#E8F5E9" if msg["role"]=="assistant" else "#F0F4FF" if msg["role"]=="user" else "#FFF8E1"
        icon = "🤖" if msg["role"]=="assistant" else "👤" if msg["role"]=="user" else "🔧"
        st.markdown(f'<div style="background:{bg};border-radius:10px;padding:8px;margin:3px 0;font-size:13px"><strong>{icon}</strong> {msg["content"][:400]}</div>',unsafe_allow_html=True)
    ui = st.chat_input("输入指令，如「帮我查POL-001的水稻田是否被洪水淹了」...")
    if ui:
        st.session_state.chat_history.append({"role":"user","content":ui})
        with st.spinner("🤖 思考中..."):
            rep, st.session_state.chat_conv = agent_run(ui, st.session_state.chat_conv)
            st.session_state.chat_history.append({"role":"assistant","content":rep})
            st.rerun()
    if st.button("🔄 重置对话", use_container_width=True):
        st.session_state.chat_history = []; st.session_state.chat_conv = [{"role":"system","content":SYSTEM_PROMPT}]; st.rerun()

# ═══════════════════════════════════════════════════════════
# RIGHT PANEL
# ═══════════════════════════════════════════════════════════
if st.session_state.current_case:
    st.markdown("### 📊 证据链可视化")
    t1,t2,t3 = st.tabs(["🛰️ 卫星影像","⚖️ 合规核验与规则","📋 审计日志"])
    with t1:
        if st.session_state.satellite_result:
            sr = st.session_state.satellite_result
            v = st.radio("视图",["Sentinel-2 光学","SAR 雷达水体"],horizontal=True,key="sv")
            thumb = sr.get("s2_thumbnail_url") if v=="Sentinel-2 光学" else sr.get("thumbnail_url")
            if thumb: st.image(thumb, caption=v, use_container_width=True)
            else: st.info("暂无影像")
            c1,c2,c3 = st.columns(3)
            c1.metric("🌊 疑似受灾",f"{sr.get('suspected_damage_area_mu',0):.1f}亩")
            c2.metric("📐 受损比例",f"{sr.get('damage_ratio',0)*100:.1f}%")
            c3.metric("📸 影像数",sr.get("image_count",0))
        else: st.info("等待卫星初筛结果")
    with t2:
        if st.session_state.compliance_result:
            cr = st.session_state.compliance_result
            c1,c2,c3,c4 = st.columns(4)
            c1.metric("承保面积",f"{cr.get('insured_area_mu',0):.1f}亩")
            c2.metric("合规受灾",f"{cr.get('valid_damage_area_mu',0):.1f}亩")
            c3.metric("剔除越界",f"{cr.get('excluded_area_mu',0):.1f}亩")
            c4.metric("合规比例",f"{cr.get('damage_ratio',0)*100:.1f}%")
        if st.session_state.rule_result:
            rr = st.session_state.rule_result; risk = rr.get("risk_level","low")
            m,b = {"high":("🔴 高风险 — 必须人工复核","#F8D7DA"),"medium":("🟡 中风险 — 建议重点巡检","#FFF3CD"),"low":("🟢 低风险 — 持续监测","#D4EDDA")}.get(risk,("⚪ 未知","#EEE"))
            st.markdown(f'<div style="background:{b};border-radius:10px;padding:16px"><strong>{m}</strong></div>',unsafe_allow_html=True)
        if not st.session_state.compliance_result and not st.session_state.rule_result: st.info("等待合规核验和规则判断")
    with t3:
        if st.session_state.current_case:
            try:
                r = subprocess.run(["curl.exe","-s",f"{API_BASE}/audit/{st.session_state.current_case}"],capture_output=True,timeout=5)
                for log in reversed(json.loads(r.stdout.decode("utf-8"))[-20:]):
                    with st.expander(f"{log.get('created_at','')[:19]} | {log.get('action','')} | {log.get('actor','')}"): st.json(log)
            except: st.info("暂无审计记录")
else:
    st.markdown('<div style="text-align:center;padding:60px 20px"><div style="font-size:64px">🛰️</div><h2 style="color:#1B4332">欢迎使用 AgriShield OS</h2><p style="color:#6C757D">空天地协同感知 · 可信计算 · 智能体调度<br>左侧面板创建案件开始理赔流程<br>也可直接用对话操作</p></div>',unsafe_allow_html=True)
st.divider()
st.caption("AgriShield OS v1.0.0 | 模型负责理解 · 引擎负责计算 · 人类负责决策")
