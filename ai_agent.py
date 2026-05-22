"""
AI Agent 核心模块 — 自主决策循环智能体

架构：
  ┌───────────────────────────────────────────┐
  │           AI Agent Brain                   │
  │  (LLM + Function Calling + Memory)        │
  └──────────────┬────────────────────────────┘
                 │ 决策 → 调用工具 → 观察结果 → 再决策
  ┌──────────────┴────────────────────────────┐
  │           Tool Registry (14 tools)          │
  └──────────────┬────────────────────────────┘
                 │
  ┌──────────────┴────────────────────────────┐
  │           Risk Guard (风控拦截层)           │
  └──────────────┬────────────────────────────┘
                 │
  ┌──────────────┴────────────────────────────┐
  │           Memory System                    │
  │  short_term (推理链) │ long_term (JSON)    │
  └───────────────────────────────────────────┘

模型接口可扩展：DeepSeek V4 Pro → 任意 OpenAI 兼容模型
"""

import json
import os
import re
import traceback
from datetime import datetime, date, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

# pandas 可能在某些工具函数的局部导入中需要
import pandas as pd

# ═══════════════════════════════════════════════════════
#  可扩展 LLM 接口
# ═══════════════════════════════════════════════════════

# ── 模型配置注册表 ──
# 新增模型只需在下面加一行即可，无需改任何代码
MODEL_REGISTRY: Dict[str, dict] = {
    "deepseek-v4-pro": {
        "provider_name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "model_id": "deepseek-v4-pro",
        "supports_function_calling": True,
        "supports_thinking": True,
        "max_tokens": 8192,
        "default_temperature": 0.3,
        "description": "DeepSeek V4 Pro — 推理能力强，适合复杂分析",
    },
    "deepseek-v4-flash": {
        "provider_name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "model_id": "deepseek-v4-flash",
        "supports_function_calling": True,
        "supports_thinking": True,
        "max_tokens": 4096,
        "default_temperature": 0.3,
        "description": "DeepSeek V4 Flash — 速度快，成本低，日常对话",
    },
    "gpt-4o-mini": {
        "provider_name": "OpenAI",
        "base_url": "https://api.openai.com",
        "model_id": "gpt-4o-mini",
        "supports_function_calling": True,
        "supports_thinking": False,
        "max_tokens": 4096,
        "default_temperature": 0.7,
        "description": "GPT-4o Mini — OpenAI 轻量模型",
    },
    "qwen-plus": {
        "provider_name": "通义千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode",
        "model_id": "qwen-plus",
        "supports_function_calling": True,
        "supports_thinking": False,
        "max_tokens": 4096,
        "default_temperature": 0.4,
        "description": "Qwen Plus — OpenAI兼容，中文金融问答稳定",
    },
    "qwen-turbo": {
        "provider_name": "通义千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode",
        "model_id": "qwen-turbo",
        "supports_function_calling": True,
        "supports_thinking": False,
        "max_tokens": 4096,
        "default_temperature": 0.4,
        "description": "Qwen Turbo — 响应更快，适合日常对话",
    },
    "moonshot-v1-8k": {
        "provider_name": "Moonshot",
        "base_url": "https://api.moonshot.cn",
        "model_id": "moonshot-v1-8k",
        "supports_function_calling": True,
        "supports_thinking": False,
        "max_tokens": 4096,
        "default_temperature": 0.4,
        "description": "Moonshot 8K — OpenAI兼容接口",
    },
    "glm-4-flash": {
        "provider_name": "智谱AI",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model_id": "glm-4-flash",
        "supports_function_calling": True,
        "supports_thinking": False,
        "max_tokens": 4096,
        "default_temperature": 0.4,
        "description": "GLM-4 Flash — 低成本快速响应",
    },
    "openai-compatible": {
        "provider_name": "自定义",
        "base_url": "http://localhost:11434/v1",
        "model_id": "openai-compatible-model",
        "supports_function_calling": True,
        "supports_thinking": False,
        "max_tokens": 4096,
        "default_temperature": 0.4,
        "description": "自定义 OpenAI 兼容服务，如 Ollama / LM Studio / One API",
    },
    # ── 新增模型模板 ──
    # "your-model": {
    #     "base_url": "https://api.your-provider.com",
    #     "model_id": "your-model-id",
    #     "supports_function_calling": True,
    #     "supports_thinking": False,
    #     "max_tokens": 4096,
    #     "default_temperature": 0.5,
    #     "description": "描述",
    # },
}


def _load_env_config() -> dict:
    """从 .env 或环境变量加载 LLM 配置"""
    config = {
        "api_key": os.environ.get("API_KEY", ""),
        "base_url": os.environ.get("API_BASE", "https://api.deepseek.com"),
        "model": os.environ.get("MODEL", "deepseek-v4-pro"),
        "proxy_url": os.environ.get("PROXY_URL", ""),
    }
    # 尝试从 .env 文件加载
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip()
                    if key == "API_KEY" and not config["api_key"]:
                        config["api_key"] = val
                    elif key == "API_BASE" and val != "https://api.openai.com":
                        config["base_url"] = val
                    elif key == "MODEL" and val != "gpt-4o-mini":
                        config["model"] = val
                    elif key == "PROXY_URL":
                        config["proxy_url"] = val
    # .env 中 MODEL=gpt-4o-mini 是旧默认值，不覆盖用户主动选择
    if config["model"] == "gpt-4o-mini":
        config["model"] = "deepseek-v4-pro"
    return config


