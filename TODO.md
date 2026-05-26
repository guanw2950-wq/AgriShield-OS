# AgriShield OS — 待开发任务详细说明

> 仓库: https://github.com/guanw2950-wq/AgriShield-OS  
> 当前完成度: 约 60%  
> 可演示: 建案→GEE卫星初筛(真实数据)→GeoPandas合规核验(真实计算)→规则引擎→人工审核→归档

---

## 一、项目现状

目前系统跑通了 MVP 的核心闭环：用户（通过按钮或 Agent 对话）创建案件 → 卫星遥感分析 → 空间合规核验 → 规则判断 → 人工审核归档。Agent 通过 MiMo Function Calling 可以自动调度所有工具。

但以下模块尚未完成，需要各成员分工推进。

---

## 二、P0 — 必须完成（MVP 闭环）

### A1. YOLOv8-seg 作物倒伏/受灾检测模型训练

**为什么需要**：卫星只能宏观判断"这片区域大概淹了"，无人机高清影像才能精确到单株作物级别的定损。这是双证据机制的第二条腿。

**当前状态**：推理代码已就绪（`cv_engine/cv_inference.py`、`cv_engine/sahi_inference.py`），SAHI 大图切片逻辑也已写好。缺少训练好的 YOLO 权重文件 `cv_engine/weights/best.pt`。

**具体步骤**：

步骤1：收集数据
- 寻找 100-300 张无人机拍摄的农田受灾照片
- 来源：Kaggle、Roboflow Universe、GitHub
- 搜索关键词：crop lodging UAV, flood damage drone, rice lodging dataset
- 如果有合作险企的资源，直接用真实查勘影像最佳

步骤2：标注数据
- 平台：Roboflow（免费额度够用）
- 标注类型：实例分割（Instance Segmentation），用多边形精确圈出倒伏/绝收区域
- 标签类别建议：lodging（倒伏）、flooded（淹没）、healthy（健康，可选）
- 导出格式：YOLOv8 PyTorch

步骤3：训练模型
- 算力：AutoDL 租 GPU（T4 约 2 元/小时）或 Google Colab Pro
- 训练命令：
  ```bash
  yolo task=segment mode=train data=data.yaml model=yolov8n-seg.pt epochs=100 imgsz=640
  ```
- 验证指标：mIoU（平均交并比）≥ 0.7 即可部署

步骤4：部署验证
- 将 `best.pt` 放入 `cv_engine/weights/`
- 启动 API 网关，调用 `POST /api/v1/tools/run_uav_analysis`
- 确认返回的 `lodged_area_mu`、`quality_flag`、`cv_confidence` 符合预期

**涉及文件**：`cv_engine/cv_inference.py`（推理逻辑）、`cv_engine/sahi_inference.py`（大图切片）、`cv_engine/weights/`（放权重）

**交付物**：`cv_engine/weights/best.pt` + 模型评估报告（mIoU、Precision、Recall）

**预估工时**：2-3 周  
**建议角色**：算法/AI 工程师

---

### A2. 双证据融合判决逻辑

**为什么需要**：卫星和无人机各自产出结果后，需要一个上层逻辑判断用哪个、怎么融合。单独证据的可信度有限，两相印证才出最终结论。

**当前状态**：卫星初筛（`run_satellite_screening`）和无人机精查（`run_uav_analysis`）各自独立输出结果，没有交叉验证和融合判决。

**需要实现**：新增 API 端点 `POST /api/v1/tools/run_dual_evidence_adjudication`，实现三种判决路径：

路径一：双证据可用（卫星置信度不是 low，无人机 quality_flag 是 acceptable/medium）
→ 取两者中较小的 damage_ratio 作为最终值，标记为高置信度

路径二：仅卫星可用（无无人机数据或 quality_flag 为 low）
→ 使用卫星结果，标记为中置信度，报告中注明"未经无人机精查确认"

路径三：双证据矛盾（卫星 ratio 与无人机 ratio 差异 > 50%）
→ 强制 need_human_review = true，同时输出两份证据对比表，暂停自动流转

**涉及文件**：
- `api_gateway/main.py`：新增端点
- `api_gateway/schemas.py`：定义 DualEvidenceResult 数据模型
- `frontend/app.py`：合规核验 Tab 增加双证据对比展示

**交付物**：新 API 端点 + Schema + 前端对比视图

