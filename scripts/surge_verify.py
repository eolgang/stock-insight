#!/usr/bin/env python3
"""표본 검증: 파이프라인과 다른 코드 경로(순수 pandas 루프)로 15사례 재계산해 오차 0 확인."""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

REPO = Path(__file__).resolve().parent.parent
CACHE = REPO.parent / "work" / "backtest" / "cache"
OHLCV = CACHE / "ohlcv"
WORK = REPO.parent / "work" / "backtest"
SLIP, COMM = 0.001, 0.00015
TAX = {2016: .0030, 2017: .0030, 2018: .0030, 2019: .0030, 2020: .0025, 2021: .0023,
       2022: .0023, 2023: .0020, 2024: .0018, 2025: .0015, 2026: .0020}


def recompute(code: str, month: str) -> dict:
    d = pd.read_pickle(OHLCV / f"{code}.pkl")
    c = d["aclose"].astype(float).to_numpy()
    o = d["aopen"].astype(float).to_numpy()
    h = d["ahigh"].astype(float).to_numpy()
    v = d["volume"].astype(float).to_numpy()
    dates = d.index
    # 그 달 마지막 거래일 인덱스, 전월 마지막 거래일 = T
    y, m = int(month[:4]), int(month[5:7])
    in_month = (dates.year == y) & (dates.month == m)
    idx_m = np.flatnonzero(in_month)
    M_end = idx_m[-1]
    prev = np.flatnonzero((dates.year * 12 + dates.month) < (y * 12 + m))
    T = prev[-1]
    out = {}
    out["ret"] = c[M_end] / c[T] - 1
    series_c = pd.Series(c)
    for n in (20, 60, 120):
        ma = series_c.rolling(n).mean()
        out[f"disp{n}"] = c[T] / ma.iloc[T] - 1
    ma = {n: series_c.rolling(n).mean() for n in (5, 20, 60)}
    out["align3"] = float(ma[5].iloc[T] > ma[20].iloc[T] > ma[60].iloc[T])
    out["win_ret"] = c[T] / c[T - 15] - 1
    out["v10_20"] = v[T - 9:T + 1].mean() / v[T - 19:T + 1].mean()
    # 15일 내 250일 고점(직전) 돌파
    brk = 0.0
    for k in range(0, 16):
        i = T - k
        prior_hi = h[i - 250:i].max()
        prev_prior_hi = h[i - 251:i - 1].max()
        if c[i] > prior_hi and c[i - 1] <= prev_prior_hi:
            brk = 1.0
            break
    out["br_h250"] = brk
    e = T + 1
    cost = 2 * SLIP + 2 * COMM + TAX[int(dates[e].year)]
    for hd in (20, 30, 40):
        out[f"r{hd}"] = c[e + hd - 1] / o[e] - 1 - cost
    return out


def main():
    df = pd.read_pickle(WORK / "surge_only_cases.pkl")
    cnt = df["code"].value_counts()
    picks = [c for c in cnt.index if cnt[c] >= 3][:5]
    keys = ["ret", "disp20", "disp60", "disp120", "align3", "win_ret", "v10_20", "br_h250",
            "r20", "r30", "r40"]
    rows, maxdiff = [], {}
    for code in picks:
        sub = df[df["code"] == code].sort_values("M")
        for _, r in sub.head(4).iterrows():
            rec = recompute(code, r["M"])
            for k in keys:
                a = float(r[k]) if not isinstance(r[k], (bool, np.bool_)) else float(bool(r[k]))
                b = float(rec[k])
                if k == "br_h250":
                    # 저장값은 '15일 내 돌파 있음'을 며칠 전으로 기록, 재계산은 0/1
                    a = 1.0 if np.isfinite(a) else 0.0
                diff = abs(a - b)
                maxdiff[k] = max(maxdiff.get(k, 0.0), diff)
                rows.append({"code": code, "name": r["name"], "M": r["M"], "item": k,
                             "stored": round(a, 8), "recomputed": round(b, 8), "diff": diff})
    v = pd.DataFrame(rows)
    n_cases = v[["code", "M"]].drop_duplicates().shape[0]
    print(f"검증: {len(picks)}종목 · {n_cases}사례 · {len(v)}개 항목")
    print(v.groupby("item")["diff"].agg(["max", "mean"]).to_string())
    print("최대 오차:", max(maxdiff.values()))
    print(v[v["diff"] > 1e-9].to_string())
    pd.DataFrame([{"cases": n_cases, "items": len(v), "max_abs_diff": max(maxdiff.values()),
                   "by_item": {k: float(x) for k, x in maxdiff.items()}}]).to_json(
        WORK / "surge_only_verify.json", orient="records", force_ascii=False, indent=1)


if __name__ == "__main__":
    main()