class LLMClient:
    """
    可扩展 LLM 客户端 — 支持 DeepSeek V4 Pro / Flash / 任何 OpenAI 兼容 API

    核心设计：
    1. Function Calling（工具调用）— DeepSeek V4 Pro / GPT-4o 通用
    2. 多轮对话上下文管理
    3. 代理绕过（国内环境不需要代理）
    4. 模型热切换 — 无需重启
    """

    def __init__(self, model_name: str = None, api_key: str = None, base_url: str = None):
        env = _load_env_config()
        self.api_key = api_key or env["api_key"]
        self.base_url = (base_url or env["base_url"]).rstrip("/")
        self.model_name = model_name or env["model"]
        self.proxy_url = env.get("proxy_url", "")
        try:
            self.timeout = max(10, min(180, int(os.environ.get("AGENT_API_TIMEOUT", "45"))))
        except ValueError:
            self.timeout = 45
        self._messages: List[dict] = []

        # 从注册表获取模型配置
        model_cfg = MODEL_REGISTRY.get(self.model_name, {})
        if model_cfg:
            # 注册表的 base_url 只在用户未显式指定时生效
            if not base_url and not env.get("_explicit_base"):
                self.base_url = model_cfg["base_url"].rstrip("/")
            self.model_id = model_cfg["model_id"]
            self.supports_function_calling = model_cfg.get("supports_function_calling", True)
            self.max_tokens = model_cfg.get("max_tokens", 4096)
            self.default_temperature = model_cfg.get("default_temperature", 0.3)
            self.supports_thinking = model_cfg.get("supports_thinking", False)
        else:
            self.model_id = self.model_name
            self.supports_function_calling = True
            self.max_tokens = 4096
            self.default_temperature = 0.3
            self.supports_thinking = False

    def _chat_completions_url(self) -> str:
        """兼容 base_url 填到域名或 /v1 两种写法。"""
        base = self.base_url.rstrip("/")
        if re.search(r"/v\d+$", base):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    def set_model(self, model_name: str):
        """热切换模型"""
        if model_name in MODEL_REGISTRY:
            cfg = MODEL_REGISTRY[model_name]
            self.model_name = model_name
            self.model_id = cfg["model_id"]
            self.base_url = cfg["base_url"].rstrip("/")
            self.supports_function_calling = cfg.get("supports_function_calling", True)
            self.max_tokens = cfg.get("max_tokens", 4096)
            self.default_temperature = cfg.get("default_temperature", 0.3)
            self.supports_thinking = cfg.get("supports_thinking", False)
        else:
            self.model_name = model_name
            self.model_id = model_name

    def clear_messages(self):
        """清空对话历史"""
        self._messages = []

    def test_connection(self) -> Tuple[bool, str]:
        """
        测试 API 连接是否正常

        Returns:
            (success, message) — success=True 表示连接正常
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        # 用最少的 token 测试连接
        data = {
            "model": self.model_id,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 5,
            "temperature": 0,
        }
        # 禁用 thinking（测试不需要）
        if self.supports_thinking:
            data["thinking"] = {"type": "disabled"}

        proxies = {}
        if self.proxy_url:
            proxies = {"http": self.proxy_url, "https": self.proxy_url}

        try:
            import contextlib

            @contextlib.contextmanager
            def _no_proxy_ctx():
                saved = {}
                for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
                    if key in os.environ:
                        saved[key] = os.environ.pop(key)
                try:
                    yield
                finally:
                    for k, v in saved.items():
                        os.environ[k] = v

            with _no_proxy_ctx():
                response = requests.post(
                    self._chat_completions_url(),
                    headers=headers,
                    json=data,
                    timeout=15,
                    proxies=proxies if proxies else None,
                )

            if response.status_code == 401:
                return False, "❌ API Key 无效，请检查密钥是否正确"
            if response.status_code == 402:
                return False, "❌ 账户余额不足，请充值后重试"
            if response.status_code == 429:
                return False, "⚠️ 请求频率超限，请稍后重试"
            if response.status_code >= 500:
                return False, f"❌ 服务器错误 ({response.status_code})，请稍后重试"

            response.raise_for_status()
            result = response.json()
            model_name = result.get("model", self.model_id)
            usage = result.get("usage", {})
            total_tokens = usage.get("total_tokens", "?")
            return True, f"✅ 连接成功！模型: {model_name}，测试消耗 {total_tokens} tokens"
        except requests.exceptions.ConnectionError:
            return False, f"❌ 无法连接到 {self.base_url}，请检查网络或 API_BASE 配置"
        except requests.exceptions.Timeout:
            return False, "❌ 连接超时，请检查网络"
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else "?"
            detail = ""
            try:
                detail = e.response.json().get("error", {}).get("message", "")
            except Exception:
                pass
            return False, f"❌ API 返回错误 {status}: {detail or str(e)}"
        except Exception as e:
            return False, f"❌ 连接测试失败: {str(e)}"

    def add_message(self, role: str, content: str, tool_calls: list = None, tool_call_id: str = None):
        """添加一条消息到对话历史"""
        msg = {"role": role, "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        if tool_call_id:
            msg["tool_call_id"] = tool_call_id
        self._messages.append(msg)

    def chat(self, messages: List[dict] = None, tools: List[dict] = None,
             temperature: float = None, max_iterations: int = 10) -> dict:
        """
        发送请求到 LLM，自动处理 Function Calling 循环

        Args:
            messages: 对话消息列表（为空则使用内部 _messages）
            tools: 可调用的工具定义（OpenAI Function Calling 格式）
            temperature: 生成温度
            max_iterations: 最大工具调用轮数（防无限循环）

        Returns:
            {"content": str, "tool_calls": [...], "usage": dict}
            如果 LLM 不需要调用工具，tool_calls 为空列表
        """
        if messages is None:
            messages = self._messages.copy()

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        data = {
            "model": self.model_id,
            "messages": messages,
            "temperature": temperature or self.default_temperature,
            "max_tokens": self.max_tokens,
        }

        if tools and self.supports_function_calling:
            data["tools"] = tools
            data["tool_choice"] = "auto"

        # DeepSeek V4 默认开启 thinking 模式，但 function calling 场景下
        # reasoning_content 必须回传，否则报错。为了稳定性，默认禁用 thinking。
        # （function calling 已提供结构化推理，不需要额外的思维链）
        if self.supports_thinking:
            data["thinking"] = {"type": "disabled"}

        # 代理绕过
        proxies = {}
        if self.proxy_url:
            proxies = {"http": self.proxy_url, "https": self.proxy_url}

        try:
            import contextlib

            @contextlib.contextmanager
            def _no_proxy_ctx():
                saved = {}
                for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
                    if key in os.environ:
                        saved[key] = os.environ.pop(key)
                try:
                    yield
                finally:
                    for k, v in saved.items():
                        os.environ[k] = v

            # 国内直连不需要代理，用 _no_proxy 绕过
            with _no_proxy_ctx():
                response = requests.post(
                    self._chat_completions_url(),
                    headers=headers,
                    json=data,
                    timeout=self.timeout,
                    proxies=proxies if proxies else None,
                )
            response.raise_for_status()
            result = response.json()

            choice = result.get("choices", [{}])[0]
            msg = choice.get("message", {})

            # 提取内容
            content = msg.get("content", "") or ""
            tool_calls = msg.get("tool_calls", [])
            reasoning_content = msg.get("reasoning_content")  # DeepSeek V4 思维链
            usage = result.get("usage", {})

            return {
                "content": content,
                "tool_calls": tool_calls,
                "reasoning_content": reasoning_content,
                "usage": usage,
                "finish_reason": choice.get("finish_reason", ""),
            }

        except requests.exceptions.Timeout:
            return {"content": f"[ERROR] API 请求超时（{self.timeout}秒），请稍后重试或切换快速模型", "tool_calls": [], "usage": {}, "finish_reason": "error"}
        except requests.exceptions.ConnectionError:
            return {"content": "[ERROR] 无法连接到 API 服务器，请检查网络和 API_BASE 配置", "tool_calls": [], "usage": {}, "finish_reason": "error"}
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else "?"
            detail = ""
            try:
                detail = e.response.json().get("error", {}).get("message", "")
            except Exception:
                pass
            return {"content": f"[ERROR] API 返回错误 {status}: {detail or str(e)}", "tool_calls": [], "usage": {}, "finish_reason": "error"}
        except Exception as e:
            return {"content": f"[ERROR] LLM 调用异常: {str(e)}", "tool_calls": [], "usage": {}, "finish_reason": "error"}

    def run_agent_loop(self, system_prompt: str, user_message: str,
                       tools: List[dict], tool_executor: Callable,
                       max_iterations: int = 10,
                       on_step: Callable = None) -> str:
        """
        Function Calling Agent 核心循环

        流程：
        1. 发送 system_prompt + user_message + tools 定义
        2. 如果 LLM 返回 tool_calls → 执行工具 → 反馈结果 → 继续
        3. 如果 LLM 返回纯文本 → 结束循环，返回最终回答
        4. 超过 max_iterations → 强制结束

        Args:
            system_prompt: 系统提示词
            user_message: 用户消息
            tools: 工具定义列表
            tool_executor: 工具执行函数 (tool_name, arguments) -> result_string
            max_iterations: 最大循环次数
            on_step: 每步回调 on_step(step, action, result)，用于 UI 更新

        Returns:
            最终文本回答
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        final_answer = ""
        for i in range(max_iterations):
            response = self.chat(messages=messages, tools=tools if i == 0 else tools)

            if response.get("finish_reason") == "error":
                return response.get("content", "Agent 执行出错")

            msg = response.get("content", "")
            tool_calls = response.get("tool_calls", [])
            reasoning_content = response.get("reasoning_content")  # DeepSeek V4

            # 没有 tool_calls → LLM 给出最终回答
            if not tool_calls:
                final_answer = msg
                break

            # 有 tool_calls → 逐个执行工具
            # DeepSeek V4 思维链必须原样回传，否则后续请求报错
            assistant_msg = {"role": "assistant", "content": msg or ""}
            if reasoning_content:
                assistant_msg["reasoning_content"] = reasoning_content
            if tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": tc["function"]["arguments"],
                        },
                    }
                    for tc in tool_calls
                ]
            messages.append(assistant_msg)

            # 逐个执行工具调用
            for tc in tool_calls:
                tool_name = tc["function"]["name"]
                try:
                    arguments = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    arguments = {}

                # 执行工具
                if on_step:
                    on_step(i + 1, f"调用工具: {tool_name}", json.dumps(arguments, ensure_ascii=False))

                result = tool_executor(tool_name, arguments)

                if on_step:
                    on_step(i + 1, f"工具结果: {tool_name}", str(result)[:200])

                # 反馈工具结果
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": str(result),
                })

        else:
            # 达到最大循环次数
            final_answer = msg or "Agent 已达到最大循环次数，未能得出最终结论。"

        # 保存到内部消息历史
        self._messages = messages + [{"role": "assistant", "content": final_answer}]

        return final_answer


