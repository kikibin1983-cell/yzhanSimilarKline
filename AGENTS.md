# AGENTS.md — yzhanSimilarKline (A股K线智能分析系统)

## What this is

A monolithic Streamlit dashboard for A-share (China) stock analysis — K-line similarity matching, MACD divergence scanning, shareholder concentration analysis, virtual portfolio trading, and an AI Agent that orchestrates all modules via Function Calling plus a local autonomous stock-selection loop.

**Target user**: Chinese-speaking retail investor using Windows + local TDX (通达信) data.

## Run

```powershell
# Activate venv and launch
venv\Scripts\python.exe -m streamlit run streamlit_app.py --server.port 8501 --server.address localhost --browser.gatherUsageStats false
```

Or just double-click `启动.bat`.

**.env**:
```
API_KEY=your_deepseek_api_key    # required for LLM chat in AI Agent tab
API_BASE=https://api.deepseek.com
MODEL=deepseek-v4-pro
PROXY_URL=                       # leave empty for DeepSeek (direct in China)
```

The AI Agent tab also supports custom OpenAI-compatible services from the UI. For local services such as Ollama or LM Studio, fill a dummy key such as `local`, the service `Base URL`, and the real model id.

## Architecture

**Single-file monolithic app**: `streamlit_app.py` (~9840 lines) — everything UI. **Zero page routing or multipage** — Streamlit `st.tabs()` only.

**8 tabs** (line 6568):
| Tab | Purpose | Key Module |
|-----|---------|------------|
| 证券搜索 | Stock/index/ETF search + basic info | inline |
| 技术分析 | K-line similarity + trend prediction | inline + `technical_cache/` |
| 🤖 AI 智能体 | Agent with function calling, local autonomous stock selection, memory, risk guard | `ai_agent.py` |
| ⭐ 我的自选股 | Watchlist with realtime quotes + intraday charts | `watchlist.json`, `virtual_portfolio.py` |
| 📋 历史回测验证 | Backtest similar K-line patterns | `strategy/backtest.py` |
| 📈 MACD底背离筛选 | Full-market MACD divergence scan | `macd_divergence_scanner.py` |
| 🏦 筹码集中度 | Shareholder concentration analysis | `shareholder_analyzer.py` |
| 💰 虚拟盘 | Virtual portfolio (buy/sell/track) | `virtual_portfolio.py` |

**Core modules** (modular, imported by the monolith):
- `ai_agent.py` — LLMClient, ToolExecutor (14 tools), RiskGuard (7 rules), AgentMemory, AIAgent
- `strategy/` — BaseStrategy, SimilarityStrategy (K-line pattern strategy), BacktestEngine (event-driven)
- `virtual_portfolio.py` — Portfolio class, JSON persistence, akshare realtime quotes
- `shareholder_analyzer.py` — akshare shareholder data + concentration scoring
- `tdx_reader.py` — Reads TDX (通达信) `.day` binary files from local disk
- `batch_scanner.py` — Streaming/parallel stock data reader
- `stock_preload_service.py` — Background async preloader for scanning performance
- `macd_divergence_scanner.py` — Full-market MACD bottom-divergence detector

**Data sources**:
1. **TDX local files** (`D:\new_tdx\vipdoc\sz\lday\*.day` / `sh\lday\*.day`) — primary K-line source
2. **akshare** — realtime quotes, shareholder data, index constituents
3. **sina (via akshare)** — realtime spot market for virtual portfolio

## Critical Gotchas

### Streamlit `session_state` widget key collision
**DO NOT** use the same key for both a widget and manual `session_state` mutation.
```python
# WRONG — button key IS the state key, then you try to flip it
st.button("👁", key=f"show_key_{provider_name}")
st.session_state[f"show_key_{provider_name}"] = not ...  # 💥 StreamlitAPIException

# CORRECT — separate widget key from state key
st.button("👁", key=f"show_key_btn_{provider_name}")
st.session_state[f"show_key_visible_{provider_name}"] = not ...
```

### `UnboundLocalError: datetime` in `main()`
Any `from datetime import datetime` **inside** `main()` shadows the module-level datetime import, causing `UnboundLocalError` on the module-level reference. Keep imports at the top only.