**预估工时**：1 周  
**建议角色**：全栈工程师

---

### A3. MiMo Agent 调优

**为什么需要**：当前 Agent 已能调度工具，但对话体验还需打磨。比如用户说"帮我查一下受灾"，Agent 应该引导用户补齐信息而不是只回复一段文字。

**当前状态**：Agent 已接入 MiMo Function Calling，基础对话可跑通。

**需要改进**：

1. 对话引导优化
   - 当用户输入信息不全时，Agent 应逐项引导而不要一次性列出所有缺失字段
   - 用语更自然："请提供保单号" 而不是输出 JSON 列表

2. 错误处理
   - 工具调用失败时，Agent 应给出明确的补救建议
   - 状态流转异常时友好提示

3. 对话+UI 联动
   - Agent 对话建案后，确保右侧可视化面板实时更新
   - Agent 完成卫星初筛后，自动提示用户查看右侧影像

**涉及文件**：
- `agent/system_prompt.md`：调优 Prompt
- `frontend/app.py`：对话与 UI 联动逻辑

**交付物**：优化后的 System Prompt + 对话流程测试报告

**预估工时**：3-5 天  
**建议角色**：Prompt/AI 工程师

---

## 三、P1 — 建议同步推进（提升产品完整度）

### B1. PostgreSQL + PostGIS 数据库迁移

**为什么需要**：当前所有数据存内存，重启就丢。生产环境必须有持久化存储，且 PostGIS 能直接在数据库层做空间查询（ST_Intersection），性能远优于 Python GeoPandas。

**当前状态**：Docker Compose 已有 PostGIS 配置，数据模型已在 `api_gateway/schemas.py` 中定义（ClaimCase、InsuredPlot、AssetFile、AnalysisResult、AuditLog）。

**具体步骤**：

1. 启动数据库：`docker-compose up postgis -d`
2. 编写建表 SQL：
   ```sql
   CREATE TABLE claim_case (
     claim_id VARCHAR(21) PRIMARY KEY,
     policy_id VARCHAR(50) NOT NULL,
     state VARCHAR(20) DEFAULT 'INIT',
     disaster_type VARCHAR(20),
     loss_date DATE,
     crop_type VARCHAR(50),
     plot_id VARCHAR(50),
     reported_at TIMESTAMP DEFAULT NOW()
   );
   CREATE TABLE insured_plot (
     plot_id VARCHAR(50) PRIMARY KEY,
     geom GEOMETRY(POLYGON, 4326),
     insured_area_mu FLOAT,
     crop_type VARCHAR(50),
     geom_version VARCHAR(20)
   );
   -- audit_log, asset_file, analysis_result 等
   ```
3. 改写 `main.py`：用 SQLAlchemy 替换内存字典 `CASES`
4. 将合规核验的空间求交改为 SQL：
   ```sql
   SELECT ST_Area(ST_Intersection(damage_geom, insured_geom)) * 0.0015 AS valid_mu
   ```
5. 确保 `docker-compose up` 一键启动全部服务

**涉及文件**：`api_gateway/main.py`、`data/init.sql`、`api_gateway/schemas.py`、`docker-compose.yml`

**交付物**：`data/init.sql` + 数据库版 `main.py`

**预估工时**：1 周  
**建议角色**：全栈工程师（需要 SQL + Python 经验）

---

### B2. 报告 DOCX/PDF 真实生成

**为什么需要**：当前"生成报告"只返回章节名称列表，没有生成实际文件。理赔报告是最重要的交付物。

**当前状态**：`agent/report_template_mapping.json` 已定义好 8 章报告结构和每个字段的来源映射。`api_gateway/schemas.py` 中有完整的字段定义。

**具体步骤**：

1. 新建 `api_gateway/report_generator.py`
2. 使用 `python-docx` 生成报告，包含：
   - 案件基础信息（案件号、保单号、灾害类型、日期、作物）
   - 承保地块信息（地块编号、投保面积、边界版本）
   - 灾情概述（卫星结论 + 无人机结论）
   - 影像与证据说明（影像编号列表、质量标记）
   - 合规面积核验结果（合规面积、受损比例、剔除面积、剔除日志）
   - 规则引擎建议（风险等级、规则追溯、规则版本）
   - 风险提示
   - 人工审核意见区（留白）
3. 所有数值字段必须标注来源（如 `来源：compliance_result.valid_damage_area`）
4. 生成的文件存入 `reports/` 目录，API 返回下载 URL

