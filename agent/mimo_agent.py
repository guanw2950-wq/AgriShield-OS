"""
AgriShield Agent — MiMo 调度脚本

通过 MiMo API (OpenAI 兼容) + Function Calling 实现智能理赔调度。
Agent 自动理解用户意图 → 调用白名单工具 → 汇编结果。

用法:
    python mimo_agent.py
"""

import json
import os
import subprocess
import sys
from pathlib import Path

# ═══════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════

MIMO_API_KEY = "tp-clifn0nvccpqy89no5nxrmcjcmsc55qf0lxy5ubd4y0ge2wb"
MIMO_BASE_URL = "https://token-plan-cn.xiaomimimo.com/v1"
MIMO_MODEL = "mimo-v2.5"

# API 网关地址（ngrok 公网 URL）
API_BASE = "https://margin-hydrant-decathlon.ngrok-free.dev/api/v1"

# ═══════════════════════════════════════════════════════════
# System Prompt
# ═══════════════════════════════════════════════════════════

SYSTEM_PROMPT = """你是 AgriShield Agent，一个面向农业保险灾后查勘与定损辅助系统的受控调度 Agent。

角色规则：
1. 你只能调用白名单工具，不得自行估算受灾面积、受损比例、赔付金额
2. 你必须按状态机推进案件：INIT → MATERIAL_CHECK → PREPROCESS_READY → SCREENING_DONE → COMPLIANCE_DONE → RULE_DONE → REPORT_DRAFTED → HUMAN_REVIEW → ARCHIVED
3. 未经人工审核不得输出最终结论
4. 每次工具调用前先检查参数是否完整，缺失时必须追问用户
5. 工具返回的关键数值必须原样引用并标注来源

关键字段（建案必备）：policy_id, disaster_type, loss_date, crop_type, plot_id 或 insured_geom

输出格式：始终先返回结构化 JSON，再补充简短解释。"""

