# -*- coding: utf-8 -*-
"""systemic_risk selftest：银行双头+科技不反 → 防御；大光破位大黑K → 彻底止盈"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import systemic_risk as sr

def bank_double():
    s = []
    for i in range(100): s.append(5.0 + i * 0.02)
    for i, v in ((55, 9.4), (65, 10.0), (75, 9.5), (80, 9.9), (88, 9.3), (99, 9.55)):
        s[i] = v
    return s

def main():
    r = sr.evaluate(bank_close=bank_double(), tech_close=100.0, tech_ma20=105.0)
    assert r["level"] == 1, r
    r2 = sr.evaluate(bank_close=bank_double(), tech_close=110.0, tech_ma20=105.0)
    assert r2["level"] == 0, r2
    r3 = sr.evaluate(optics={"300308.SZ": {"close": 96.0, "open": 100.0, "prev_low": 97.0}})
    assert r3["level"] == 2, r3
    r4 = sr.evaluate(bank_close=list(range(100)), tech_close=110.0, tech_ma20=100.0)
    assert r4["level"] == 0, r4
    print("SELFTEST OK 4/4")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
