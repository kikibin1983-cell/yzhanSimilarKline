"""
虚拟交易盘核心模块

功能:
  1. 账户管理（初始资金、现金、持仓）
  2. 买入/卖出（含佣金和印花税）
  3. 持仓市值计算
  4. 交易记录
  5. 自选股实时价格获取

存储: JSON 文件 ./portfolio/portfolio.json
"""

import os
import json
import contextlib
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# ── 代理绕过 ──
@contextlib.contextmanager
def _no_proxy():
    """临时清除 HTTP/HTTPS 代理环境变量"""
    saved = {}
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        if key in os.environ:
            saved[key] = os.environ.pop(key)
    try:
        yield
    finally:
        for key, value in saved.items():
            os.environ[key] = value


# ── 全市场行情缓存（新浪数据源，避免重复慢请求）──
_sina_spot_cache = None
_sina_spot_cache_time = None
_SINA_CACHE_TTL = 300  # 5分钟有效


def _get_sina_spot():
    """获取全市场实时行情（新浪源，一次请求覆盖全部股票，缓存5分钟）"""
    global _sina_spot_cache, _sina_spot_cache_time
    now = datetime.now()
    if (_sina_spot_cache is not None and _sina_spot_cache_time is not None
            and (now - _sina_spot_cache_time).total_seconds() < _SINA_CACHE_TTL):
        return _sina_spot_cache
    try:
        import akshare as ak
        with _no_proxy():
            df = ak.stock_zh_a_spot()
        _sina_spot_cache = df
        _sina_spot_cache_time = now
        return df
    except Exception:
        return _sina_spot_cache  # 返回旧缓存


# ── 配置 ──
PORTFOLIO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolio")
PORTFOLIO_FILE = os.path.join(PORTFOLIO_DIR, "portfolio.json")

COMMISSION_RATE = 0.0001   # 佣金费率：万分之一
STAMP_TAX_RATE = 0.001    # 印花税：千分之一（仅卖出）

os.makedirs(PORTFOLIO_DIR, exist_ok=True)


# ═══════════════════════════════════════════════════════
#  数据模型
# ═══════════════════════════════════════════════════════