**涉及文件**：`api_gateway/report_generator.py`（新建）、`api_gateway/main.py`（修改 generate_report 端点）、`agent/report_template_mapping.json`（参考）

**交付物**：`api_gateway/report_generator.py` + 示例报告 .docx 文件

**预估工时**：1 周  
**建议角色**：全栈工程师（Python）

---

### B3. 前端细节优化

**为什么需要**：当前沙盘功能完整但交互细节有待打磨。

**具体改进项**：

1. 无人机大图上传：改 requests 为分片上传或增加超时时间
2. SHP 文件上传提示：用户可能不知道 SHP 需要打包为 zip，前端加引导提示
3. GeoPandas 错误处理：CRS 转换失败、空文件等异常给用户明确的错误提示
4. 审计日志展示优化：当前仅展示 JSON，增加可读性

**涉及文件**：`frontend/app.py`

**交付物**：优化后的前端代码

**预估工时**：3 天  
**建议角色**：前端/全栈工程师

---

## 四、P2 — 后续扩展（非 MVP 范围）

### C1. 承保验标模块
- 调用 GEE 提取过去 10 年洪涝/干旱频次
- 多因子精算方程：Risk = w1(洪涝) + w2(干旱) + w3(高程) + w4(土壤)
- 新增 API：`POST /api/v1/tools/risk_score`、`POST /api/v1/tools/premium_estimation`

### C2. 风险预警模块
- 接入气象局网格化预报数据
- 气象落区与承保地块空间交叉评估
- NDVI 连续两周低于历史均值 15% 自动触发告警
- `space_engine/optical_ndvi.py` 已有 `calculate_ndvi_anomaly()` 函数，需封装为 API

### C3. 测试体系
- 扩展 `data/sample_cases/` 至 10-20 个金标案例
- 覆盖场景：正常、空数据、边界全越界、双证据矛盾、人工驳回
- 每次 Prompt/规则/模型变更后跑全量回归

### C4. 生产环境部署
- API 鉴权（JWT）
- GEE 异步轮询（避免 60 秒超时）
- 报告 SHA256 哈希防篡改入库
- 前端响应式适配（查勘员移动端使用）

---

## 五、技术栈速览

| 层级 | 技术 | 说明 |
|------|------|------|
| 前端 | Streamlit | Python WebUI，当前可演示 |
| API | FastAPI + Pydantic | RESTful，7 个白名单工具 |
| 空间计算 | GeoPandas + Shapely | 多格式支持（GeoJSON/SHP/GPKG/KML） |
| 卫星数据 | Google Earth Engine | Sentinel-1/2，已认证 |
| CV 推理 | Ultralytics YOLOv8-seg + SAHI | 代码就绪，缺权重 |
| Agent 调度 | MiMo API（OpenAI 兼容） | Function Calling，已接入 |
| 数据库 | PostgreSQL + PostGIS | Docker 配置就绪，待迁移 |
| 规则引擎 | Python 自研 | 多因子阈值 + 大额预警 |
| 隧道 | ngrok | 本地 API 暴露公网，MiMo 可访问 |

---

## 六、建议分工

| 成员 | 推荐任务 | 启动前提 |
|------|---------|---------|
| 算法/AI | A1 — YOLO 模型训练 | 找数据集 + 标注 |
| Prompt/AI | A3 — Agent 调优 | 当前对话可跑通 |
| 全栈A | A2(双证据) + B1(数据库) | 熟悉 FastAPI + SQL |
| 全栈B | B2(报告生成) + B3(前端) | 熟悉 python-docx + Streamlit |

---

## 七、开发环境复现

```bash
# 1. 克隆仓库
git clone https://github.com/guanw2950-wq/AgriShield-OS.git
cd AgriShield-OS

# 2. 创建环境
conda create -n agrishield python=3.11 -y
conda activate agrishield
pip install fastapi uvicorn pydantic geopandas shapely streamlit earthengine-api --break-system-packages

# 3. GEE 认证（需要 GEE 账号 + GCP 项目）
earthengine authenticate

# 4. 启动
# 终端1: API 网关
cd api_gateway && python -m uvicorn main:app --reload --port 8000
# 终端2: 前端
cd frontend && streamlit run app.py
# 终端3: ngrok 隧道（Agent 需要）
ngrok http 8000
```
