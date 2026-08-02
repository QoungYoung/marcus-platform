# -*- coding: utf-8 -*-
"""港股份额 vs 恒生贪婪指数 vs 恒生ETF价格 关联性分析

数据源:
  1. alla.json series[6] (513130 恒生科技指数) — 每日 {close, date, greed}
  2. alla.json series[7] (513600 恒生指数)     — 每日 {close, date, greed}
  3. global-capital-flow API — hong_kong share 每日份额曲线
  4. Tushare fund_daily — 513130/513600 ETF K线

输出:
  - 皮尔逊 & 斯皮尔曼相关系数矩阵
  - 滚动相关性
  - 领先/滞后交叉相关分析
"""
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from scipy import stats
from scipy.stats import pearsonr, spearmanr

# ── 路径 & 环境设置 ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

# 加载 .env
env_path = PROJECT_ROOT / ".env"
if env_path.exists():
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value

# ── 1. 从 alla.json 提取恒生 greed 时间序列 ──
def load_alla_hk_series():
    alla_path = PROJECT_ROOT / "data" / "arkvol" / "alla.json"
    with open(alla_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    inner = data.get("data", data)
    items = inner.get("items", [])
    series_list = inner.get("series", [])

    # 找到 513130 (恒生科技) 和 513600 (恒生指数) 在 items 中的索引
    idx_513130 = idx_513600 = None
    for i, item in enumerate(items):
        fc = str(item.get("fund_code", ""))
        if fc == "513130":
            idx_513130 = i
        elif fc == "513600":
            idx_513600 = i

    if idx_513130 is None or idx_513600 is None:
        print("ERROR: 未在 alla.json 中找到 513130/513600")
        return None, None

    print(f"513130 恒生科技指数 位于 series[{idx_513130}], items[{idx_513130}]:")
    print(f"  name={items[idx_513130].get('index_name')}, greed={items[idx_513130].get('greed')}, date={items[idx_513130].get('date')}")
    print(f"513600 恒生指数 位于 series[{idx_513600}], items[{idx_513600}]:")
    print(f"  name={items[idx_513600].get('index_name')}, greed={items[idx_513600].get('greed')}, date={items[idx_513600].get('date')}")

    # 解析时间序列: {date: {greed, close}}
    def parse_series(idx, label):
        raw = series_list[idx]
        result = {}
        for pt in raw:
            d = pt["date"]
            result[d] = {
                "greed": float(pt["greed"]),
                "close": float(pt["close"]),
            }
        print(f"{label}: {len(result)} 个交易日, {min(result.keys())} ~ {max(result.keys())}")
        return result

    s513130 = parse_series(idx_513130, "513130 恒生科技")
    s513600 = parse_series(idx_513600, "513600 恒生指数")
    return s513130, s513600


# ── 2. 从 global-capital-flow 获取 hong_kong share 时间序列 ──
def load_gcf_hk_share():
    """优先读缓存; 无有效 series 则实时请求 ArkVol API"""
    gcf_path = PROJECT_ROOT / "data" / "arkvol" / "global-capital-flow.json"

    # 尝试缓存
    if gcf_path.exists():
        with open(gcf_path, "r", encoding="utf-8") as f:
            gcf = json.load(f)
        inner = gcf.get("data", gcf)
        series = inner.get("series", [])
        if series and len(series) >= 10:
            result = {}
            for s in series:
                d = s.get("date", "")
                shares = s.get("shares", {})
                hk = shares.get("hong_kong")
                if d and hk is not None:
                    result[d] = float(hk)
            if len(result) >= 10:
                print(f"GCF hong_kong share (缓存): {len(result)} 天, {min(result.keys())} ~ {max(result.keys())}")
                return result

    # 实时请求
    print("GCF 缓存 series 不足，实时请求 ArkVol API...")
    try:
        import requests
        resp = requests.get("https://arkvol.com/api/data/global-capital-flow", params={"view": "full"}, timeout=30)
        resp.raise_for_status()
        gcf = resp.json()
        inner = gcf.get("data", gcf)
        series = inner.get("series", [])
        result = {}
        for s in series:
            d = s.get("date", "")
            shares = s.get("shares", {})
            hk = shares.get("hong_kong")
            if d and hk is not None:
                result[d] = float(hk)
        print(f"GCF hong_kong share (实时): {len(result)} 天, {min(result.keys())} ~ {max(result.keys())}")
        return result
    except Exception as e:
        print(f"获取 GCF 数据失败: {e}")
        return {}


# ── 3. 从 Tushare 获取 ETF K线 ──
def load_etf_kline(ts_code):
    """获取 ETF 日K线, 返回 {date: close}"""
    try:
        from app.core.trading._api_config import get_tushare_pro
        pro = get_tushare_pro()
        # 近一年数据
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=400)).strftime("%Y%m%d")

        df = pro.fund_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df is None or df.empty:
            # 尝试 SZ
            alt_code = ts_code.replace(".SH", ".SZ")
            df = pro.fund_daily(ts_code=alt_code, start_date=start_date, end_date=end_date)

        if df is None or df.empty:
            print(f"Tushare 未返回 {ts_code} 数据")
            return {}

        result = {}
        for _, row in df.iterrows():
            d = row.get("trade_date", "")
            close = row.get("close", 0)
            if d and close:
                d = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
                result[d] = float(close)
        print(f"ETF K线 {ts_code}: {len(result)} 天, {min(result.keys())} ~ {max(result.keys())}")
        return result
    except Exception as e:
        print(f"获取 {ts_code} K线失败: {e}")
        return {}