# ═══════════════════════════════════════════════════════
#  风控拦截层 (Risk Guard)
# ═══════════════════════════════════════════════════════

RISK_RULES = {
    "max_single_position_pct": 0.20,   # 单票不超过总资产 20%
    "max_total_position_pct": 0.80,    # 总仓位不超过 80%
    "stop_loss_pct": -0.05,            # 浮亏 5% 强制止损
    "take_profit_pct": 0.10,           # 浮盈 10% 强制止盈
    "max_daily_trades": 10,            # 单日最多 10 笔交易
    "min_cash_reserve": 200_000,       # 至少保留 20 万现金
    "blacklist": [],                   # 黑名单（踩雷后加入）
}


class RiskGuard:
    """风控拦截层 — 所有交易指令必须经过此层校验"""

    def __init__(self, rules: dict = None):
        self.rules = rules or RISK_RULES.copy()
        self.daily_trade_count = 0
        self.daily_date = date.today()

    def _reset_daily_if_needed(self):
        """每日重置交易计数"""
        today = date.today()
        if self.daily_date != today:
            self.daily_date = today
            self.daily_trade_count = 0

    def check_buy(self, code: str, name: str, shares: int, price: float,
                  portfolio, total_asset: float) -> Tuple[bool, str]:
        """
        买入前风控检查

        Returns:
            (allowed, reason)
        """
        self._reset_daily_if_needed()

        # 1. 黑名单检查
        if code in self.rules["blacklist"]:
            return False, f"⛔ {name}({code}) 在黑名单中，禁止交易"

        # 2. A股整手检查
        if shares % 100 != 0:
            return False, f"⛔ A股必须以手为单位（100股整数倍），当前 {shares} 股不满足"

        # 3. 单票仓位检查
        position_cost = shares * price
        position_ratio = position_cost / total_asset if total_asset > 0 else 1
        existing_ratio = 0
        if code in portfolio.positions:
            existing_ratio = (portfolio.positions[code]["shares"] * price) / total_asset if total_asset > 0 else 0

        if position_ratio + existing_ratio > self.rules["max_single_position_pct"]:
            max_shares = int(total_asset * self.rules["max_single_position_pct"] / price / 100) * 100
            remaining = max(0, max_shares - portfolio.positions.get(code, {}).get("shares", 0))
            return False, (f"⛔ 单票仓位不能超过总资产 {self.rules['max_single_position_pct']:.0%}，"
                           f"当前+目标将达 {(position_ratio + existing_ratio):.1%}，"
                           f"最多可再买 {remaining} 股")

        # 4. 总仓位检查
        current_position_ratio = (total_asset - portfolio.cash) / total_asset if total_asset > 0 else 0
        new_position_ratio = current_position_ratio + position_ratio
        if new_position_ratio > self.rules["max_total_position_pct"]:
            return False, (f"⛔ 总仓位不能超过 {self.rules['max_total_position_pct']:.0%}，"
                           f"当前 {current_position_ratio:.1%}，加仓后将达 {new_position_ratio:.1%}")

        # 5. 最低现金储备检查
        remaining_cash = portfolio.cash - position_cost
        if remaining_cash < self.rules["min_cash_reserve"]:
            return False, (f"⛔ 买入后现金将剩余 {remaining_cash:,.0f}，"
                           f"低于最低储备 {self.rules['min_cash_reserve']:,.0f}")

        # 6. 日交易次数检查
        if self.daily_trade_count >= self.rules["max_daily_trades"]:
            return False, f"⛔ 今日已交易 {self.daily_trade_count} 笔，达到日交易上限 {self.rules['max_daily_trades']}"

        # 7. 资金充足检查
        total_cost = position_cost + max(position_cost * 0.0001, 5)  # 含佣金
        if total_cost > portfolio.cash:
            return False, f"⛔ 资金不足！需要 {total_cost:,.2f}，可用 {portfolio.cash:,.2f}"

        self.daily_trade_count += 1
        return True, "✅ 买入风控通过"

    def check_sell(self, code: str, name: str, shares: int, price: float,
                   portfolio, total_asset: float) -> Tuple[bool, str]:
        """卖出前风控检查"""
        self._reset_daily_if_needed()

        if code not in portfolio.positions:
            return False, f"⛔ 持仓中没有 {name}({code})"

        held = portfolio.positions[code]["shares"]
        if shares > held:
            return False, f"⛔ 持仓不足！持有 {held} 股，想卖出 {shares} 股"

        if self.daily_trade_count >= self.rules["max_daily_trades"]:
            return False, f"⛔ 今日已交易 {self.daily_trade_count} 笔，达到日交易上限"

        self.daily_trade_count += 1
        return True, "✅ 卖出风控通过"

    def check_stop_loss(self, code: str, price: float, portfolio) -> Optional[str]:
        """
        止损检查 — 返回 None 表示不需要操作，返回原因表示触发止损

        Args:
            code: 股票代码
            price: 当前价格
            portfolio: Portfolio 实例

        Returns:
            None 或 止损原因
        """
        if code not in portfolio.positions:
            return None

        pos = portfolio.positions[code]
        avg_cost = pos["avg_cost"]
        pnl_pct = (price - avg_cost) / avg_cost

        if pnl_pct <= self.rules["stop_loss_pct"]:
            return f"💥 触发止损！{code} 浮亏 {pnl_pct:.1%}，低于止损线 {self.rules['stop_loss_pct']:.0%}"

        if pnl_pct >= self.rules["take_profit_pct"]:
            return f"🎯 触发止盈！{code} 浮盈 {pnl_pct:.1%}，超过止盈线 {self.rules['take_profit_pct']:.0%}"

        return None

    def add_to_blacklist(self, code: str, reason: str = ""):
        """加入黑名单"""
        if code not in self.rules["blacklist"]:
            self.rules["blacklist"].append(code)

    def remove_from_blacklist(self, code: str):
        """移出黑名单"""
        if code in self.rules["blacklist"]:
            self.rules["blacklist"].remove(code)


# ═══════════════════════════════════════════════════════
#  工具注册表 (Tool Registry)
# ═══════════════════════════════════════════════════════