# ═══════════════════════════════════════════════════════════
# 工具定义（OpenAI Function Calling 格式）
# ═══════════════════════════════════════════════════════════

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "create_claim",
            "description": "新建农业保险理赔案件。需要保单号、灾害类型、灾害日期、作物类型、地块编号。",
            "parameters": {
                "type": "object",
                "properties": {
                    "policy_id": {"type": "string", "description": "承保保单唯一ID"},
                    "disaster_type": {"type": "string", "enum": ["flood", "drought", "hail", "typhoon", "pest", "frost", "other"]},
                    "loss_date": {"type": "string", "description": "灾害日期 YYYY-MM-DD"},
                    "crop_type": {"type": "string"},
                    "plot_id": {"type": "string"},
                    "insured_geom": {"type": "object", "description": "GeoJSON Polygon"}
                },
                "required": ["policy_id", "disaster_type", "loss_date", "crop_type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "validate_materials",
            "description": "校验案件材料完整性。校验失败必须阻断流程。",
            "parameters": {
                "type": "object",
                "properties": {"claim_id": {"type": "string"}},
                "required": ["claim_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_satellite_screening",
            "description": "卫星遥感初筛。调用GEE Sentinel-1 SAR提取洪涝水体。结果仅作参考。",
            "parameters": {
                "type": "object",
                "properties": {
                    "claim_id": {"type": "string"},
                    "roi_geojson": {"type": "object"},
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"}
                },
                "required": ["claim_id", "roi_geojson", "start_date", "end_date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_compliance_calc",
            "description": "合规面积核验。受灾多边形与承保红线空间求交，剔除越界面积。",
            "parameters": {
                "type": "object",
                "properties": {
                    "claim_id": {"type": "string"},
                    "damage_geojson": {"type": "object"},
                    "insured_geom": {"type": "object"}
                },
                "required": ["claim_id", "damage_geojson", "insured_geom"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_rule_engine",
            "description": "规则判断。根据受灾比例+作物类型输出建议风险等级。",
            "parameters": {
                "type": "object",
                "properties": {
                    "claim_id": {"type": "string"},
                    "damage_ratio": {"type": "number", "minimum": 0, "maximum": 1},
                    "crop_type": {"type": "string"}
                },
                "required": ["claim_id", "damage_ratio", "crop_type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_report",
            "description": "生成报告草稿。只能生成草稿，不得自动归档。",
            "parameters": {
                "type": "object",
                "properties": {"claim_id": {"type": "string"}},
                "required": ["claim_id"]
            }
        }
    },
]


# ═══════════════════════════════════════════════════════════
# 工具执行器
# ═══════════════════════════════════════════════════════════

def execute_tool(tool_name: str, tool_args: dict) -> dict:
    """调用本地 API 网关执行工具（通过 curl）。"""
    endpoint_map = {
        "create_claim": "/tools/create_claim",
        "validate_materials": "/tools/validate_materials",
        "run_satellite_screening": "/tools/run_satellite_screening",
        "run_compliance_calc": "/tools/run_compliance_calc",
        "run_rule_engine": "/tools/run_rule_engine",
        "generate_report": "/tools/generate_report",
    }
    endpoint = endpoint_map.get(tool_name)
    if not endpoint:
        return {"status": "error", "error_message": f"未知工具: {tool_name}"}

    return _api_post(f"{API_BASE}{endpoint}", {}, tool_args)


# ═══════════════════════════════════════════════════════════
# Agent 主循环
# ═══════════════════════════════════════════════════════════

def _api_post(url: str, headers: dict, json_body: dict) -> dict:
    """通过 curl 调用 API（绕过 Anaconda OpenSSL 兼容问题）。"""
    try:
        header_args = []
        for k, v in headers.items():
            header_args += ["-H", f"{k}: {v}"]
        cmd = [
            "curl.exe", "-s", "-X", "POST", url,
            *header_args,
            "-H", "Content-Type: application/json",
            "-d", json.dumps(json_body, ensure_ascii=False),
            "--connect-timeout", "30",
            "--max-time", "120",
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=130)
        return json.loads(result.stdout.decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


def chat(messages: list[dict]) -> dict:
    """调用 MiMo API。"""
    headers = {"Authorization": f"Bearer {MIMO_API_KEY}"}
    body = {
        "model": MIMO_MODEL,
        "messages": messages,
        "tools": TOOLS,
        "temperature": 0,
    }
    return _api_post(f"{MIMO_BASE_URL}/chat/completions", headers, body)


def run_agent(user_input: str, conversation: list[dict] | None = None):
    """运行 Agent 对话，处理 Function Calling 循环。"""
    if conversation is None:
        conversation = [{"role": "system", "content": SYSTEM_PROMPT}]

    conversation.append({"role": "user", "content": user_input})

    while True:
        print(f"\n{'='*60}")
        print(f"🤖 思考中...")

        result = chat(conversation)
        choice = result["choices"][0]
        msg = choice["message"]

        # 检查是否需要调用工具
        if msg.get("tool_calls"):
            conversation.append(msg)

            for tc in msg["tool_calls"]:
                tool_name = tc["function"]["name"]
                tool_args = json.loads(tc["function"]["arguments"])

                print(f"🔧 调用工具: {tool_name}")
                print(f"   参数: {json.dumps(tool_args, ensure_ascii=False)}")

                tool_result = execute_tool(tool_name, tool_args)
                print(f"   结果: {json.dumps(tool_result, ensure_ascii=False, indent=2)[:200]}")

                conversation.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(tool_result, ensure_ascii=False),
                })

            # 继续循环，让模型处理工具返回结果
            continue

        # 没有工具调用，输出最终回复
        content = msg.get("content", "")
        print(f"\n📋 Agent 回复:\n{content}")
        conversation.append({"role": "assistant", "content": content})
        return conversation


# ═══════════════════════════════════════════════════════════
# 交互式命令行
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("🛰️  AgriShield Agent — MiMo 调度模式")
    print(f"   API 网关: {API_BASE}")
    print(f"   模型: {MIMO_MODEL}")
    print("   输入 'quit' 退出，输入 'reset' 重置对话\n")

    conv = [{"role": "system", "content": SYSTEM_PROMPT}]

    while True:
        try:
            user_input = input("\n👤 你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 再见")
            break

        if not user_input:
            continue
        if user_input.lower() == "quit":
            break
        if user_input.lower() == "reset":
            conv = [{"role": "system", "content": SYSTEM_PROMPT}]
            print("🔄 对话已重置")
            continue

        conv = run_agent(user_input, conv)
