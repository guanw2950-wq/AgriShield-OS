# AgriShield Agent 报告模板字段映射与状态机定义

本文档用于把 `AgriShield Agent` 的流程和报告输出完全钉死，减少自由发挥空间。

配套机器可读文件：

- [AgriShield_Agent_state_machine_and_report_mapping.json](C:\Users\hello\Documents\Playground\AgriShield_Agent_state_machine_and_report_mapping.json)

---

## 1. 为什么必须单独定义状态机和报告映射

如果没有单独的状态机和报告字段映射，Agent 最容易在两个地方失控：

1. 流程上跳步
2. 报告上自由润色或篡改口径

所以必须把下面两件事拆开固定：

1. `状态机`
2. `报告字段来源表`

---

## 2. 状态机定义

完整状态列表如下：

1. `INIT`
2. `MATERIAL_CHECK`
3. `PREPROCESS_READY`
4. `SCREENING_DONE`
5. `UAV_DONE`
6. `COMPLIANCE_DONE`
7. `RULE_DONE`
8. `REPORT_DRAFTED`
9. `HUMAN_REVIEW`
10. `ARCHIVED`

### 2.1 状态的业务含义

| 状态 | 含义 | Agent 能做什么 | Agent 不能做什么 |
|---|---|---|---|
| `INIT` | 原始案件输入阶段 | 检查关键字段、准备建案 | 不能做识别、核验、规则、报告 |
| `MATERIAL_CHECK` | 资料校验阶段 | 检查文件与边界 | 不能跳到分析阶段 |
| `PREPROCESS_READY` | 数据预处理完成 | 进入初筛或无人机精查 | 不能直接出结论 |
| `SCREENING_DONE` | 卫星初筛完成 | 决定是否继续精查或核验 | 不能当最终定损 |
| `UAV_DONE` | 无人机分析完成 | 准备合规核验 | 不能跳过质量判断 |
| `COMPLIANCE_DONE` | 合规面积核验完成 | 准备规则判断 | 不能自行解读赔付结论 |
| `RULE_DONE` | 规则建议完成 | 准备生成报告草稿或人工复核 | 不能自动归档 |
| `REPORT_DRAFTED` | 报告草稿完成 | 等待人工审核 | 不能视为最终报告 |
| `HUMAN_REVIEW` | 人工复核阶段 | 汇总待审核事项 | 不能替代审核人决定 |
| `ARCHIVED` | 结案归档 | 只允许查询 | 不能继续改关键结果 |

### 2.2 合法流转关系

主链路：

`INIT -> MATERIAL_CHECK -> PREPROCESS_READY -> SCREENING_DONE -> UAV_DONE(optional) -> COMPLIANCE_DONE -> RULE_DONE -> REPORT_DRAFTED -> HUMAN_REVIEW -> ARCHIVED`

允许的回退：

1. `HUMAN_REVIEW -> MATERIAL_CHECK`
2. `HUMAN_REVIEW -> PREPROCESS_READY`
3. `HUMAN_REVIEW -> SCREENING_DONE`
4. `HUMAN_REVIEW -> UAV_DONE`
5. `HUMAN_REVIEW -> COMPLIANCE_DONE`
6. `HUMAN_REVIEW -> RULE_DONE`
7. `HUMAN_REVIEW -> REPORT_DRAFTED`

不允许的行为：

1. `INIT -> SCREENING_DONE`
2. `MATERIAL_CHECK -> RULE_DONE`
3. `COMPLIANCE_DONE -> ARCHIVED`
4. `RULE_DONE -> ARCHIVED`
5. `REPORT_DRAFTED -> ARCHIVED`

一句话控制：

```text
没有人工审核通过，任何案件都不得进入 ARCHIVED。
```

---

## 3. 每个状态的进入条件与退出条件

### 3.1 `INIT`

进入条件：

1. 收到原始案件信息

退出条件：

1. `policy_id` 已有
2. `disaster_type` 已有
3. `loss_date` 已有
4. `crop_type` 已有
5. `plot_id` 或 `insured_geom` 至少有一个

### 3.2 `MATERIAL_CHECK`

进入条件：

1. 已成功建案

退出条件：

1. 承保边界文件有效
2. 文件可读取
3. 必要影像可追溯
4. 没有阻断性缺失项

### 3.3 `PREPROCESS_READY`

进入条件：

1. 材料校验通过

退出条件：

1. 可以执行卫星初筛或无人机精查

### 3.4 `SCREENING_DONE`

进入条件：

1. 卫星初筛成功返回

退出条件：

1. 疑似受灾范围已生成
2. 已决定是否进入 UAV 精查或直接核验

### 3.5 `UAV_DONE`

进入条件：

1. 无人机分析完成

退出条件：

1. `quality_flag` 为可接受
2. 已生成可用于核验的精查结果

### 3.6 `COMPLIANCE_DONE`

进入条件：

1. 已有受灾几何
2. 已有承保红线

退出条件：

1. `valid_damage_area` 已生成
2. `damage_ratio` 已生成
3. `clip_log` 已生成

### 3.7 `RULE_DONE`

进入条件：

1. 合规核验完成

退出条件：

1. `risk_level` 已生成
2. `review_required` 已生成
3. `rule_version` 已生成

### 3.8 `REPORT_DRAFTED`

进入条件：

1. 报告模板所需字段齐全

退出条件：

1. 报告草稿已生成
2. 待审核清单已列出

### 3.9 `HUMAN_REVIEW`

进入条件：

1. 草稿已生成或规则要求人工复核

退出条件：

1. 审核通过，进入 `ARCHIVED`
2. 审核驳回，回退到指定状态

### 3.10 `ARCHIVED`

