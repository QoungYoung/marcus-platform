# -*- coding: utf-8 -*-
"""theme_sleeve_monitor.py — 题材持仓统一监视器（双向触发）。

向下跌破 down_trigger → 清仓
向上触及 up_trigger  → 减半
点位来自 wolf_v3_production 的 agent；不设固定百分比。
"""
from __future__ import annotations
import json, os, sys, threading, time
from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "data")
POS_FILE = os.path.join(DATA, "theme_sleeve_positions.json")
STATE_FILE = os.path.join(DATA, "theme_sleeve_exit_state.json")


def _canon_symbol(sym: str) -> str:
    """SH600118.SH / SH600118 / 600118.SH → 600118.SH。"""
    s = str(sym or "").upper().replace(" ", "")
    for p in ("SH", "SZ", "BJ"):
        if s.startswith(p):
            s = s[2:]
            break
    if "." in s:
        return s
    if s.startswith(("6", "9", "5")):
        return s + ".SH"
    if s.startswith(("4", "8", "92")):
        return s + ".BJ"
    return s + ".SZ"


class ThemeSleeveMonitor:
    def __init__(self, executor=None, interval: int = 30):
        self.executor = executor
        self.interval = interval
        self.running = False
        self.thread = None
        self._levels_cache = {}
        self._lock = threading.Lock()

    def start(self):
        if self.running:
            return False
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True, name="theme-sleeve-monitor")
        self.thread.start()
        print("[theme-monitor] started interval=%ss" % self.interval, file=sys.stderr)
        return True

    def stop(self):
        self.running = False

    def _pos(self):
        """读 PG 持仓（上下位在 position_levels，不在 JSON）。"""
        try:
            import psycopg2
            c = psycopg2.connect(os.environ["DATABASE_URL"]); cur = c.cursor()
            cur.execute("SELECT symbol,volume,avg_price FROM paper_positions WHERE account_id=%s AND volume>0", ("stock",))
            rows = [{"symbol": _canon_symbol(r[0]), "volume": int(r[1]), "avg_price": float(r[2])} for r in cur.fetchall()]
            c.close()
            return {r["symbol"]: r for r in rows}
        except Exception:
            return {}

    def _state(self):
        try:
            return json.load(open(STATE_FILE, encoding="utf-8"))
        except Exception:
            return {}

    def _save_state(self, s):
        try:
            json.dump(s, open(STATE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except Exception:
            pass

    @staticmethod
    def _now():
        # 统一按交易所时区判断，避免容器 TZ=UTC 时窗口整体错位
        return datetime.now(ZoneInfo("Asia/Shanghai")) if ZoneInfo else datetime.now()

    def _trading(self):
        now = self._now()
        if now.weekday() >= 5:
            return False
        hm = now.strftime("%H:%M")
        return ("09:30" <= hm <= "11:30") or ("13:00" <= hm <= "15:00")

    @classmethod
    def _exec_window(cls):
        """只在狼大窗口卖出：09:45-10:00 / 14:00-14:30。"""
        hm = cls._now().strftime("%H:%M")
        return ("09:45" <= hm <= "10:00") or ("14:00" <= hm <= "14:30")

    @staticmethod
    def _limit_price(pre: float, symbol: str) -> float:
        from app.services.exit_rule_engine import _limit_price
        return _limit_price(pre, symbol)

    def _down_break_confirmed(self, q: dict, px: float, down: float, symbol: str) -> bool:
        """跌破确认：非涨停/封板附近，且跌破 0.3% 缓冲，避免一分钱假破。"""
        try:
            pre = float((q or {}).get("pre_close") or 0)
            lim = self._limit_price(pre, symbol) if pre > 0 else 0.0
            if lim > 0 and (px >= lim - 0.001 or float((q or {}).get("high") or 0) >= lim - 0.001):
                return False
            return px < float(down) * 0.997
        except Exception:
            return False

    def _quote(self, symbol):
        try:
            from app.services.t_data_sources import fetch_quote_one
            q = fetch_quote_one(symbol) or {}
            return q, float(q.get("current") or 0)
        except Exception:
            return {}, 0.0

    def _levels(self, account_id: str = "stock"):
        try:
            from app.services.position_levels_store import load
            return load(account_id)
        except Exception:
            return {}

    def _sell(self, symbol, px, vol, reason):
        # 唯一卖出出口也做窗口兜底：即使调用方绕过 _loop，也不允许 09:30 成交
        if not self._exec_window():
            return {"status": "blocked", "reason": "outside_theme_window",
                    "window": "09:45-10:00,14:00-14:30",
                    "now": self._now().strftime("%Y-%m-%d %H:%M:%S")}
        try:
            if self.executor is None:
                return {"status": "no_executor"}
            return self.executor.sell(symbol, px, vol, reason=reason)
        except Exception as e:
            return {"status": "error", "error": str(e)[:160]}

    AB_SLOTS = ("09:45","10:15","10:45","11:15","13:15","13:45","14:15","14:45")
    _last_ab_slot = None

    def _ensure_ab_table(self):
        try:
            import psycopg2
            with psycopg2.connect(os.environ["DATABASE_URL"]) as c:
                with c.cursor() as cur:
                    cur.execute("""
                    create table if not exists theme_ab_trigger (
                      trade_date date not null,
                      slot text not null,
                      status text not null default 'pending',
                      created_at timestamptz default now(),
                      primary key (trade_date, slot)
                    )""")
        except Exception:
            pass

    def _maybe_trigger_ab(self):
        """每 30 分钟入队一次 A/B 触发；不阻塞监控循环。"""
        now = self._now()
        if now.weekday() >= 5:
            return
        hm = now.strftime("%H:%M")
        if hm not in self.AB_SLOTS or hm == self._last_ab_slot:
            return
        self._last_ab_slot = hm
        try:
            import psycopg2
            self._ensure_ab_table()
            with psycopg2.connect(os.environ["DATABASE_URL"]) as c:
                with c.cursor() as cur:
                    cur.execute("""
                    insert into theme_ab_trigger(trade_date,slot,status)
                    values(%s,%s,'pending') on conflict do nothing
                    """, (now.strftime("%Y-%m-%d"), hm))
            print(f"[theme-monitor] AB trigger {now.strftime('%Y-%m-%d')} {hm}", file=sys.stderr)
        except Exception as e:
            print(f"[theme-monitor] AB trigger fail {str(e)[:100]}", file=sys.stderr)

    def _loop(self):
        time.sleep(8)
        while self.running:
            try:
                self._maybe_trigger_ab()
                if self._trading() and self._exec_window():
                    pos = self._pos()
                    levels = self._levels("stock")
                    st = self._state()
                    day = datetime.now().strftime("%Y%m%d")
                    done = st.setdefault(day, {})
                    for sym, p in pos.items():
                        if done.get(sym):
                            continue
                        lv = levels.get(sym) or {}
                        if lv.get("down") is None and lv.get("up") is None:
                            continue
                        q, px = self._quote(sym)
                        if px <= 0:
                            continue
                        vol = int(p.get("volume") or 0)
                        if vol < 100:
                            continue
                        down, up = lv.get("down"), lv.get("up")
                        if down and px < down:
                            if not self._down_break_confirmed(q, px, float(down), sym):
                                continue
                            r = self._sell(sym, px, vol, "theme_monitor:down_break")
                            done[sym] = {"action": "clear", "px": px, "result": r, "t": datetime.now().isoformat()}
                        elif up and px >= up:
                            # 跳空/快速越过压力位 >3%：按突破处理，不按“压力位”卖；等下一次点位刷新
                            if px > float(up) * 1.03:
                                continue
                            half = (vol // 2 // 100) * 100
                            if half >= 100:
                                r = self._sell(sym, px, half, "theme_monitor:up_resistance")
                                done[sym] = {"action": "half", "px": px, "result": r, "t": datetime.now().isoformat()}
                    self._save_state(st)
            except Exception as e:
                print("[theme-monitor] error %s: %s" % (type(e).__name__, str(e)[:120]), file=sys.stderr)
            time.sleep(self.interval)


_monitor = None
_lock = threading.Lock()


def start_theme_monitor(executor=None, interval: int = 30):
    global _monitor
    with _lock:
        if _monitor is None:
            _monitor = ThemeSleeveMonitor(executor=executor, interval=interval)
        elif executor is not None and _monitor.executor is None:
            _monitor.executor = executor
        return _monitor.start()