class Portfolio:
    """虚拟盘账户"""

    def __init__(self, initial_cash: float = 1_000_000):
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.positions: Dict[str, dict] = {}       # {code: {shares, avg_cost}}
        self.trade_history: List[dict] = []         # 交易记录
        self.daily_snapshots: List[dict] = []       # 每日净值快照
        self.created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── 序列化 ──

    def to_dict(self) -> dict:
        return {
            "initial_cash": self.initial_cash,
            "cash": self.cash,
            "positions": self.positions,
            "trade_history": self.trade_history,
            "daily_snapshots": self.daily_snapshots,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Portfolio":
        p = cls(initial_cash=data.get("initial_cash", 1_000_000))
        p.cash = data.get("cash", p.initial_cash)
        p.positions = data.get("positions", {})
        p.trade_history = data.get("trade_history", [])
        p.daily_snapshots = data.get("daily_snapshots", [])
        p.created_at = data.get("created_at", "")
        return p

    # ── 保存 / 加载 ──

    def save(self):
        with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls) -> Optional["Portfolio"]:
        if not os.path.exists(PORTFOLIO_FILE):
            return None
        try:
            with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls.from_dict(data)
        except Exception:
            return None

    # ── 交易操作 ──

    def buy(self, code: str, name: str, shares: int, price: float) -> Tuple[bool, str]:
        """
        买入股票

        Args:
            code: 股票代码
            name: 股票名称
            shares: 买入股数（100的整数倍）
            price: 成交价格

        Returns:
            (success, message)
        """
        if shares <= 0:
            return False, "股数必须大于0"
        if shares % 100 != 0:
            return False, "A股必须以手为单位交易（100股整数倍）"

        amount = shares * price
        commission = max(amount * COMMISSION_RATE, 5)  # 最低5元佣金
        total_cost = amount + commission

        if total_cost > self.cash:
            return False, f"资金不足！需要 {total_cost:.2f}，可用 {self.cash:.2f}"

        # 更新持仓
        if code in self.positions:
            pos = self.positions[code]
            old_shares = pos["shares"]
            old_cost = pos["avg_cost"]
            new_shares = old_shares + shares
            new_cost = (old_cost * old_shares + amount) / new_shares
            pos["shares"] = new_shares
            pos["avg_cost"] = round(new_cost, 3)
        else:
            self.positions[code] = {
                "shares": shares,
                "avg_cost": round(price, 3),
            }

        # 扣款
        self.cash -= total_cost

        # 记录
        self.trade_history.append({
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "code": code,
            "name": name,
            "type": "买入",
            "shares": shares,
            "price": round(price, 3),
            "amount": round(amount, 2),
            "commission": round(commission, 2),
            "stamp_tax": 0,
        })

        return True, f"✅ 买入成功！{name}({code}) {shares}股 @ {price:.2f}，佣金 {commission:.2f}"

    def sell(self, code: str, name: str, shares: int, price: float) -> Tuple[bool, str]:
        """
        卖出股票

        Args:
            code: 股票代码
            name: 股票名称
            shares: 卖出股数（0=全部卖出）
            price: 成交价格

        Returns:
            (success, message)
        """
        if code not in self.positions:
            return False, "持仓中没有该股票"

        pos = self.positions[code]
        held = pos["shares"]

        if shares <= 0:
            shares = held  # 默认全部卖出
        if shares > held:
            return False, f"持仓不足！持有 {held} 股，想卖出 {shares} 股"
        if shares % 100 != 0 and shares != held:
            return False, "A股必须以手为单位交易（100股整数倍），或全部卖出"

        amount = shares * price
        commission = max(amount * COMMISSION_RATE, 5)
        stamp_tax = amount * STAMP_TAX_RATE
        total_proceeds = amount - commission - stamp_tax

        # 更新持仓
        remaining = held - shares
        if remaining == 0:
            del self.positions[code]
        else:
            pos["shares"] = remaining

        # 入账
        self.cash += total_proceeds

        # 计算盈亏
        cost_basis = pos["avg_cost"] * shares
        pnl = amount - cost_basis - commission - stamp_tax

        self.trade_history.append({
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "code": code,
            "name": name,
            "type": "卖出",
            "shares": shares,
            "price": round(price, 3),
            "amount": round(amount, 2),
            "commission": round(commission, 2),
            "stamp_tax": round(stamp_tax, 2),
            "pnl": round(pnl, 2),
        })

        return True, f"✅ 卖出成功！{name}({code}) {shares}股 @ {price:.2f}，盈亏 {pnl:+.2f}"

    # ── 持仓市值计算 ──

    def get_position_value(self, code: str, current_price: float) -> float:
        """计算单只持仓市值"""
        if code not in self.positions:
            return 0.0
        return self.positions[code]["shares"] * current_price

    def get_total_market_value(self, price_map: Dict[str, float]) -> float:
        """计算总持仓市值"""
        total = 0.0
        for code, pos in self.positions.items():
            price = price_map.get(code, 0)
            total += pos["shares"] * price
        return total

    def get_total_asset(self, price_map: Dict[str, float]) -> float:
        """总资产 = 现金 + 持仓市值"""
        return self.cash + self.get_total_market_value(price_map)

    def get_pnl(self, price_map: Dict[str, float]) -> float:
        """累计盈亏 = 总资产 - 初始资金"""
        return self.get_total_asset(price_map) - self.initial_cash

    def get_pnl_pct(self, price_map: Dict[str, float]) -> float:
        """累计收益率"""
        if self.initial_cash == 0:
            return 0
        return self.get_pnl(price_map) / self.initial_cash * 100

    def reset(self, initial_cash: float = 1_000_000):
        """重置账户"""
        self.__init__(initial_cash)


# ═══════════════════════════════════════════════════════
#  实时价格获取（单只股票，速度快）
# ═══════════════════════════════════════════════════════

