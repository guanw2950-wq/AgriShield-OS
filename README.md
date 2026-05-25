# AgriShield OS

空天地协同农业保险智能理赔调度系统

## 架构

```
L0 治理层: PostgreSQL + PostGIS + 审计日志
L1 调度层: MiMo Agent + 状态机
L2 中台层: FastAPI + Pydantic + GeoPandas
L3 引擎层: GEE (天基) + YOLOv8 (空基)
🖥️ 交互端: Streamlit
```

## 快速启动

```bash
# 1. 安装依赖
pip install -r requirements.txt --break-system-packages

# 2. 启动 API 网关
cd api_gateway && uvicorn main:app --reload --port 8000

# 3. 启动前端沙盘
cd frontend && streamlit run app.py
```

## 目录结构

| 目录 | 用途 |
|------|------|
| `space_engine/` | GEE 卫星遥感计算（SAR 洪涝 + NDVI 光学） |
| `cv_engine/` | 无人机 YOLOv8 视觉定损 |
| `api_gateway/` | FastAPI 业务中台（空间合规 + 规则引擎） |
| `agent/` | MiMo Agent 配置（Prompt + Tools + 状态机） |
| `frontend/` | Streamlit 交互沙盘 |
| `config/` | 规则配置 + 报告模板 |
| `tests/` | 回归测试用例 |

## 核心原则

- **模型负责理解，引擎负责计算，人类负责决策**
- 所有面积/比例必须由引擎产出，Agent 只做 1:1 引用
- 未经人工审核，任何案件不得归档
