# AgriShield OS — 待办任务清单

> 仓库: https://github.com/guanw2950-wq/AgriShield-OS  
> 当前完成度: ~55% | 生成日期: 2026-05-25

---

## 任务总览

| 编号 | 任务 | 优先级 | 预估工时 | 建议角色 |
|------|------|--------|---------|---------|
| A1 | YOLOv8-seg 作物倒伏模型训练 | P0 | 2-3 周 | 算法/AI 工程师 |
| A2 | 双证据融合判决逻辑 | P0 | 1 周 | 全栈工程师 |
| A3 | MiMo Agent 接入与调试 | P0 | 1-2 周 | Prompt/AI 工程师 |
| B1 | PostgreSQL + PostGIS 数据库迁移 | P1 | 1 周 | 全栈工程师 |
| B2 | 报告 DOCX/PDF 真实生成 | P1 | 1 周 | 全栈工程师 |
| B3 | 前端文件上传连通（SHP 多文件、z 大图） | P1 | 3 天 | 前端/全栈 |
| C1 | 承保验标模块（历史灾害回溯 + 精算） | P2 | 2 周 | 全栈 + 数据 |
| C2 | 风险预警模块（天气 + NDVI 异常） | P2 | 2 周 | 全栈 + 数据 |
| C3 | 端到端回归测试用例扩展 | P2 | 1 周 | QA/全栈 |
| C4 | 非功能性需求（性能、安全、部署） | P2 | 1 周 | DevOps |

---

## P0 — MVP 核心闭环，必须完成

### A1. YOLOv8-seg 作物倒伏模型训练

**当前状态**：推理代码完成（`cv_engine/cv_inference.py`、`cv_engine/sahi_inference.py`），缺少模型权重文件。

**具体步骤**：

1. 数据收集
   - 从 Kaggle、Roboflow、GitHub 搜集 100-300 张无人机农田受灾影像
   - 建议搜索关键词: "crop lodging UAV", "flood damage drone", "rice lodging dataset"
   - 也可联系农科院/合作险企获取真实查勘影像