def _build_tool_schemas() -> List[dict]:
    """
    构建工具的 OpenAI Function Calling schema

    每个工具的参数必须用 JSON Schema 描述清楚，LLM 才能正确传参。
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "search_stocks",
                "description": "搜索A股/指数/ETF，支持名称模糊搜索和代码精确搜索。返回前5个匹配结果。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "keyword": {
                            "type": "string",
                            "description": "搜索关键词，如'平安'、'600519'、'银行ETF'",
                        }
                    },
                    "required": ["keyword"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_kline_analysis",
                "description": "获取单只股票的K线技术分析，包括最新行情、相似形态、趋势预测和持仓收益分析。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": "6位股票代码，如'600519'、'000001'",
                        },
                        "window_size": {
                            "type": "integer",
                            "description": "相似度匹配窗口天数，默认30",
                        },
                    },
                    "required": ["code"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "screen_macd_divergence",
                "description": "MACD底背离全市场扫描，发现即将反转的股票。可选仅金叉或全部扫描。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "scan_mode": {
                            "type": "string",
                            "enum": ["底背离形态", "即将金叉预测", "全部扫描"],
                            "description": "扫描模式：底背离形态（较保守）、即将金叉预测（较积极）、全部扫描",
                        },
                        "strict_mode": {
                            "type": "boolean",
                            "description": "是否严格模式（仅已金叉的底背离），默认True",
                        },
                    },
                    "required": ["scan_mode"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "auto_select_stocks",
                "description": "本地自主选股闭环：从自选股或全市场抽样读取TDX日线，快速打分、排序、输出候选股，并可加入自选股。优先用于选股请求。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "universe": {
                            "type": "string",
                            "enum": ["自选股", "全市场抽样", "自选股优先+全市场抽样"],
                            "description": "候选范围。全市场抽样会受max_candidates限制，避免阻塞UI。",
                        },
                        "max_candidates": {
                            "type": "integer",
                            "description": "最多扫描多少只股票，默认120，建议不超过500。",
                        },
                        "top_n": {
                            "type": "integer",
                            "description": "返回前N只候选股，默认8。",
                        },
                        "add_to_watchlist": {
                            "type": "boolean",
                            "description": "是否把Top候选股自动加入自选股，默认False。",
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_concentration_analysis",
                "description": "获取单只股票的筹码集中度分析，包括股东人数变化、户均持股等。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": "6位股票代码",
                        },
                    },
                    "required": ["code"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "add_to_watchlist",
                "description": "将股票加入自选股列表。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": "6位股票代码",
                        },
                    },
                    "required": ["code"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "remove_from_watchlist",
                "description": "将股票从自选股列表移除。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": "6位股票代码",
                        },
                    },
                    "required": ["code"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_realtime_quotes",
                "description": "批量获取实时行情数据，包括现价、涨跌幅、成交量等。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "codes": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "6位股票代码列表，如['600519', '000001']",
                        },
                    },
                    "required": ["codes"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "buy_stock",
                "description": "在虚拟盘中买入股票。会经过风控检查（仓位限制、资金充足等）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": "6位股票代码",
                        },
                        "shares": {
                            "type": "integer",
                            "description": "买入股数，必须是100的整数倍（A股1手=100股）",
                        },
                    },
                    "required": ["code", "shares"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "sell_stock",
                "description": "在虚拟盘中卖出股票。传入0表示全部卖出。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": "6位股票代码",
                        },
                        "shares": {
                            "type": "integer",
                            "description": "卖出股数，0表示全部卖出",
                        },
                    },
                    "required": ["code", "shares"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_portfolio_status",
                "description": "获取虚拟盘持仓状态，包括每只持仓的市值、盈亏、总资产等。",
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_trade_history",
                "description": "获取虚拟盘近期交易记录。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "days": {
                            "type": "integer",
                            "description": "查询最近N天的交易记录，默认7天",
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_market_overview",
                "description": "获取大盘概况（上证指数、深证成指、创业板指的涨跌幅），用于判断市场整体走势。",
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "save_learning",
                "description": "把本轮分析得到的经验、交易规则或避坑结论写入Agent长期记忆。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "note": {
                            "type": "string",
                            "description": "要保存的经验或规则，必须具体可执行。",
                        },
                        "category": {
                            "type": "string",
                            "description": "经验分类，如选股、风控、复盘、交易。",
                        },
                    },
                    "required": ["note"],
                },
            },
        },
    ]


# ═══════════════════════════════════════════════════════
#  工具执行器 — 连接现有模块
# ═══════════════════════════════════════════════════════

class ToolExecutor:
    """
    工具执行器 — 将 LLM 的工具调用翻译为对现有模块的 Python 调用

    每个 tool 调用最终到这里执行，并返回字符串结果给 LLM。
    """

    def __init__(self, risk_guard: RiskGuard = None):
        self.risk_guard = risk_guard or RiskGuard()

    def execute(self, tool_name: str, arguments: dict) -> str:
        """执行一个工具调用，返回结果字符串"""
        try:
            handler = getattr(self, f"_tool_{tool_name}", None)
            if handler is None:
                return f"[ERROR] 未知工具: {tool_name}"
            result = handler(**arguments)
            return str(result)
        except Exception as e:
            traceback.print_exc()
            return f"[ERROR] 工具 {tool_name} 执行失败: {str(e)}"

    # ── 工具1：搜索股票 ──
    def _tool_search_stocks(self, keyword: str) -> str:
        from streamlit_app import search_securities, get_stock_name_by_code
        results = search_securities(keyword)
        if not results:
            return f"搜索 '{keyword}' 无结果"
        # 只返回前5个
        top5 = results[:5]
        lines = []
        for r in top5:
            lines.append(f"- {r.get('name', '?')}({r.get('code', '?')}) 类型:{r.get('type', '?')} 交易所:{r.get('exchange', '?')}")
        return f"搜索 '{keyword}' 找到 {len(results)} 个结果（显示前5个）：\n" + "\n".join(lines)

    # ── 工具2：K线分析 ──
    def _tool_get_kline_analysis(self, code: str, window_size: int = 30) -> str:
        from streamlit_app import get_market_data, find_similar_patterns, analyze_future_trends, analyze_holding_returns
        from tdx_reader import set_tdx_data_dir
        import streamlit as st

        tdx_path = st.session_state.get("tdx_data_path", r"D:\new_tdx\vipdoc")
        set_tdx_data_dir(tdx_path)

        df = get_market_data(code, "stock", days=None)
        if df is None or df.empty:
            return f"无法获取 {code} 的K线数据"

        # 最新行情
        latest = df.iloc[-1]
        lines = [
            f"=== {code} K线分析 ===",
            f"最新日期: {latest['trade_date']}",
            f"收盘价: {latest['close']:.2f}",
            f"开盘价: {latest['open']:.2f}",
            f"最高价: {latest['high']:.2f}",
            f"最低价: {latest['low']:.2f}",
            f"成交量: {latest.get('volume', 'N/A')}",
        ]

        # 相似形态
        try:
            similar = find_similar_patterns(df, window_size=window_size, top_n=5, use_optimized=True, period_type="daily")
            if similar:
                lines.append(f"\n相似K线形态（Top 5）：")
                for i, p in enumerate(similar[:5]):
                    pdata = p.get("pattern_data", pd.DataFrame())
                    fdata = p.get("future_data", pd.DataFrame())
                    score = p.get("similarity_score", 0)
                    name = p.get("name", "?")
                    pcode = p.get("code", "?")
                    lines.append(f"  {i+1}. {name}({pcode}) 相似度={score:.4f}")
            else:
                lines.append("\n未找到相似形态")
        except Exception as e:
            lines.append(f"\n相似形态分析失败: {e}")

        # 持仓收益
        try:
            similar_patterns = find_similar_patterns(df, window_size=window_size, top_n=10, use_optimized=True, period_type="daily")
            if similar_patterns:
                holding_stats = analyze_holding_returns(similar_patterns)
                if holding_stats:
                    lines.append("\n持仓收益分析：")
                    for days_key in sorted(holding_stats.keys(), key=lambda x: int(x)):
                        stat = holding_stats[days_key]
                        lines.append(f"  持有{days_key}天: 平均收益={stat.get('avg_return', 0):.2f}%, 胜率={stat.get('win_rate', 0):.1%}")
        except Exception:
            pass

        return "\n".join(lines)

    # ── 工具3：MACD筛选 ──
    def _tool_screen_macd_divergence(self, scan_mode: str = "全部扫描", strict_mode: bool = True) -> str:
        # MACD筛选是重量级操作，返回摘要
        return (f"MACD底背离扫描已启动（模式: {scan_mode}, 严格模式: {strict_mode}）。\n"
                f"这是一个耗时操作（约1-5分钟），请在TAB6中查看完整结果。\n"
                f"建议：先用 get_market_overview 判断大盘走势，再决定是否筛选。")

    @staticmethod
    def _sample_evenly(items: List[str], limit: int) -> List[str]:
        """从大列表中均匀抽样，避免只扫固定代码段。"""
        if limit <= 0:
            return []
        if len(items) <= limit:
            return items
        if limit == 1:
            return [items[0]]
        step = (len(items) - 1) / float(limit - 1)
        sampled = []
        seen = set()
        for i in range(limit):
            item = items[int(round(i * step))]
            if item not in seen:
                sampled.append(item)
                seen.add(item)
        return sampled

    @staticmethod
    def _normalize_stock_code(code: Any) -> str:
        match = re.search(r"\d{6}", str(code or ""))
        return match.group(0) if match else str(code or "").strip()

    @staticmethod
    def _is_a_share_stock_code(code: str) -> bool:
        code = str(code or "")
        stock_prefixes = ("000", "001", "002", "003", "300", "301", "600", "601", "603", "605", "688", "689")
        return len(code) == 6 and code.startswith(stock_prefixes)

    def _load_watchlist_items(self) -> List[dict]:
        try:
            import streamlit as st
            from streamlit_app import load_watchlist
            return list(st.session_state.get("watchlist", load_watchlist()))
        except Exception:
            try:
                from streamlit_app import load_watchlist
                return list(load_watchlist())
            except Exception:
                return []

    def _get_tdx_path(self) -> str:
        try:
            import streamlit as st
            return st.session_state.get("tdx_data_path", r"D:\new_tdx\vipdoc")
        except Exception:
            return r"D:\new_tdx\vipdoc"

    def _build_auto_select_universe(self, universe: str, max_candidates: int):
        from tdx_reader import get_tdx_reader

        reader = get_tdx_reader(self._get_tdx_path())
        available = reader.get_available_codes()
        all_codes = sorted({
            code for codes in available.values()
            for code in codes
            if self._is_a_share_stock_code(str(code))
        })

        watch_items = self._load_watchlist_items()
        watch_codes = []
        for item in watch_items:
            code = self._normalize_stock_code(item.get("code") if isinstance(item, dict) else item)
            if code and code in all_codes and code not in watch_codes:
                watch_codes.append(code)

        universe_text = str(universe or "自选股优先+全市场抽样")
        wants_watch_priority = "自选" in universe_text and ("优先" in universe_text or "+" in universe_text)
        if "自选" in universe_text and not wants_watch_priority and "全市场" not in universe_text and "抽样" not in universe_text:
            codes = watch_codes
            note = f"自选股 {len(watch_codes)} 只"
            if not codes:
                codes = self._sample_evenly(all_codes, max_candidates)
                note = f"自选股为空，已回退到全市场均匀抽样 {len(codes)} / {len(all_codes)} 只"
        elif wants_watch_priority:
            remaining = [code for code in all_codes if code not in set(watch_codes)]
            sampled = self._sample_evenly(remaining, max(0, max_candidates - len(watch_codes)))
            codes = (watch_codes + sampled)[:max_candidates]
            note = f"自选股优先 {len(watch_codes)} 只 + 全市场抽样 {len(sampled)} 只"
        elif "全" in universe_text or "all" in universe_text.lower():
            codes = self._sample_evenly(all_codes, max_candidates)
            note = f"全市场均匀抽样 {len(codes)} / {len(all_codes)} 只"
        else:
            remaining = [code for code in all_codes if code not in set(watch_codes)]
            sampled = self._sample_evenly(remaining, max(0, max_candidates - len(watch_codes)))
            codes = (watch_codes + sampled)[:max_candidates]
            note = f"自选股优先 {len(watch_codes)} 只 + 全市场抽样 {len(sampled)} 只"

        return reader, codes, note, len(all_codes)

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            if pd.isna(value):
                return default
            return float(value)
        except Exception:
            return default

    def _score_stock_candidate(self, code: str, df: pd.DataFrame) -> Optional[dict]:
        from streamlit_app import calculate_macd

        if df is None or df.empty:
            return None
        if "close" not in df.columns:
            return None

        df = df.dropna(subset=["close"]).tail(240).copy()
        if len(df) < 60:
            return None

        macd_values = calculate_macd(df)
        if macd_values is None or macd_values.empty:
            return None
        df_macd = df.copy()
        for col in ["DIF", "DEA", "MACD"]:
            if col in macd_values.columns:
                df_macd[col] = macd_values[col]

        latest = df_macd.iloc[-1]
        prev = df_macd.iloc[-2]
        close = self._safe_float(latest.get("close"))
        if close <= 0:
            return None

        close_series = df_macd["close"].astype(float)
        ma20 = self._safe_float(close_series.tail(20).mean())
        ma60 = self._safe_float(close_series.tail(60).mean())
        close_20 = self._safe_float(close_series.iloc[-20])
        close_60 = self._safe_float(close_series.iloc[-60])
        ret20 = (close / close_20 - 1) * 100 if close_20 > 0 else 0
        ret60 = (close / close_60 - 1) * 100 if close_60 > 0 else 0
        high20 = self._safe_float(close_series.tail(20).max())
        drawdown20 = (close / high20 - 1) * 100 if high20 > 0 else 0

        if "volume" in df_macd.columns:
            volume_series = df_macd["volume"].astype(float)
            recent_vol = self._safe_float(volume_series.tail(5).mean())
            base_vol = self._safe_float(volume_series.tail(30).head(25).mean())
            vol_ratio = recent_vol / base_vol if base_vol > 0 else 0
        else:
            vol_ratio = 0

        dif = self._safe_float(latest.get("DIF"))
        dea = self._safe_float(latest.get("DEA"))
        prev_dif = self._safe_float(prev.get("DIF"))
        prev_dea = self._safe_float(prev.get("DEA"))
        macd_cross = prev_dif <= prev_dea and dif > dea

        score = 0.0
        reasons = []

        if macd_cross:
            score += 28
            reasons.append("MACD金叉")
        elif dif > dea:
            score += 14
            reasons.append("MACD多头")
        else:
            score -= 8

        if dif < 0 and dif > dea:
            score += 6
            reasons.append("低位修复")

        if close > ma20:
            score += 12
            reasons.append("站上20日线")
        else:
            score -= 6

        if ma20 > ma60:
            score += 10
            reasons.append("均线多头")
        elif close < ma60:
            score -= 10

        if 0 <= ret20 <= 18:
            score += 12
            reasons.append(f"20日涨幅{ret20:+.1f}%")
        elif ret20 < -12:
            score -= 10
        elif ret20 > 30:
            score -= 12

        if 1.1 <= vol_ratio <= 3.0:
            score += 8
            reasons.append(f"温和放量{vol_ratio:.2f}x")
        elif vol_ratio > 5:
            score -= 8

        if -15 <= drawdown20 <= -2:
            score += 6
            reasons.append("高位回撤可控")

        trade_date = latest.get("trade_date", "")
        if hasattr(trade_date, "strftime"):
            trade_date = trade_date.strftime("%Y-%m-%d")

        return {
            "code": code,
            "score": score,
            "close": close,
            "ret20": ret20,
            "ret60": ret60,
            "vol_ratio": vol_ratio,
            "drawdown20": drawdown20,
            "macd_cross": macd_cross,
            "trade_date": str(trade_date),
            "reasons": reasons[:5] or ["无明显优势"],
        }

    # ── 工具4：本地自主选股 ──
    def _tool_auto_select_stocks(
        self,
        universe: str = "自选股优先+全市场抽样",
        max_candidates: int = 120,
        top_n: int = 8,
        add_to_watchlist: bool = False,
    ) -> str:
        from streamlit_app import get_stock_name_by_code, load_watchlist, save_watchlist

        try:
            max_candidates = int(max_candidates or 120)
        except Exception:
            max_candidates = 120
        try:
            top_n = int(top_n or 8)
        except Exception:
            top_n = 8

        max_candidates = max(10, min(max_candidates, 1000))
        top_n = max(1, min(top_n, 30))
        days = 180
        reader, codes, source_note, total_available = self._build_auto_select_universe(universe, max_candidates)

        if not codes:
            return "本地自主选股未找到可扫描股票。请先检查自选股或TDX数据目录。"

        start_date = (date.today() - timedelta(days=days * 3)).strftime("%Y%m%d")
        scored = []
        scanned = 0
        skipped = 0

        for code in codes:
            try:
                df = reader.get_stock_data(code, start_date=start_date)
                if df is None or df.empty:
                    df = reader.get_stock_data(code)
                scanned += 1
                item = self._score_stock_candidate(code, df)
                if item:
                    scored.append(item)
                else:
                    skipped += 1
            except Exception:
                skipped += 1

        if not scored:
            return (
                "本地自主选股没有得到有效候选。"
                f"\n范围: {source_note}"
                f"\n扫描: {scanned} 只，跳过: {skipped} 只，TDX可用股票: {total_available} 只"
            )

        scored.sort(key=lambda x: x["score"], reverse=True)
        top = scored[:top_n]

        added_count = 0
        if add_to_watchlist:
            try:
                import streamlit as st
                watchlist = list(st.session_state.get("watchlist", load_watchlist()))
            except Exception:
                watchlist = list(load_watchlist())
            existing = {self._normalize_stock_code(item.get("code")) for item in watchlist if isinstance(item, dict)}
            for item in top:
                if item["code"] not in existing:
                    watchlist.append({"code": item["code"], "name": get_stock_name_by_code(item["code"])})
                    existing.add(item["code"])
                    added_count += 1
            save_watchlist(watchlist)
            try:
                st.session_state.watchlist = watchlist
            except Exception:
                pass

        named_top = []
        lines = [
            "=== 本地自主选股结果 ===",
            f"范围: {universe} | {source_note}",
            f"扫描: {scanned}/{len(codes)} 只 | 有效: {len(scored)} 只 | 跳过: {skipped} 只 | TDX可用: {total_available} 只",
            f"说明: 当前为轻量评分，适合先缩小候选池；完整MACD底背离仍可到TAB6执行。",
            "",
        ]
        for idx, item in enumerate(top, 1):
            name = get_stock_name_by_code(item["code"])
            named_top.append({**item, "name": name})
            reasons = "、".join(item["reasons"])
            cross = "是" if item["macd_cross"] else "否"
            lines.append(
                f"{idx}. {name}({item['code']}) | 评分 {item['score']:.1f} | "
                f"收盘 {item['close']:.2f} | 20日 {item['ret20']:+.1f}% | "
                f"60日 {item['ret60']:+.1f}% | 量比 {item['vol_ratio']:.2f} | "
                f"金叉 {cross} | {reasons}"
            )

        if added_count:
            lines.append(f"\n已自动加入自选股: {added_count} 只")

        lines.append("\n下一步建议: 对Top候选逐只调用 get_kline_analysis，再结合筹码集中度和虚拟盘风控决定是否交易。")

        try:
            memory = AgentMemory()
            memory.add_screen_history({
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "universe": universe,
                "max_candidates": max_candidates,
                "top_n": top_n,
                "scanned": scanned,
                "valid": len(scored),
                "top": named_top[:10],
            })
            memory.save_long_term()
        except Exception:
            pass

        return "\n".join(lines)

    # ── 工具4：筹码集中度 ──
    def _tool_get_concentration_analysis(self, code: str) -> str:
        from shareholder_analyzer import get_shareholder_history
        result = get_shareholder_history(code, years=3)
        if result is None:
            return f"无法获取 {code} 的筹码集中度数据"

        records = result.get("records", [])
        if not records:
            return f"{code} 无股东人数记录"

        lines = [f"=== {code} 筹码集中度分析 ==="]
        # 显示最近4个季度
        for r in records[:4]:
            lines.append(
                f"- 日期:{r.get('end_date', '?')} "
                f"股东人数:{r.get('holder_count', '?')} "
                f"户均持股:{r.get('avg_shares', '?')} "
            )

        stats = result.get("stats", {})
        if stats:
            change_pct = stats.get("latest_change_pct")
            if change_pct is not None:
                direction = "减少↑" if change_pct < 0 else "增加↓"
                lines.append(f"\n最近一期股东人数变化: {change_pct:.1f}% ({direction})")
                lines.append("筹码集中度" + ("升高（利好）" if change_pct < 0 else "下降（需警惕）"))

        return "\n".join(lines)

    # ── 工具5：加入自选 ──
    def _tool_add_to_watchlist(self, code: str) -> str:
        import streamlit as st
        from streamlit_app import load_watchlist, save_watchlist, get_stock_name_by_code

        watchlist = st.session_state.get("watchlist", load_watchlist())
        # 检查是否已存在
        for item in watchlist:
            if item.get("code") == code:
                return f"{code} 已在自选股列表中"

        name = get_stock_name_by_code(code)
        watchlist.append({"code": code, "name": name})
        st.session_state.watchlist = watchlist
        save_watchlist(watchlist)
        return f"✅ 已将 {name}({code}) 加入自选股"

    # ── 工具6：移出自选 ──
    def _tool_remove_from_watchlist(self, code: str) -> str:
        import streamlit as st
        from streamlit_app import load_watchlist, save_watchlist

        watchlist = st.session_state.get("watchlist", load_watchlist())
        original_len = len(watchlist)
        watchlist = [w for w in watchlist if w.get("code") != code]
        if len(watchlist) == original_len:
            return f"{code} 不在自选股列表中"
        st.session_state.watchlist = watchlist
        save_watchlist(watchlist)
        return f"✅ 已将 {code} 从自选股移除"

    # ── 工具7：实时行情 ──
    def _tool_get_realtime_quotes(self, codes: list) -> str:
        from virtual_portfolio import get_realtime_price
        if not codes:
            return "请提供至少一个股票代码"
        results = []
        for code in codes[:10]:  # 最多10只
            info = get_realtime_price(code)
            if info:
                results.append(f"- {info.get('name', code)}({code}): 现价={info['price']}, 涨跌幅={info.get('change_pct', 'N/A')}%, 成交量={info.get('volume', 'N/A')}")
            else:
                results.append(f"- {code}: 无行情数据")
        return "\n".join(results)

    # ── 工具8：买入 ──
    def _tool_buy_stock(self, code: str, shares: int) -> str:
        import streamlit as st
        from virtual_portfolio import get_realtime_price, get_or_create_portfolio
        from streamlit_app import get_stock_name_by_code

        portfolio = get_or_create_portfolio()
        price_info = get_realtime_price(code)
        if not price_info:
            return f"无法获取 {code} 的实时价格，无法买入"

        price = price_info["price"]
        name = price_info.get("name", get_stock_name_by_code(code))
        total_asset = portfolio.get_total_asset({code: price})

        # 风控检查
        allowed, reason = self.risk_guard.check_buy(code, name, shares, price, portfolio, total_asset)
        if not allowed:
            return reason

        success, msg = portfolio.buy(code, name, shares, price)
        if success:
            portfolio.save()
        return msg

    # ── 工具9：卖出 ──
    def _tool_sell_stock(self, code: str, shares: int) -> str:
        import streamlit as st
        from virtual_portfolio import get_realtime_price, get_or_create_portfolio
        from streamlit_app import get_stock_name_by_code

        portfolio = get_or_create_portfolio()
        if code not in portfolio.positions:
            return f"持仓中没有 {code}"

        price_info = get_realtime_price(code)
        if not price_info:
            return f"无法获取 {code} 的实时价格，无法卖出"

        price = price_info["price"]
        name = price_info.get("name", get_stock_name_by_code(code))
        total_asset = portfolio.get_total_asset({code: price})

        # shares=0 表示全部卖出
        if shares == 0:
            shares = portfolio.positions[code]["shares"]

        # 风控检查
        allowed, reason = self.risk_guard.check_sell(code, name, shares, price, portfolio, total_asset)
        if not allowed:
            return reason

        success, msg = portfolio.sell(code, name, shares, price)
        if success:
            portfolio.save()
        return msg

    # ── 工具10：持仓状态 ──
    def _tool_get_portfolio_status(self) -> str:
        from virtual_portfolio import get_realtime_price, get_or_create_portfolio

        portfolio = get_or_create_portfolio()
        if not portfolio.positions:
            lines = [
                f"=== 虚拟盘持仓 ===",
                f"总资产: {portfolio.cash:,.2f}（全现金）",
                f"现金: {portfolio.cash:,.2f}",
            ]
            return "\n".join(lines)

        # 获取所有持仓股的价格
        price_map = {}
        for code in portfolio.positions:
            info = get_realtime_price(code)
            if info:
                price_map[code] = info["price"]

        total_asset = portfolio.get_total_asset(price_map)
        total_pnl = portfolio.get_pnl(price_map)
        total_pnl_pct = portfolio.get_pnl_pct(price_map)

        lines = [
            f"=== 虚拟盘持仓 ===",
            f"总资产: {total_asset:,.2f}",
            f"现金: {portfolio.cash:,.2f}",
            f"累计盈亏: {total_pnl:+,.2f} ({total_pnl_pct:+.2f}%)",
            f"",
            f"持仓明细：",
        ]

        for code, pos in portfolio.positions.items():
            price = price_map.get(code, 0)
            mkt_val = pos["shares"] * price
            pnl = (price - pos["avg_cost"]) * pos["shares"]
            pnl_pct = (price - pos["avg_cost"]) / pos["avg_cost"] * 100 if pos["avg_cost"] > 0 else 0
            name = get_realtime_price(code).get("name", code) if price else code
            lines.append(
                f"  {name}({code}): {pos['shares']}股 @ 成本{pos['avg_cost']:.3f} "
                f"现价{price:.2f} 市值{mkt_val:,.0f} 盈亏{pnl:+,.0f}({pnl_pct:+.1f}%)"
            )

        return "\n".join(lines)

    # ── 工具11：交易记录 ──
    def _tool_get_trade_history(self, days: int = 7) -> str:
        from virtual_portfolio import get_or_create_portfolio
        from datetime import timedelta

        portfolio = get_or_create_portfolio()
        if not portfolio.trade_history:
            return "暂无交易记录"

        cutoff = datetime.now() - timedelta(days=days)
        recent = []
        for t in reversed(portfolio.trade_history):
            try:
                trade_dt = datetime.strptime(t["date"], "%Y-%m-%d %H:%M:%S")
                if trade_dt >= cutoff:
                    recent.append(t)
            except Exception:
                recent.append(t)

        if not recent:
            return f"最近{days}天内无交易记录"

        lines = [f"=== 最近{days}天交易记录 ({len(recent)}笔) ==="]
        for t in recent[-10:]:  # 最多显示10笔
            lines.append(
                f"  [{t['date']}] {t['type']} {t.get('name', '?')}({t['code']}) "
                f"{t['shares']}股@{t['price']} 盈亏={t.get('pnl', 'N/A')}"
            )
        return "\n".join(lines)

    # ── 工具12：大盘概况 ──
    def _tool_get_market_overview(self) -> str:
        from virtual_portfolio import get_realtime_price
        indices = {
            "上证指数": "000001",
            "深证成指": "399001",
            "创业板指": "399006",
        }
        lines = ["=== 大盘概况 ==="]
        for name, code in indices.items():
            try:
                info = get_realtime_price(code)
                if info:
                    lines.append(f"{name}({code}): {info['price']:.2f} 涨跌{info.get('change_pct', 0):+.2f}%")
                else:
                    lines.append(f"{name}({code}): 数据不可用")
            except Exception:
                lines.append(f"{name}({code}): 获取失败")
        return "\n".join(lines)

    # ── 工具14：保存经验 ──
    def _tool_save_learning(self, note: str, category: str = "经验") -> str:
        note = str(note or "").strip()
        category = str(category or "经验").strip()
        if not note:
            return "未提供可保存的经验内容"
        memory = AgentMemory()
        memory.add_learned_rule(f"[{category}] {note}")
        memory.save_long_term()
        return f"✅ 已写入长期记忆: [{category}] {note}"


# ═══════════════════════════════════════════════════════
#  记忆系统 (Memory System)
# ═══════════════════════════════════════════════════════

AGENT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_data")
os.makedirs(AGENT_DIR, exist_ok=True)
AGENT_MEMORY_FILE = os.path.join(AGENT_DIR, "agent_memory.json")


class AgentMemory:
    """
    Agent 记忆系统 — 短期推理链 + 长期策略经验

    短期记忆：当前循环的推理步骤，每次循环结束后清空
    长期记忆：跨会话持久化的策略经验和教训，存 JSON 文件
    """

    def __init__(self):
        self.short_term: List[dict] = []  # 当前循环推理链
        self.long_term: dict = self._load_long_term()

    @staticmethod
    def _safe_score(value: Any) -> float:
        try:
            return float(value or 0)
        except Exception:
            return 0.0

    def _load_long_term(self) -> dict:
        """从文件加载长期记忆"""
        if os.path.exists(AGENT_MEMORY_FILE):
            try:
                with open(AGENT_MEMORY_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "strategy_performance": {},  # {策略名: {trades, win_rate, avg_return}}
            "learned_rules": [],         # 从经验中学到的规则
            "daily_logs": [],            # 每日操作日志
            "interactions": [],           # 对话摘要，用于后续追踪
            "screen_history": [],         # 自主选股历史
            "blacklist_notes": {},       # {code: 原因} 黑名单说明
        }

    def save_long_term(self):
        """持久化长期记忆"""
        # 只保留最近30天的日志
        if "daily_logs" in self.long_term:
            self.long_term["daily_logs"] = self.long_term["daily_logs"][-30:]
        if "interactions" in self.long_term:
            self.long_term["interactions"] = self.long_term["interactions"][-100:]
        if "screen_history" in self.long_term:
            self.long_term["screen_history"] = self.long_term["screen_history"][-50:]
        if "learned_rules" in self.long_term:
            self.long_term["learned_rules"] = self.long_term["learned_rules"][-100:]
        with open(AGENT_MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(self.long_term, f, ensure_ascii=False, indent=2)

    # ── 短期记忆 ──
    def add_step(self, step: int, action: str, result: str, reasoning: str = ""):
        """添加一步推理记录"""
        self.short_term.append({
            "step": step,
            "action": action,
            "result": result[:500],  # 截断太长的结果
            "reasoning": reasoning,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
        })

    def clear_short_term(self):
        """清空短期记忆"""
        self.short_term = []

    def get_short_term_summary(self) -> str:
        """将短期记忆格式化为 LLM 可读的摘要"""
        if not self.short_term:
            return "（本循环尚未执行任何操作）"
        lines = []
        for s in self.short_term:
            lines.append(f"[{s['timestamp']}][步骤{s['step']}] {s['action']}\n  结果: {s['result']}")
            if s.get("reasoning"):
                lines.append(f"  推理: {s['reasoning']}")
        return "\n".join(lines)

    # ── 长期记忆 ──
    def add_daily_log(self, date_str: str, summary: str, trades: list, pnl: float):
        """添加每日日志"""
        self.long_term.setdefault("daily_logs", []).append({
            "date": date_str,
            "summary": summary,
            "trades": trades,
            "pnl": pnl,
        })

    def add_interaction(self, user_message: str, answer: str, steps: Optional[List[dict]] = None):
        """保存一次对话摘要，让Agent能跨会话回看自己的判断。"""
        self.long_term.setdefault("interactions", []).append({
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "user": str(user_message or "")[:500],
            "answer": str(answer or "")[:1200],
            "steps": list(steps or [])[-8:],
        })

    def add_screen_history(self, summary: dict):
        """保存一次自主选股结果。"""
        self.long_term.setdefault("screen_history", []).append(summary)

    def update_strategy_performance(self, strategy: str, result: dict):
        """更新策略表现"""
        perf = self.long_term.setdefault("strategy_performance", {})
        if strategy not in perf:
            perf[strategy] = {"trades": 0, "wins": 0, "total_return": 0}
        perf[strategy]["trades"] += result.get("trades", 1)
        perf[strategy]["wins"] += result.get("wins", 0)
        perf[strategy]["total_return"] += result.get("return", 0)
        perf[strategy]["win_rate"] = perf[strategy]["wins"] / max(perf[strategy]["trades"], 1)
        perf[strategy]["avg_return"] = perf[strategy]["total_return"] / max(perf[strategy]["trades"], 1)

    def add_learned_rule(self, rule: str):
        """添加经验规则"""
        rules = self.long_term.setdefault("learned_rules", [])
        if rule not in rules:
            rules.append(rule)

    def get_long_term_prompt(self) -> str:
        """将长期记忆格式化为 LLM system prompt 的一部分"""
        parts = []

        # 策略表现
        perf = self.long_term.get("strategy_performance", {})
        if perf:
            parts.append("=== 策略表现 ===")
            for name, data in perf.items():
                parts.append(f"  {name}: 胜率={data.get('win_rate', 0):.1%} 平均收益={data.get('avg_return', 0):.2%} 共{data.get('trades', 0)}笔")

        # 学到的规则
        rules = self.long_term.get("learned_rules", [])
        if rules:
            parts.append("\n=== 经验教训 ===")
            for i, rule in enumerate(rules[-10:], 1):  # 最近10条
                parts.append(f"  {i}. {rule}")

        # 最近自主选股
        screen_history = self.long_term.get("screen_history", [])
        if screen_history:
            latest = screen_history[-1]
            parts.append("\n=== 最近一次自主选股 ===")
            parts.append(
                f"  时间: {latest.get('timestamp', '?')} "
                f"范围: {latest.get('universe', '?')} "
                f"扫描: {latest.get('scanned', 0)} 有效: {latest.get('valid', 0)}"
            )
            for item in latest.get("top", [])[:5]:
                score = self._safe_score(item.get("score", 0))
                parts.append(f"  - {item.get('name', '?')}({item.get('code', '?')}) 评分={score:.1f}")

        # 黑名单备注
        blacklist = self.long_term.get("blacklist_notes", {})
        if blacklist:
            parts.append("\n=== 黑名单备注 ===")
            for code, reason in blacklist.items():
                parts.append(f"  {code}: {reason}")

        return "\n".join(parts) if parts else "（暂无历史经验数据）"


# ═══════════════════════════════════════════════════════
#  Agent 主类 — 自主决策循环
# ═══════════════════════════════════════════════════════

AGENT_SYSTEM_PROMPT = """你是"A股分析助手"，一个专业的A股市场分析AI Agent。