进入条件：

1. 人工审核通过

退出条件：

1. 无

---

## 4. 报告模板固定结构

报告建议固定为以下 8 个章节：

1. 案件基础信息
2. 承保地块信息
3. 灾情概述
4. 影像与证据说明
5. 合规面积核验结果
6. 规则引擎建议
7. 风险提示
8. 人工审核意见区

任何 Agent 生成报告时，都不允许新增第 9 个“自由章节”。

---

## 5. 报告字段映射原则

所有字段分三类：

1. `直接字段`
2. `工具结果字段`
3. `人工审核字段`

严格限制：

1. `直接字段` 只能来自业务表
2. `工具结果字段` 只能来自对应工具返回
3. `人工审核字段` 只能来自审核动作记录
4. Agent 只能组织语言，不能改字段含义

---

## 6. 报告字段映射表

### 6.1 案件基础信息

| 报告字段 | 来源 | 是否必填 | 备注 |
|---|---|---|---|
| 案件编号 | `claim_case.claim_id` | 是 | 不可手写 |
| 保单号 | `claim_case.policy_id` | 是 | 不可改写 |
| 报案时间 | `claim_case.reported_at` | 否 | 无则留空 |
| 灾害类型 | `claim_case.disaster_type` | 是 | 只能用枚举值映射 |
| 灾害时间 | `claim_case.loss_date` | 是 | 不允许模糊表述 |
| 作物类型 | `insured_plot.crop_type` | 是 | 来自承保地块信息 |

### 6.2 承保地块信息

| 报告字段 | 来源 | 是否必填 | 备注 |
|---|---|---|---|
| 地块编号 | `insured_plot.plot_id` | 是 | 直接引用 |
| 投保面积 | `insured_plot.insured_area_mu` | 是 | 不允许 Agent 自己换算 |
| 边界版本 | `insured_plot.geom_version` | 是 | 用于追溯 |
| 承保边界示意 | `insured_plot.insured_geom` | 是 | 可转为地图或附图 |

### 6.3 灾情概述

| 报告字段 | 来源 | 是否必填 | 备注 |
|---|---|---|---|
| 灾情摘要 | `agent_summary.disaster_summary` | 是 | 只能基于结构化结果生成 |
| 初筛结论 | `screening_result.summary` | 否 | 只作辅助描述 |
| 无人机精查结论 | `uav_result.summary` | 否 | 有则写，无则留空 |

### 6.4 影像与证据说明

| 报告字段 | 来源 | 是否必填 | 备注 |
|---|---|---|---|
| 卫星证据列表 | `screening_result.reference_assets` | 否 | 卫星初筛可选 |
| 无人机影像编号 | `uav_result.dom_asset_id` | 否 | 有则写 |
| 现场照片编号 | `asset_files.photo_ids` | 否 | 建议列出编号 |
| 影像质量标记 | `uav_result.quality_flag` | 否 | 必须保留原值 |

### 6.5 合规面积核验结果

| 报告字段 | 来源 | 是否必填 | 备注 |
|---|---|---|---|
| 受灾有效面积 | `compliance_result.valid_damage_area` | 是 | 关键字段 |
| 受灾比例 | `compliance_result.damage_ratio` | 是 | 关键字段 |
| 剔除面积 | `compliance_result.excluded_area` | 是 | 关键字段 |
| 剔除日志 | `compliance_result.clip_log` | 是 | 必须可审计 |

### 6.6 规则引擎建议

| 报告字段 | 来源 | 是否必填 | 备注 |
|---|---|---|---|
| 建议风险等级 | `rule_result.risk_level` | 是 | 只能写“建议” |
| 是否必须复核 | `rule_result.review_required` | 是 | 原样保留 |
| 规则依据 | `rule_result.rule_trace` | 是 | 建议分条展示 |
| 规则版本号 | `rule_result.rule_version` | 是 | 必须写入 |

### 6.7 风险提示

| 报告字段 | 来源 | 是否必填 | 备注 |
|---|---|---|---|
| 风险标记 | `agent_output.risk_flags` | 是 | 只能基于已有结果整理 |
| 注意事项 | `agent_output.notes` | 是 | 不得自行扩张业务结论 |

### 6.8 人工审核意见区

| 报告字段 | 来源 | 是否必填 | 备注 |
|---|---|---|---|
| 审核结论 | `human_review.review_status` | 是 | 人工填写 |
| 审核意见 | `human_review.review_comment` | 是 | 人工填写 |
| 审核人 | `human_review.reviewer_name` | 是 | 人工填写 |
| 审核时间 | `human_review.reviewed_at` | 是 | 人工填写 |

---

## 7. 报告生成时给 Agent 的控制口令

你可以直接对 Agent 说：

```text
请按固定报告结构生成草稿。你只能使用已定义章节和字段映射，不得新增自由章节，不得改写关键字段含义，不得生成未经人工审核确认的最终结论。
```

如果它开始乱写，再补一句：

```text
请逐字段列出报告字段来源。任何没有 source 的字段都不允许进入报告草稿。
```

---

## 8. 最关键的约束总结

状态机最重要的约束：

1. 不允许跳步
2. 不允许跳过人工审核
3. 不允许失败后自动伪造后续结果

报告最重要的约束：

1. 不允许字段无来源
2. 不允许关键数值自由改写
3. 不允许建议结论伪装成最终结论

---

## 9. 实际落地建议

这份文件最适合配合三种地方使用：

1. 写进后端流程引擎
2. 写进 Agent system prompt 的补充上下文
3. 写进测试用例和验收脚本

这样控制就不是“只靠嘴说”，而是“流程、字段、模板三位一体”。