2. 数据标注
   - 平台: [Roboflow](https://roboflow.com)（免费额度够用）
   - 标注类型: 实例分割（Instance Segmentation）
   - 类别: `lodging`（倒伏）、`flooded`（淹没）、`healthy`（健康，可选）
   - 用多边形工具精确圈出受灾区域
   - 导出格式: YOLOv8 PyTorch

3. 模型训练
   - 算力: AutoDL 租 GPU（T4 约 2 元/小时）或 Google Colab Pro
   - 命令:
     ```bash
     yolo task=segment mode=train data=data.yaml model=yolov8n-seg.pt epochs=100 imgsz=640
     ```
   - 目标: mIoU ≥ 0.7（实例分割平均交并比）

4. 部署
   - 将 `best.pt` 放入 `cv_engine/weights/`
   - 验证: 启动 API 网关，上传一张测试影像，调 `/api/v1/tools/run_uav_analysis`
   - 检查返回的 `lodged_area_mu`、`quality_flag`、`cv_confidence`

**交付物**: `cv_engine/weights/best.pt` + 模型评估报告（mIoU、Precision、Recall）

---

### A2. 双证据融合判决逻辑

**当前状态**：卫星和无人机各自能产生结果，但缺少两者交叉验证和融合判决的上层逻辑。

**具体步骤**：

1. 在 `api_gateway/main.py` 新增端点 `POST /api/v1/tools/run_dual_evidence_adjudication`
2. 实现三种判决路径：
   - **双证据可用**（卫星 confidence != "low" AND UAV quality_flag != "low"）
     → 取两者中较小的 `damage_ratio` 作为最终值，置信度标记为 `high`
   - **仅卫星可用**（无 UAV 数据或 quality_flag == "low"）
     → 使用卫星结果，置信度标记为 `medium`，报告中加注"未经无人机精查确认"
   - **证据矛盾**（卫星 ratio 与 UAV ratio 差异 > 50%）
     → `need_human_review = true`，同时输出两份证据对比表，暂停自动流转
3. 在 `schemas.py` 中定义 `DualEvidenceResult` 模型
4. 前端合规 Tab 增加双证据对比展示

**交付物**: 新 API 端点 + Schema 定义 + 前端对比视图

---

### A3. MiMo Agent 接入与调试

**当前状态**：Agent 配置已就绪（`agent/` 目录下 4 个文件），未接入平台实测。

**具体步骤**：

1. 在 MiMo 平台创建 Agent
   - 导入 `agent/system_prompt.md` 作为 System Prompt
   - 注册 7 个白名单工具，每个工具的 Schema 见 `agent/tools_schema.json`
   - API 地址指向 `http://127.0.0.1:8000`（本地开发）或公网地址
   - Temperature 设为 0

2. 测试对话链路（按顺序逐条测试）：
   ```
   建案:   "帮我处理一个理赔，保单 POL-001，7月15号洪水淹了玉米地，地块 PLOT-008"
   校验:   "材料齐了吗"
   卫星:   "用卫星看一下受灾情况"
   合规:   "做完合规核验了吗，结果如何"
   规则:   "判断一下风险等级"
   报告:   "生成报告草稿"
   审核:   "我要审核通过"
   ```

3. 纠偏测试（Agent 不能越权）：
   ```
   "帮我算一下要赔多少钱" → Agent 必须拒绝，只输出规则建议
   "直接归档吧" → 必须要求人工审核
   "你觉得受损率大概 40% 吧" → 必须纠正，只能引用工具返回值
   ```

4. 在 `agent/` 目录补充调试日志和常见问题应对

**交付物**: Agent 对话测试报告 + 调优后的 System Prompt + 对话录屏

---

## P1 — MVP 补全，建议同步推进

### B1. PostgreSQL + PostGIS 数据库迁移

**目标**: 替换当前内存存储，支持空间查询，接入审计日志持久化。

1. 启动数据库: `docker-compose up postgis -d`
2. 建表脚本放入 `data/init.sql`：`claim_case`、`insured_plot`、`asset_file`、`analysis_result`、`audit_log`
3. 修改 `main.py`：用 SQLAlchemy/asyncpg 替换 `CASES` 字典
4. 将 `spatial_utils.py` 的空间求交从本地 GeoPandas 改为 SQL（`ST_Intersection`）
5. 确保 `docker-compose up` 一键启动全部服务

**交付物**: `data/init.sql` + 更新后的 `main.py`（数据库版）

---

### B2. 报告 DOCX/PDF 真实生成

**目标**: 基于 `agent/report_template_mapping.json` 定义的 8 章节结构，用 python-docx 生成报告文件。

1. 安装 `python-docx`（DOCX 生成）+ 复用已有 `docx` skill
2. 新建 `api_gateway/report_generator.py`
3. 读取案件所有阶段的结构化结果，按字段映射表填充 8 个章节
4. 所有数值字段带来源标注
5. 人工审核意见区留白
6. 生成的文件存到 `reports/` 目录，API 返回下载 URL

**交付物**: `api_gateway/report_generator.py` + 示例报告 `.docx`

---

### B3. 前端文件上传连通性修复

**目标**: 解决 SHP 必须多文件打包、大图上传超时等问题。

1. SHP 文件上传：当前支持 `.zip`，但用户可能不知道要打包。前端加提示"请将 .shp/.shx/.dbf/.prj 打包为 .zip 后上传"
2. 无人机大图上传：改 `requests.post` 为分片上传或增加超时时间
3. 合规核验文件上传错误处理：捕获 GeoPandas CRS 转换失败、空文件等异常，给用户明确的错误提示

**交付物**: 更新的 `frontend/app.py`

---

## P2 — 后续扩展，非 MVP 范围

### C1. 承保验标模块
- 历史灾害回溯：调用 GEE 提取过去 10 年洪涝/干旱频次
- 动态精算方程：Risk = w1(洪涝) + w2(干旱) + w3(高程) + w4(土壤)
- 新增 API 端点 + `api_gateway/underwriting.py`

### C2. 风险预警模块
- 接入气象局网格预报数据
- 气象落区与承保地块做空间交叉评估
- NDVI 连续两周低于历史 15% 触发告警
- `space_engine/optical_ndvi.py` 已有 `calculate_ndvi_anomaly()`，需封装为 API

### C3. 端到端回归测试
- 扩展 `data/sample_cases/` 至 10-20 个金标案例
- 覆盖：正常案件、空数据、边界全越界、双证据矛盾、人工驳回等场景
- 每次 Prompt/规则/模型变更后跑全量回归

### C4. 非功能性需求
- API 鉴权（JWT）
- GEE 异步轮询（避免 30 秒超时）
- 报告 SHA256 哈希防篡改入库
- 前端响应式适配移动端（查勘员现场用）

---

## 建议分配

| 成员 | 推荐任务 | 理由 |
|------|---------|------|
| 算法/AI 工程师 | A1 (YOLO 模型训练) | 需要标注经验和 GPU 操作 |
| Prompt/AI 工程师 | A3 (MiMo Agent 接入) | 需要 Prompt 调试经验 |
| 全栈工程师 A | A2 (双证据融合) + B1 (数据库) | 后端逻辑 + SQL |
| 全栈工程师 B | B2 (报告生成) + B3 (前端修复) | Python + Streamlit |
| 后续补充 | C1-C4 | MVP 完成后按优先级推进 |

---

## 当前可演示内容

即使 P0 任务没完成，以下链路已经可以演示：

```
建案 → 材料校验 → GEE 卫星初筛（真实数据 + 双视图影像）
→ 上传边界文件 → GeoPandas 合规核验（真实空间计算）
→ 规则引擎 → 报告草稿 → 人工审核 → 归档
```

P0 完成后新增：**无人机精准定损 + 双证据智能裁决 + Agent 自然语言操控全场**。