你的职责：
1. 独立分析市场走势和个股表现
2. 通过工具搜索、筛选、分析股票
3. 基于分析结果做出买卖决策（虚拟盘）
4. 持续跟踪持仓并执行止损/止盈
5. 每日复盘，总结经验教训

工作流程（ReAct循环）：
- Observation：通过工具获取数据
- Analysis：分析数据，推理判断
- Decision：做出操作决策
- Action：执行买卖操作
- Reflection：反思策略效果

风险控制铁律（不可绕过）：
- 单票仓位不超过总资产20%
- 总仓位不超过80%
- 止损线-5%，止盈线+10%
- 每日最多10笔交易
- 至少保留20万现金储备

注意事项：
- 你分析的是虚拟盘，初始资金100万
- A股T+1规则：今天买的股票，明天才能卖
- 所有股票以100股（1手）为最小交易单位
- 回答用中文，数据用具体数字说话
- 如果数据不足以判断，明确说出来，不要猜
- 需要选股时优先调用 auto_select_stocks 缩小候选池，再对Top候选调用 get_kline_analysis 深入分析
- 需要沉淀经验时调用 save_learning，把具体可执行的规则写入长期记忆
- 快速模式下系统不会预先抓取所有实时数据；需要大盘或持仓实时信息时主动调用工具