# ── 4. 数据对齐 & 关联性分析 ──
def align_series(*named_series):
    """对齐多个时间序列到共同的日期集合。

    Args:
        named_series: (name, {date: value}) 元组序列
    Returns:
        dates: 排序后的共同日期列表
        aligned: {name: [values]}
    """
    # 找共同日期
    date_sets = [set(s[1].keys()) for s in named_series]
    common_dates = sorted(set.intersection(*date_sets)) if date_sets else []
    if not common_dates:
        print("WARNING: 没有共同日期，使用各序列最大重叠")
        # 使用至少出现在两个序列中的日期
        from collections import Counter
        all_dates = Counter()
        for ds in date_sets:
            all_dates.update(ds)
        common_dates = sorted(d for d, c in all_dates.items() if c >= 2)

    print(f"\n共同日期: {len(common_dates)} 天, {common_dates[0]} ~ {common_dates[-1]}")

    aligned = {}
    for name, series in named_series:
        aligned[name] = [series.get(d, np.nan) for d in common_dates]

    return common_dates, aligned


def run_correlation_analysis(dates, values_dict):
    """全面的相关性分析"""
    names = list(values_dict.keys())
    n = len(names)

    # 提取有效数据
    arrays = {}
    for name in names:
        arr = np.array(values_dict[name])
        arrays[name] = arr[~np.isnan(arr)]

    # 皮尔逊 & 斯皮尔曼相关矩阵
    print("\n" + "=" * 90)
    print("  皮尔逊 (Pearson) 相关系数矩阵")
    print("=" * 90)
    header = f"{'':>20s}" + "".join(f"{n:>16s}" for n in names)
    print(header)
    print("-" * len(header))

    pearson_matrix = {}
    spearman_matrix = {}
    for i, n1 in enumerate(names):
        row_str = f"{n1:>20s}"
        for j, n2 in enumerate(names):
            common = ~(np.isnan(values_dict[n1]) | np.isnan(values_dict[n2]))
            a = np.array(values_dict[n1])[common]
            b = np.array(values_dict[n2])[common]
            if len(a) < 5:
                r, p = np.nan, np.nan
            else:
                r, p = pearsonr(a, b)
            pearson_matrix[(n1, n2)] = (r, p, len(a))
            row_str += f"{r:>16.4f}"
        print(row_str)
        # 显著性
        sig_row = f"{'':>20s}"
        for j, n2 in enumerate(names):
            _, p, _ = pearson_matrix[(n1, n2)]
            sig = "***" if p < 0.001 else ("**" if p < 0.01 else ("*" if p < 0.05 else ""))
            sig_row += f"{sig:>16s}"
        print(sig_row)

    print("\n" + "=" * 90)
    print("  斯皮尔曼 (Spearman) 秩相关系数矩阵")
    print("=" * 90)
    print(header)
    print("-" * len(header))
    for i, n1 in enumerate(names):
        row_str = f"{n1:>20s}"
        for j, n2 in enumerate(names):
            common = ~(np.isnan(values_dict[n1]) | np.isnan(values_dict[n2]))
            a = np.array(values_dict[n1])[common]
            b = np.array(values_dict[n2])[common]
            if len(a) < 5:
                r, p = np.nan, np.nan
            else:
                r, p = spearmanr(a, b)
            spearman_matrix[(n1, n2)] = (r, p, len(a))
            row_str += f"{r:>16.4f}"
        print(row_str)
        sig_row = f"{'':>20s}"
        for j, n2 in enumerate(names):
            _, p, _ = spearman_matrix[(n1, n2)]
            sig = "***" if p < 0.001 else ("**" if p < 0.01 else ("*" if p < 0.05 else ""))
            sig_row += f"{sig:>16s}"
        print(sig_row)
    print("  *** p<0.001  ** p<0.01  * p<0.05")

    return pearson_matrix, spearman_matrix