def get_realtime_price(code: str) -> Optional[dict]:
    """
    获取单只股票的实时价格

    优先 akshare 实时行情（最新），失败则回退 TDX 本地收盘价

    Returns:
        {"price": 12.50, "name": "平安银行", "change_pct": 1.5, "volume": 123456}
    """
    # 方案1：新浪全市场行情（一次请求覆盖全部股票，5分钟缓存，不受东方财富限流）
    try:
        df = _get_sina_spot()
        if df is not None and not df.empty:
            # 列0 格式: sh688518 / sz000001，匹配代码
            code_col = df.columns[0]
            match = df[df[code_col].astype(str).str.contains(code)]
            if not match.empty:
                row = match.iloc[0]
                close = float(row['最新价'])
                chg_pct = float(row.get('涨跌幅', 0))
                # 新浪数据是当天行情，日期取今天
                from datetime import date as _date
                return {
                    "price": round(close, 2),
                    "name": str(row.get('名称', code)),
                    "change_pct": round(chg_pct, 2),
                    "volume": int(row.get('成交量', 0)),
                    "source": "实时",
                    "date": _date.today().strftime("%m-%d"),
                }
    except Exception:
        pass

    # 方案2：TDX 本地数据（兜底，可能是 T-1 收盘价）
    try:
        from tdx_reader import read_tdx_data
        df = read_tdx_data(code=code, security_type="stock")
        if df is not None and not df.empty and "close" in df.columns:
            latest = df.iloc[-1]
            prev = df.iloc[-2] if len(df) >= 2 else latest
            change_pct = (latest["close"] - prev["close"]) / prev["close"] * 100 if prev["close"] > 0 else 0
            # TDX 数据日期取 trade_date 字段
            tdx_date = ""
            if "trade_date" in df.columns:
                td = latest["trade_date"]
                try:
                    # trade_date 可能是 Timestamp、str 或 date
                    from datetime import datetime as _dt
                    if hasattr(td, 'strftime'):
                        tdx_date = td.strftime("%m-%d")
                    else:
                        # str 格式如 "2026-05-14"
                        tdx_date = str(td)[-5:] if '-' in str(td) else str(td)[-5:]
                except Exception:
                    tdx_date = ""
            return {
                "price": round(float(latest["close"]), 2),
                "name": code,
                "change_pct": round(change_pct, 2),
                "volume": int(latest.get("volume", 0)),
                "source": "TDX",
                "date": tdx_date or "?",
            }
    except Exception:
        pass

    return None


def get_realtime_prices(codes: List[str]) -> Dict[str, dict]:
    """
    批量获取多只股票的实时价格

    Args:
        codes: 股票代码列表

    Returns:
        {code: {price, name, change_pct, ...}}
    """
    result = {}
    for code in codes:
        price_info = get_realtime_price(code)
        if price_info:
            result[code] = price_info
    return result


# ── 分时走势数据 ──
_intraday_cache: Dict[str, tuple] = {}  # {code: (timestamp, df)}
_INTRADAY_TTL = 120  # 2分钟缓存


def _code_to_sina_prefix(code: str) -> str:
    """将6位代码转为新浪前缀格式（sh/sz）"""
    if code.startswith(("6", "9")):
        return f"sh{code}"
    elif code.startswith(("0", "3")):
        return f"sz{code}"
    elif code.startswith("5"):
        return f"sh{code}"  # ETF
    return f"sz{code}"


def get_intraday_data(code: str) -> Optional[object]:
    """
    获取单只股票的分时走势数据（1分钟K线）

    Returns:
        pandas.DataFrame with columns: day, open, high, low, close, volume
        或 None（获取失败时）
    """
    now = datetime.now()
    # 检查缓存
    if code in _intraday_cache:
        ts, cached_df = _intraday_cache[code]
        if (now - ts).total_seconds() < _INTRADAY_TTL:
            return cached_df

    try:
        import akshare as ak
        symbol = _code_to_sina_prefix(code)
        with _no_proxy():
            df = ak.stock_zh_a_minute(symbol=symbol, period="1")
        if df is not None and not df.empty:
            # 只保留今天的数据
            today_str = now.strftime("%Y-%m-%d")
            if "day" in df.columns:
                df_today = df[df["day"].astype(str).str.startswith(today_str)]
                if not df_today.empty:
                    _intraday_cache[code] = (now, df_today)
                    return df_today
                # 非交易日或数据未更新：返回最近一天的数据
                df_last_day = df[df["day"].astype(str).str.startswith(df["day"].astype(str).iloc[-1][:10])]
                if not df_last_day.empty:
                    _intraday_cache[code] = (now, df_last_day)
                    return df_last_day
            # 没有 day 列，返回全部
            _intraday_cache[code] = (now, df)
            return df
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[get_intraday_data] Error for {code}: {e}")

    return None

    return None

def get_or_create_portfolio() -> Portfolio:
    """获取或创建虚拟盘账户"""
    portfolio = Portfolio.load()
    if portfolio is None:
        portfolio = Portfolio(initial_cash=1_000_000)
        portfolio.save()
    return portfolio


def add_watchlist_to_portfolio(watchlist: List[dict]) -> None:
    """从自选股列表导入到虚拟盘关注列表（不买入，仅快速查询价格）"""
    pass  # 自选股已在 session_state.watchlist 中，TAB8 直接读取