当前日期：{current_date}
大盘概况：{market_overview}
持仓状态：{portfolio_status}
自选股：{watchlist}
历史经验：{memory_prompt}"""


class AIAgent:
    """
    自主决策循环 Agent

    使用方式：
        agent = AIAgent()
        result = agent.run("帮我分析今天的市场，找出3只值得关注的股票")
        print(result)
    """

    def __init__(self, model_name: str = None, api_key: str = None, base_url: str = None):
        self.llm = LLMClient(model_name=model_name, api_key=api_key, base_url=base_url)
        self.tools = _build_tool_schemas()
        self.executor = ToolExecutor(risk_guard=RiskGuard())
        self.memory = AgentMemory()
        self.tool_schema_map = {t["function"]["name"]: t for t in self.tools}

    @staticmethod
    def _message_mentions_any(message: str, keywords: List[str]) -> bool:
        text = str(message or "").lower()
        return any(keyword.lower() in text for keyword in keywords)

    def run(
        self,
        user_message: str,
        on_step: Callable = None,
        eager_context: bool = False,
        max_iterations: int = 6,
    ) -> str:
        """
        执行一次 Agent 循环

        Args:
            user_message: 用户指令（如"分析今天的市场"）
            on_step: 每步回调 step_callback(step, action, detail)
            eager_context: 是否在首轮请求前主动抓取大盘和持仓实时数据
            max_iterations: 最大工具调用轮数

        Returns:
            Agent 最终回复
        """
        # 构建系统提示词（注入实时数据）
        import streamlit as st
        from virtual_portfolio import get_or_create_portfolio, get_realtime_price

        max_iterations = max(1, min(int(max_iterations or 6), 12))
        need_market_context = eager_context or self._message_mentions_any(
            user_message,
            ["大盘", "市场", "指数", "全市场", "选股", "扫描", "复盘"],
        )
        need_portfolio_context = eager_context or self._message_mentions_any(
            user_message,
            ["持仓", "账户", "虚拟盘", "买", "卖", "止损", "止盈", "复盘", "仓位"],
        )

        # 大盘概况按需获取，避免每次对话都等待实时行情接口。
        if need_market_context:
            market_overview = self.executor._tool_get_market_overview()
        else:
            market_overview = "快速模式：未预抓取；需要时请调用 get_market_overview。"

        # 获取持仓状态
        portfolio = get_or_create_portfolio()
        if need_portfolio_context:
            price_map = {}
            for code in portfolio.positions:
                info = get_realtime_price(code)
                if info:
                    price_map[code] = info["price"]

            if portfolio.positions and price_map:
                total_asset = portfolio.get_total_asset(price_map)
                total_pnl = portfolio.get_pnl(price_map)
                portfolio_status = f"总资产{total_asset:,.0f} 盈亏{total_pnl:+,.0f} 持仓{len(portfolio.positions)}只"
            else:
                portfolio_status = f"全仓现金 {portfolio.cash:,.0f}"
        else:
            portfolio_status = (
                f"快速模式：持仓{len(portfolio.positions)}只，现金{portfolio.cash:,.0f}；"
                "需要实时盈亏时请调用 get_portfolio_status。"
            )

        # 获取自选股
        try:
            watchlist = st.session_state.get("watchlist", [])
        except Exception:
            watchlist = []
        watchlist_str = ", ".join([f"{w.get('name', '?')}({w.get('code', '?')})" for w in watchlist[:10]]) if watchlist else "空"

        # 构建完整系统提示
        system_prompt = AGENT_SYSTEM_PROMPT.format(
            current_date=datetime.now().strftime("%Y-%m-%d %H:%M"),
            market_overview=market_overview,
            portfolio_status=portfolio_status,
            watchlist=watchlist_str,
            memory_prompt=self.memory.get_long_term_prompt(),
        )

        # 清空短期记忆
        self.memory.clear_short_term()

        # 执行 Function Calling 循环
        result = self.llm.run_agent_loop(
            system_prompt=system_prompt,
            user_message=user_message,
            tools=self.tools,
            tool_executor=self._execute_tool,
            max_iterations=max_iterations,
            on_step=on_step,
        )

        # 写入长期记忆，形成“提问 → 工具 → 结论”的可追踪闭环。
        self.memory.add_interaction(user_message, result, self.memory.short_term)
        self.memory.save_long_term()
        return result

    def _execute_tool(self, tool_name: str, arguments: dict) -> str:
        """执行工具并记录到短期记忆"""
        # 风控拦截：买入和卖出需要额外风控
        # (ToolExecutor 内部已包含风控检查)
        result = self.executor.execute(tool_name, arguments)
        step = len(self.memory.short_term) + 1
        self.memory.add_step(
            step=step,
            action=f"调用 {tool_name}({json.dumps(arguments, ensure_ascii=False)})",
            result=result,
        )
        return result

    def daily_review(self, on_step: Callable = None) -> str:
        """
        每日盘后复盘

        Returns:
            复盘总结
        """
        from virtual_portfolio import get_or_create_portfolio
        portfolio = get_or_create_portfolio()

        # 获取持仓盈亏
        position_summary = []
        for code, pos in portfolio.positions.items():
            from virtual_portfolio import get_realtime_price
            info = get_realtime_price(code)
            if info:
                pnl_pct = (info["price"] - pos["avg_cost"]) / pos["avg_cost"] * 100
                position_summary.append(f"{info.get('name', code)}({code}) 持仓{pos['shares']}股 盈亏{pnl_pct:+.1f}%")

        positions_str = "\n".join(position_summary) if position_summary else "空仓"

        review_prompt = f"""请对今天的操作进行复盘总结。

当前持仓：
{positions_str}

请分析：
1. 今天哪些操作是正确的，为什么？
2. 哪些操作有问题，应该如何改进？
3. 对明天的操作有什么建议？
4. 有哪些经验教训应该记录下来？"""

        result = self.run(review_prompt, on_step=on_step)

        # 记录到长期记忆
        today = date.today().isoformat()
        self.memory.add_daily_log(
            date_str=today,
            summary=result[:200],
            trades=[],
            pnl=0,
        )
        self.memory.save_long_term()

        return result


# ═══════════════════════════════════════════════════════
#  便捷导出
# ═══════════════════════════════════════════════════════

__all__ = [
    "LLMClient",
    "ToolExecutor",
    "RiskGuard",
    "AgentMemory",
    "AIAgent",
    "MODEL_REGISTRY",
    "RISK_RULES",
    "_build_tool_schemas",
]
