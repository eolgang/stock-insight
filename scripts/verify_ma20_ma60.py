#!/usr/bin/env python3
"""표본 수동 재계산 — 파이프라인과 다른 코드 경로(순수 pandas + 명시적 for 루프).

3종목의 규칙 신호를 독립적으로 다시 찾아 20일 순수익을 계산하고 저장된 신호 테이블과 비교한다.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
from ma_support_rebound import CACHE, OHLCV, WORK, TAX_BY_YEAR, SLIP, COMM, MIN_MKTCAP, MIN_VALUE

S = pd.read_pickle(WORK / "ma20_ma60_signals.pkl")
uni = pd.read_pickle(CACHE / "universe.pkl")
idx = pd.read_pickle(CACHE / "index.pkl")

codes = ["267260", "005930", "247540"]
codes = [c for c in (S.groupby("code").size().sort_values(ascending=False).index[:3].tolist()
                     if not codes else codes) if c in set(S.code)]
rows = []
for code in codes:
    row = uni[uni.code == code].iloc[0]
    shares = row["mktcap_eok"] * 1e8 / row["last_close"]
    d = pd.read_pickle(OHLCV / f"{code}.pkl")
    df = pd.DataFrame({
        "open": d["aopen"].astype(float), "high": d["ahigh"].astype(float),
        "low": d["alow"].astype(float), "close": d["aclose"].astype(float),
        "vol": d["volume"].astype(float), "turn": d["turnover"].astype(float),
    })
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma60"] = df["close"].rolling(60).mean()
    df["disp20"] = df["close"] / df["ma20"] - 1
    df["disp60"] = df["close"] / df["ma60"] - 1
    df["t20"] = df["turn"].rolling(20).mean()
    df["mktcap"] = df["close"] * shares
    df["d52"] = df["close"] / df["high"].rolling(250).max() - 1
    n = len(df)
    trades = []
    for i in range(n - 21):
        r = df.iloc[i]
        if not (i >= df.index.get_loc(df.index[df.index >= "2016-01-01"][0])):
            continue
        if not np.isfinite(r.disp20) or not np.isfinite(r.disp60) or not np.isfinite(r.d52):
            continue
        if not (r.mktcap >= MIN_MKTCAP and r.t20 >= MIN_VALUE and r.vol > 0):
            continue
        if not (-0.02 <= r.disp20 < 0.02):
            continue
        if not (r.disp60 < -0.10):
            continue
        e = i + 1
        entry = df["open"].iloc[e]
        exit_px = df["close"].iloc[e + 19]
        yr = df.index[e + 19].year   # 파이프라인과 동일하게 청산일 연도 세율
        cost = 2 * SLIP + 2 * COMM + TAX_BY_YEAR.get(yr, 0.0020)
        trades.append({"date": df.index[i], "entry_date": df.index[e],
                       "r20_manual": exit_px / entry - 1 - cost})
    t = pd.DataFrame(trades)
    sub = S[S.code == code][["date", "entry_date", "r20"]].copy()
    sub["date"] = pd.to_datetime(sub["date"]); sub["entry_date"] = pd.to_datetime(sub["entry_date"])
    sub = sub[sub["r20"].notna()]          # 데이터 끝을 넘어 20일 창이 없는 신호는 제외
    m = t.merge(sub, on="entry_date", how="outer", indicator=True)
    diff = (m["r20_manual"] - m["r20"]).abs()
    rows.append({"code": code, "n_manual": len(t), "n_pipeline": int(len(sub)),
                 "matched": int((m["_merge"] == "both").sum()),
                 "max_abs_diff": float(np.nanmax(diff)) if len(m) else None,
                 "mean_manual": float(t["r20_manual"].mean()) if len(t) else None,
                 "mean_pipeline": float(sub["r20"].mean()) if len(sub) else None})

out = pd.DataFrame(rows)
print(out.to_string(index=False))
print("합계 수동 n:", out["n_manual"].sum(), " 파이프라인 n:", out["n_pipeline"].sum(),
      " 최대 오차:", out["max_abs_diff"].max())
WORK.joinpath("ma20_ma60_verify.json").write_text(out.to_json(orient="records", force_ascii=False, indent=1))