def run_cross_correlation(dates, values_dict, max_lag=20):
    """领先/滞后交叉相关分析: 港股份额变化 vs N日后恒生greed/价格变化"""
    print("\n" + "=" * 90)
    print("  交叉相关分析: 港股份额日变化 vs N日后目标变化")
    print("=" * 90)

    hk_shares = np.array(values_dict.get("港股份额(hk_share)", []))
    target_names = [n for n in values_dict.keys() if n != "港股份额(hk_share)"]

    # 计算日变化
    hk_diff = np.diff(hk_shares)

    all_results = {}
    for name in target_names:
        target = np.array(values_dict[name])
        target_diff = np.diff(target)  # 日变化
        target_pct = target_diff / target[:-1] * 100  # 日收益率 (%)

        print(f"\n--- 港股份额日变化 vs {name} ---")

        results = []
        for lag in range(0, max_lag + 1):
            # lag=0: 同一天; lag=1: hk变化领先1天
            if lag == 0:
                common_len = min(len(hk_diff), len(target_pct))
                a = hk_diff[-common_len:]
                b = target_pct[-common_len:]
            else:
                # hk_diff[i] 领先 target_diff[i+lag]
                common_len = min(len(hk_diff) - lag, len(target_pct))
                if common_len <= 5:
                    continue
                a = hk_diff[:common_len]
                b = target_pct[lag:lag + common_len]

            if len(a) < 10:
                continue
            r, p = pearsonr(a, b)
            results.append((lag, r, p))

        # 输出 top 相关
        if results:
            # 找最强相关
            best = max(results, key=lambda x: abs(x[1]))
            print(f"  最强相关: lag={best[0]}天, r={best[1]:.4f}, p={best[2]:.4f}")

            # 显示 lag=0,1,3,5,10,20
            show_lags = [l for l in [0, 1, 3, 5, 10, 20] if l <= max_lag]
            for lag in show_lags:
                item = next((x for x in results if x[0] == lag), None)
                if item:
                    print(f"  lag={item[0]:>2d}天: r={item[1]:.4f}, p={item[2]:.4f}")

        all_results[name] = results

    return all_results


def run_rolling_correlation(dates, values_dict, window=60):
    """60天滚动相关分析"""
    print(f"\n{'=' * 90}")
    print(f"  滚动 {window} 天相关性: 港股份额 vs 恒生贪婪指数")
    print(f"{'=' * 90}")

    hk_key = "港股份额(hk_share)"
    if hk_key not in values_dict:
        print("  无港股份额数据")
        return

    hk = np.array(values_dict[hk_key])
    for name in values_dict:
        if name == hk_key:
            continue
        target = np.array(values_dict[name])
        n = len(hk)
        rolling_corrs = []
        for i in range(window, n + 1):
            a = hk[i - window:i]
            b = target[i - window:i]
            valid = ~(np.isnan(a) | np.isnan(b))
            if valid.sum() < 20:
                rolling_corrs.append(np.nan)
            else:
                r, _ = pearsonr(a[valid], b[valid])
                rolling_corrs.append(r)

        valid_corrs = [c for c in rolling_corrs if not np.isnan(c)]
        if valid_corrs:
            avg_corr = np.mean(valid_corrs)
            min_corr = np.min(valid_corrs)
            max_corr = np.max(valid_corrs)
            recent = valid_corrs[-20:] if len(valid_corrs) >= 20 else valid_corrs
            recent_avg = np.mean(recent)

            # 相关性时间趋势
            half = len(valid_corrs) // 2
            first_half_avg = np.mean(valid_corrs[:half]) if half > 0 else np.nan
            second_half_avg = np.mean(valid_corrs[half:]) if half > 0 else np.nan
            trend = "增强" if second_half_avg > first_half_avg + 0.05 else ("减弱" if second_half_avg < first_half_avg - 0.05 else "稳定")

            print(f"\n  {name}:")
            print(f"    平均相关: {avg_corr:.4f}  (范围: {min_corr:.4f} ~ {max_corr:.4f})")
            print(f"    近期(20天)平均: {recent_avg:.4f}")
            print(f"    前半段: {first_half_avg:.4f}  后半段: {second_half_avg:.4f}  趋势: {trend}")