### DeepSeek V4 `reasoning_content` handling
DeepSeek V4 returns `reasoning_content` in streaming chunks. The app disables thinking mode by default (`thinking: {type: "disabled"}`) to avoid the `"reasoning_content" must be passed back` error. The code still preserves the reasoning_content passback path for when thinking is enabled.

### akshare proxy conflict
`shareholder_analyzer.py` and `virtual_portfolio.py` both use a `_no_proxy()` context manager because `.env` may have `PROXY_URL` set, which breaks akshare's direct API calls. **Always** wrap akshare calls when proxy env vars exist.

### TDX data dependency
`tdx_reader.py` hardcodes `D:\new_tdx\vipdoc` — the stock data comes from a locally installed copy of the 通达信 trading software. Without this directory, K-line data falls back to akshare API (slower, network-dependent).

### Numba optional
Performance-critical similarity calculations use Numba JIT when available. Falls back to pure NumPy. Import is wrapped in try/except — don't add hard numba imports.

### Model registry pattern
New LLM models are added by inserting one entry into `MODEL_REGISTRY` dict in `ai_agent.py`. Each model is tied to a `provider_name`, and API keys are stored per-provider in `session_state.agent_api_keys`. The UI also has a custom OpenAI-compatible option, so one-off providers can be tested without editing code.

### AI Agent fast path
TAB2/TAB3 Agent chat defaults to fast mode: it does not pre-fetch market overview or realtime portfolio prices unless the user message needs them. The model can still call `get_market_overview` or `get_portfolio_status` on demand. Local autonomous selection uses `auto_select_stocks`, reads TDX `.day` data directly, scores candidates locally, persists `screen_history`, and can add Top results to `watchlist.json`.

### Chinese text processing
Uses `jieba` tokenizer with LRU caching. `TfidfVectorizer` + `TruncatedSVD` for text chunk vectorization. These are only used in the legacy RAG-based Q&A (TAB3 pre-Agent), now mostly superseded by the AI Agent's direct tool calls.

## Cache System

Multiple overlapping caching layers — be aware when debugging stale data:

| Cache | Storage | TTL | Purpose |
|-------|---------|-----|---------|
| `SimilarityCache` (in-memory LRU) | RAM, max 512MB | Session | Stock features, search results |
| `TfidfVectorizer` / `jieba` LRU | RAM | Session | Tokenization results |
| `file_cache` decorator | `securities_cache/`, `search_cache/` | 1 day | API responses (akshare) |
| `_indicator_cache` | `technical_cache/` | Persistent | Pre-computed MACD, KDJ etc. |
| `shareholder_cache/` JSON | Disk | 1 day | Shareholder data |
| Sina spot market cache | RAM (global var) | 5 min | Realtime quotes |
| `stock_preload_service` | RAM | Session | Full stock data in memory |

## Testing

No formal test suite. Files named `test_*.py` are manual verification scripts, not automated tests:
- `test_tabs.py`, `test_tabs_fixed.py`, `test_tabs_issue.py` — Streamlit tab rendering checks
- `simple_test.py` — Basic API connection smoke test
- `performance_test.py` — Scan speed benchmark
- `test_intraday.py` — Intraday chart rendering

**To verify changes**: Run `python -c "import py_compile; py_compile.compile('streamlit_app.py', doraise=True)"` for syntax, then launch the app and manually test the affected tab. There is no `pytest`, `unittest`, or CI.

## Update Log

### 2026-05-17 — AI Agent TAB refactor
- Added OpenAI-compatible model presets and a UI path for custom providers.
- Split AI Agent into two usable paths: local autonomous stock selection without LLM, and LLM-driven function-calling analysis.
- Added `auto_select_stocks` and `save_learning` tools.
- Reduced chat latency by switching default prompt context to on-demand data fetching.
- Persisted interaction summaries and autonomous screen history in `agent_data/agent_memory.json`.

## Build & Distribution

- `启动.bat` — Development launch
- `installer/` — Portable and EXE installer build scripts
- `分发版本/` — Distribution-ready copies
- `A股K线智能分析系统_便携版/` — Portable distribution with bundled Python

**Requirements**: `requirements.txt` (pinned: streamlit 1.54, pandas 2.2.3, plotly 5.24, etc.)