def run_distribution_analysis(dates, values_dict):
    """分析港股份额在不同区间下恒生贪婪/价格的表现"""
    print(f"\n{'=' * 90}")
    print(f"  分布分析: 港股份额分位 vs 恒生贪婪/价格表现")
    print(f"{'=' * 90}")

    hk_key = "港股份额(hk_share)"
    if hk_key not in values_dict:
        return

    hk = np.array(values_dict[hk_key])
    valid_mask = ~np.isnan(hk)
    hk_valid = hk[valid_mask]

    if len(hk_valid) < 30:
        return

    # 按港股份额分为 高/中/低 三组
    p33 = np.percentile(hk_valid, 33)
    p67 = np.percentile(hk_valid, 67)

    low_mask = hk_valid <= p33
    mid_mask = (hk_valid > p33) & (hk_valid <= p67)
    high_mask = hk_valid > p67

    print(f"\n  港股份额 分位: 低(≤{p33:.1f}%), 中({p33:.1f}%~{p67:.1f}%), 高(>{p67:.1f}%)")

    for name in values_dict:
        if name == hk_key:
            continue
        target = np.array(values_dict[name])[valid_mask]

        low_vals = target[low_mask]
        mid_vals = target[mid_mask]
        high_vals = target[high_mask]

        print(f"\n  {name}:")
        print(f"    低份额区间: 均值={np.mean(low_vals):.4f}, 中位数={np.median(low_vals):.4f}, std={np.std(low_vals):.4f}")
        print(f"    中份额区间: 均值={np.mean(mid_vals):.4f}, 中位数={np.median(mid_vals):.4f}, std={np.std(mid_vals):.4f}")
        print(f"    高份额区间: 均值={np.mean(high_vals):.4f}, 中位数={np.median(high_vals):.4f}, std={np.std(high_vals):.4f}")

        # 高 vs 低的差异检验
        if len(low_vals) >= 5 and len(high_vals) >= 5:
            t_stat, t_p = stats.ttest_ind(high_vals, low_vals)
            print(f"    t-test (高vs低): t={t_stat:.4f}, p={t_p:.4f}")


def main():
    print("=" * 90)
    print("  港股份额 (GCF) vs 恒生贪婪指数 (ArkVol) vs 恒生ETF价格 关联性分析")
    print(f"  分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 90)

    # ── 加载数据 ──
    print("\n[1/4] 加载 alla.json 恒生 greed 时间序列...")
    s513130, s513600 = load_alla_hk_series()

    print("\n[2/4] 加载 global-capital-flow hong_kong share 时间序列...")
    hk_share = load_gcf_hk_share()

    print("\n[3/4] 加载 Tushare ETF K线...")
    kline_513130 = load_etf_kline("513130.SH")
    kline_513600 = load_etf_kline("513600.SH")

    if not s513130 or not hk_share:
        print("\n数据不足，无法继续分析")
        return

    # ── 提取各序列值 ──
    # 从 alla series 提取 greed 和 close
    greed_513130 = {d: v["greed"] for d, v in s513130.items()}
    close_513130_alla = {d: v["close"] for d, v in s513130.items()}
    greed_513600 = {d: v["greed"] for d, v in s513600.items()}
    close_513600_alla = {d: v["close"] for d, v in s513600.items()}

    print("\n[4/4] 数据对齐...")
    named = [
        ("港股份额(hk_share)", hk_share),
        ("513130_greed(恒生科技贪婪)", greed_513130),
        ("513600_greed(恒生指数贪婪)", greed_513600),
        ("513130_close(恒生科技ETF价格)", kline_513130),
        ("513600_close(恒生指数ETF价格)", kline_513600),
    ]

    # 过滤掉空的系列
    named = [(n, s) for n, s in named if s and len(s) >= 10]

    dates, aligned = align_series(*named)

    if len(dates) < 30:
        print(f"\n共同日期仅 {len(dates)} 天，扩大对齐范围...")
        # 允许任意两个序列重叠
        from collections import Counter
        all_dates = Counter()
        for _, s in named:
            all_dates.update(s.keys())
        common_dates = sorted(d for d, c in all_dates.items() if c >= 2)
        dates, aligned = align_series(*named)

    # ── 分析 ──
    pearson_m, spearman_m = run_correlation_analysis(dates, aligned)

    run_cross_correlation(dates, aligned, max_lag=20)

    run_rolling_correlation(dates, aligned, window=60)

    run_distribution_analysis(dates, aligned)

    # ── 导出 CSV ──
    print("\n\n导出对齐后的数据到 data/hk_correlation_data.csv ...")
    csv_path = PROJECT_ROOT / "data" / "hk_correlation_data.csv"
    with open(csv_path, "w", encoding="utf-8") as f:
        headers = ["date"] + [n for n, _ in named]
        f.write(",".join(headers) + "\n")
        for i, d in enumerate(dates):
            vals = [d] + [f"{aligned[n][i]:.6f}" if not np.isnan(aligned[n][i]) else "" for n, _ in named]
            f.write(",".join(vals) + "\n")
    print(f"已导出到: {csv_path}")

    print("\n分析完成。")


if __name__ == "__main__":
    main()
