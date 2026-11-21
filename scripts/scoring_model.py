#!/usr/bin/env python3
"""과제 2·3: 월말 스냅샷 패널 → 다중특징 점수 모델 + 조합 준전수 탐색 + 급등 전 '과정' 프로파일/국면.

사용자 지시(2026-11-21):
  - 매월 마지막 거래일 종가로 전 종목 점수화 → 다음 거래일 시가 매수 → 20/30거래일 보유
  - 학습 2016~2020 / 검증 2021~2026, 검증 성과만으로 판정
  - 특징 조합 2~5개 준전수 탐색, 학습 상위 50개의 검증 순위 유지 여부
  - 급등 전 20거래일 평균 프로파일(사건 vs 통제), 4국면 분해, 체크리스트, 예시 3건

출력: work/backtest/scoring_panel.pkl, data/scoring-model.json, work/backtest/scoring_portfolio_equity.csv,
      work/backtest/surge_profile.png, work/backtest/surge_phases_example.png, work/backtest/scoring_tables.md

사용법: python3 scripts/scoring_model.py --workers 2
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

REPO = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("BACKTEST_CACHE", REPO.parent / "work" / "backtest" / "cache"))
OHLCV = CACHE / "ohlcv"
WORK = REPO.parent / "work" / "backtest"
PANEL = WORK / "scoring_panel.pkl"

MIN_MKTCAP = 50_000_000_000
MIN_VALUE = 10_000_000_000
SLIP = 0.001
COMM = 0.00015
TAX_BY_YEAR = {2015: 0.0030, 2016: 0.0030, 2017: 0.0030, 2018: 0.0030, 2019: 0.0030,
               2020: 0.0025, 2021: 0.0023, 2022: 0.0023, 2023: 0.0020, 2024: 0.0018,
               2025: 0.0015, 2026: 0.0020}
HORIZONS = (5, 10, 20, 30, 40)
SURGE = 0.20
PREFERRED_RE = re.compile(r"(?:\d?우B?C?)$|우\(|우B$")
NON_COMMON_KEYWORDS = ("스팩", "리츠", "인프라", "ETN", "ETF", "선물", "레버리지", "인버스", "합성")

UNI = None
IDX = None


def init_worker(uni, idx):
    global UNI, IDX
    UNI, IDX = uni, idx


def reg_slope(y: np.ndarray, win: int = 10) -> np.ndarray:
    s = pd.Series(y, dtype=float)
    x = np.arange(win, dtype=float)
    x = x - x.mean()
    sxx = (x * x).sum()
    out = s.rolling(win).apply(lambda w: float((x * (w - w.mean())).sum() / sxx), raw=True)
    return (out / s.rolling(win).mean().replace(0, np.nan)).to_numpy()


def prep(d: pd.DataFrame, shares: float, idx: pd.DataFrame) -> pd.DataFrame:
    x = pd.DataFrame(index=d.index)
    c, o, h, l = d["aclose"].astype(float), d["aopen"].astype(float), d["ahigh"].astype(float), d["alow"].astype(float)
    v, tf = d["volume"].astype(float), d["turnover"].astype(float)
    x["c"], x["o"], x["h"], x["l"] = c, o, h, l
    for n in (5, 20, 30, 60, 120):
        m = c.rolling(n).mean()
        x[f"ma{n}"] = m
        x[f"disp{n}"] = c / m - 1
    x["align3"] = (x["ma5"] > x["ma20"]) & (x["ma20"] > x["ma60"])
    x["align4"] = x["align3"] & (x["ma60"] > x["ma120"])
    x["sl20"] = x["ma20"] / x["ma20"].shift(20) - 1
    x["sl30"] = x["ma30"] / x["ma30"].shift(20) - 1
    x["sl60"] = x["ma60"] / x["ma60"].shift(20) - 1
    x["gap"] = x["ma20"] / x["ma60"] - 1
    x["d52"] = c / h.rolling(250).max() - 1
    x["dh60"] = c / h.rolling(60).max() - 1
    x["depth10"] = c / h.rolling(10).max() - 1
    x["ret10"] = c.pct_change(10)
    x["ret20"] = c.pct_change(20)
    v20, v10, v5 = v.rolling(20).mean(), v.rolling(10).mean(), v.rolling(5).mean()
    x["vr"] = v / v20
    x["v520"] = v5 / v20
    x["v10_20"] = v10 / v20
    x["vs10"] = reg_slope(v.to_numpy())
    x["vr_max10"] = x["vr"].rolling(10).max()
    x["touch10"] = (x["disp20"].abs() <= 0.02).fillna(False).astype(float).rolling(10).sum()
    x["d20_chg5"] = x["disp20"] - x["disp20"].shift(5)
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    x["atr14"] = tr.rolling(14).mean() / c
    sd20 = c.rolling(20).std(ddof=0)
    x["bbw"] = 4 * sd20 / x["ma20"]
    x["bbwpct"] = x["bbw"].rolling(250).rank(pct=True)
    x["box20"] = (h.rolling(20).max() - l.rolling(20).min()) / c
    x["box60"] = (h.rolling(60).max() - l.rolling(60).min()) / c
    yang = (c > o).astype(float)
    x["yang5"] = yang.rolling(5).mean()
    x["yang10"] = yang.rolling(10).mean()
    body = (c - o).abs() / (h - l).replace(0, np.nan)
    x["body"] = body
    x["up_candle"] = (c > o) | (c > c.shift(1))
    fp = d["foreign_pct"].astype(float)
    x["f5"] = fp - fp.shift(5)
    x["f20"] = fp - fp.shift(20)
    x["mktcap"] = d["close"].astype(float) * shares
    x["lncap"] = np.log(x["mktcap"])
    x["t20"] = tf.rolling(20).mean()
    x["vol0"] = v > 0
    k = idx["close_k"].reindex(x.index).ffill()
    x["bull"] = (k > k.rolling(20).mean()).fillna(False)
    # 국면 판정용
    x["collect"] = ((x["ret10"].abs() <= 0.03) & (x["vs10"] > 0)).fillna(False)
    x["collect20"] = x["collect"].astype(float).rolling(20).max().fillna(0)
    x["cross20"] = (c > x["ma20"]) & (~(c > x["ma20"]).shift(1).fillna(True).astype(bool))
    return x


FEATS = ["disp5", "disp20", "disp30", "disp60", "disp120", "align3", "align4",
         "sl20", "sl30", "sl60", "gap", "d52", "dh60", "depth10", "ret10", "ret20",
         "vr", "v520", "v10_20", "vs10", "vr_max10", "touch10", "d20_chg5",
         "atr14", "bbw", "bbwpct", "box20", "box60", "yang5", "yang10", "body",
         "f5", "f20", "lncap", "bull"]


def work_one(code: str):
    row = UNI.loc[code]
    p = OHLCV / f"{code}.pkl"
    if not p.exists():
        return None
    d = pd.read_pickle(p)
    if d.empty or len(d) < 320:
        return None
    x = prep(d, float(row["shares"]), IDX)
    n = len(x)
    c = x["c"].to_numpy(float)
    o = x["o"].to_numpy(float)
    ma20 = x["ma20"].to_numpy(float)
    uni_ok = ((x["mktcap"].to_numpy(float) >= MIN_MKTCAP)
              & (x["t20"].to_numpy(float) >= MIN_VALUE) & (x["vol0"].to_numpy(bool))
              & np.isfinite(x["ma120"].to_numpy(float)) & np.isfinite(x["d52"].to_numpy(float))
              & np.isfinite(x["bbwpct"].to_numpy(float)))
    dates = x.index
    pm = dates.year.to_numpy() * 12 + dates.month.to_numpy()
    nlast = np.r_[np.flatnonzero(pm[:-1] != pm[1:]), n - 1]
    tax = np.array([TAX_BY_YEAR.get(int(y), 0.0020) for y in dates.year])
    cols = {f: x[f].to_numpy(float) for f in FEATS}
    prof_src = {f: x[f].to_numpy(float) for f in ("disp20", "disp60", "vr", "yang5", "atr14")}
    collect20 = x["collect20"].to_numpy(float)
    cross20 = x["cross20"].to_numpy(bool)
    up_candle = x["up_candle"].to_numpy(bool)
    body = x["body"].to_numpy(float)
    vr = x["vr"].to_numpy(float)
    disp20 = x["disp20"].to_numpy(float)
    ret10 = x["ret10"].to_numpy(float)

    rows = []
    for k in range(1, len(nlast)):
        T = int(nlast[k - 1])
        if pm[nlast[k]] - pm[T] != 1:
            continue
        if not uni_ok[T]:
            continue
        e = T + 1
        if e >= n or not (np.isfinite(o[e]) and o[e] > 0):
            continue
        rec = {"code": code, "name": row["name"], "market": row["market"], "T": T,
               "Tdate": dates[T].strftime("%Y-%m-%d"), "M": f"{dates[nlast[k]].year:04d}-{dates[nlast[k]].month:02d}",
               "year": int(dates[T].year), "pm": int(pm[nlast[k]]), "entry_date": dates[e].strftime("%Y-%m-%d")}
        okrow = True
        for hz in HORIZONS:
            ex = e + hz - 1
            if ex >= n or not np.isfinite(c[ex]):
                rec[f"r{hz}"] = np.nan
                continue
            rec[f"r{hz}"] = c[ex] / o[e] - 1 - (2 * SLIP + 2 * COMM + tax[ex])
        rec["ret_month"] = c[int(nlast[k])] / c[T] - 1
        for f in FEATS:
            rec[f] = cols[f][T]
        # 국면
        j0 = max(0, T - 19)
        cr = np.flatnonzero(cross20[j0:T + 1])
        last_cross = (j0 + cr[-1]) if cr.size else -1
        rec["ph_collect"] = float(collect20[T] > 0)
        rec["ph_break"] = float(last_cross >= 0)
        rec["cross_days_before"] = float(T - last_cross) if last_cross >= 0 else np.nan
        if last_cross >= 0:
            seg = disp20[last_cross:T + 1]
            rec["ph_pull"] = float(np.nanmin(seg) <= 0.02)
            rec["pull_depth_after_cross"] = float(np.nanmin(seg))
            segr = x["ret10"].to_numpy(float)[last_cross:T + 1]
            rec["pull_ret10"] = float(np.nanmin(segr[~np.isnan(segr)])) if np.isfinite(segr).any() else np.nan
        else:
            rec["ph_pull"] = 0.0
            rec["pull_depth_after_cross"] = np.nan
            rec["pull_ret10"] = np.nan
        rec["ph_rebound"] = float(up_candle[T] and vr[T] >= 1.0)
        rec["rebound_body"] = float(body[T]) if np.isfinite(body[T]) else np.nan
        rec["rebound_vr"] = float(vr[T])
        rows.append(rec)

    # 프로파일 (T-20..T)
    prof_cols = []
    if rows:
        prof = np.full((len(rows), 21 * 5), np.nan, np.float32)
        for i, r in enumerate(rows):
            T = int(r["T"])
            for mi, (f, arr) in enumerate(prof_src.items()):
                a = max(0, T - 20)
                seg = arr[a:T + 1]
                prof[i, mi * 21 + (21 - len(seg)):mi * 21 + 21] = seg
        df = pd.DataFrame(rows)
        pdf = pd.DataFrame(prof, columns=[f"p_{f}_{lag}" for f in prof_src for lag in range(-20, 1)])
        df = pd.concat([df, pdf], axis=1)
        return df
    return None


def cmd_panel(args):
    uni_all = pd.read_pickle(CACHE / "universe.pkl")
    keep = []
    for r in uni_all.itertuples():
        name = r.name or ""
        if r.end_type != "stock" or PREFERRED_RE.search(name) or any(k in name for k in NON_COMMON_KEYWORDS):
            continue
        if not (r.last_close and r.last_close > 0) or not (r.mktcap_eok and r.mktcap_eok > 0):
            continue
        if not (OHLCV / f"{r.code}.pkl").exists():
            continue
        keep.append((r.code, name, r.market, r.mktcap_eok * 1e8 / r.last_close))
    uni = pd.DataFrame(keep, columns=["code", "name", "market", "shares"]).set_index("code")
    idx = pd.read_pickle(CACHE / "index.pkl")
    codes = uni.index.tolist()
    t0 = time.time()
    dfs = []
    done = 0
    with ProcessPoolExecutor(max_workers=args.workers, initializer=init_worker, initargs=(uni, idx)) as ex:
        futs = {ex.submit(work_one, cd): cd for cd in codes}
        for f in as_completed(futs):
            done += 1
            try:
                df = f.result()
            except Exception as e:  # noqa: BLE001
                print(f"[fail] {futs[f]}: {e}", flush=True)
                continue
            if df is not None:
                dfs.append(df)
            if done % 400 == 0:
                print(f"[panel] {done}/{len(codes)} {time.time()-t0:.0f}s", flush=True)
    panel = pd.concat(dfs, ignore_index=True)
    panel = panel[panel["M"] < "2026-11"].copy()
    panel.to_pickle(PANEL)
    print(f"[panel] rows={len(panel)} stocks={panel['code'].nunique()} months={panel['M'].nunique()} "
          f"{time.time()-t0:.0f}s", flush=True)
    print(panel.groupby("year").size().to_string(), flush=True)



# ===========================================================================
# 분석: 점수 모델 · 조합 탐색 · 회전 포트폴리오 · 급등 전 과정
# ===========================================================================
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

for f in ("Noto Sans CJK KR", "Noto Sans CJK JP"):
    try:
        font_manager.findfont(f, fallback_to_default=False)
        plt.rcParams["font.family"] = f
        break
    except Exception:  # noqa: BLE001
        continue
plt.rcParams["axes.unicode_minus"] = False

from sklearn.linear_model import LogisticRegression, LinearRegression
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.preprocessing import StandardScaler

SPLIT = 2021


def md_table(df: pd.DataFrame, floatfmt: dict | None = None) -> str:
    """DataFrame → 마크다운 표 (숫자는 이미 문자열로 포맷했다고 가정)."""
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] + ["---:"] * (len(cols) - 1)) + "|"]
    for _, r in df.iterrows():
        out.append("| " + " | ".join(str(r[c]) for c in cols) + " |")
    return "\n".join(out)


def pct(v, d=2, sign=True):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "–"
    s = f"{v*100:+.{d}f}%" if sign else f"{v*100:.{d}f}%"
    return s


def num(v, d=2):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "–"
    return f"{v:.{d}f}"


# ---------------------------------------------------------------- 조건 풀
def cond_pool(p: pd.DataFrame) -> dict[str, np.ndarray]:
    c = {}
    c["신고가-3%내"] = (p["d52"] >= -0.03).to_numpy()
    c["신고가-5%내"] = (p["d52"] >= -0.05).to_numpy()
    c["신고가-10%내"] = (p["d52"] >= -0.10).to_numpy()
    c["종가>MA20"] = (p["disp20"] > 0).to_numpy()
    c["종가<MA20"] = (p["disp20"] < 0).to_numpy()
    c["MA20상승"] = (p["sl20"] > 0.01).to_numpy()
    c["MA20하락"] = (p["sl20"] < 0).to_numpy()
    c["MA20>MA60"] = (p["gap"] > 0).to_numpy()
    c["MA60상승"] = (p["sl60"] > 0).to_numpy()
    c["MA20접촉1~2회"] = ((p["touch10"] >= 1) & (p["touch10"] <= 2)).to_numpy()
    c["MA20접촉0회"] = (p["touch10"] == 0).to_numpy()
    c["거래량기울기+"] = (p["vs10"] > 0).to_numpy()
    c["거래량1.5배"] = (p["vr"] >= 1.5).to_numpy()
    c["거래량2배"] = (p["vr"] >= 2.0).to_numpy()
    c["5일>20일거래량"] = (p["v520"] > 1.0).to_numpy()
    c["ATR상위30%"] = (p["atr14"] > p["atr14"].quantile(0.70)).to_numpy()
    c["이격도0~5%"] = ((p["disp20"] >= 0) & (p["disp20"] <= 0.05)).to_numpy()
    c["이격도5~10%"] = ((p["disp20"] > 0.05) & (p["disp20"] <= 0.10)).to_numpy()
    c["이격도10%+"] = (p["disp20"] > 0.10).to_numpy()
    c["60일고점-3%내"] = (p["dh60"] >= -0.03).to_numpy()
    c["양봉비율상위30%"] = (p["yang10"] > p["yang10"].quantile(0.70)).to_numpy()
    c["외국인5일↑"] = (p["f5"] > 0).to_numpy()
    c["코스피>MA20"] = (p["bull"]).to_numpy()
    c["눌림3~5%"] = ((p["depth10"] >= -0.05) & (p["depth10"] <= -0.03)).to_numpy()
    c["눌림5~10%"] = ((p["depth10"] >= -0.10) & (p["depth10"] < -0.05)).to_numpy()
    c["MA20-60수렴"] = ((p["gap"] >= 0) & (p["gap"] <= 0.03)).to_numpy()
    return c


def stats_row(r: np.ndarray) -> dict:
    r = r[np.isfinite(r)]
    n = r.size
    if n == 0:
        return {"n": 0}
    pos = r > 0
    aw = r[pos].mean() if pos.any() else 0.0
    al = -r[~pos].mean() if (~pos).any() else np.nan
    return {"n": int(n), "mean": float(r.mean()), "median": float(np.median(r)),
            "win": float(pos.mean()), "sd": float(r.std(ddof=0)),
            "se": float(r.std(ddof=0) / n ** 0.5),
            "payoff": float(aw / al) if al and np.isfinite(al) and al > 0 else None}


def sweep(panel: pd.DataFrame, pool: dict[str, np.ndarray], max_k: int = 5, min_n: int = 200):
    tr = (panel["year"] <= 2020).to_numpy()
    va = ~tr
    R = {h: panel[f"r{h}"].to_numpy(float) for h in (20, 30, 40)}
    base = {h: R[h][tr & np.isfinite(R[h])].mean() for h in R}
    res = []
    names = list(pool)
    fin = np.isfinite(R[20])
    masks = {k: (v.astype(bool) & fin) for k, v in pool.items()}
    for k in range(2, max_k + 1):
        for combo in combinations(names, k):
            m_tr = masks[combo[0]].copy()
            for nm in combo[1:]:
                m_tr &= masks[nm]
            n_tr = int((m_tr & tr).sum())
            if n_tr < min_n:
                continue
            mtr = m_tr & tr
            a_tr = R[20][mtr]
            mu = a_tr.mean(); sd = a_tr.std(ddof=0)
            rec = {"combo": " & ".join(combo), "k": k, "n_train": n_tr,
                   "mean_train": mu, "med_train": float(np.median(a_tr)),
                   "win_train": float((a_tr > 0).mean()),
                   "sig_train": (mu - base[20]) / (sd / np.sqrt(n_tr)) if sd > 0 else np.nan,
                   "r30_train": float(np.nanmean(R[30][mtr])) if np.isfinite(R[30][mtr]).any() else np.nan,
                   "r40_train": float(np.nanmean(R[40][mtr])) if np.isfinite(R[40][mtr]).any() else np.nan}
            mva = m_tr & va
            n_va = int(mva.sum())
            if n_va >= 100:
                a_va = R[20][mva]
                mu2 = a_va.mean(); sd2 = a_va.std(ddof=0)
                rec.update({"n_val": n_va, "mean_val": mu2, "med_val": float(np.median(a_va)),
                            "win_val": float((a_va > 0).mean()),
                            "sig_val": (mu2 - base[20]) / (sd2 / np.sqrt(n_va)) if sd2 > 0 else np.nan,
                            "r30_val": float(np.nanmean(R[30][mva])) if np.isfinite(R[30][mva]).any() else np.nan,
                            "r40_val": float(np.nanmean(R[40][mva])) if np.isfinite(R[40][mva]).any() else np.nan})
            else:
                rec.update({"n_val": n_va, "mean_val": np.nan, "med_val": np.nan, "win_val": np.nan,
                            "sig_val": np.nan, "r30_val": np.nan, "r40_val": np.nan})
            res.append(rec)
    return pd.DataFrame(res), base


# ---------------------------------------------------------------- 점수 모델
def build_features(p: pd.DataFrame) -> list[str]:
    return [f for f in FEATS if p[f].notna().mean() > 0.8]


def whitebox_score(p: pd.DataFrame, feats: list[str], target: str = "surge") -> tuple[pd.Series, dict]:
    """학습 구간에서 특징별 5분위 리프트 → 리프트 순위를 0~1 점수로. 행별 합산."""
    tr = (p["year"] <= 2020).to_numpy()
    y_all = p[target].to_numpy(float)
    y_tr = y_all[tr]
    base = y_tr.mean()
    score = np.zeros(len(p))
    detail = {}
    for f in feats:
        v = p[f].to_numpy(float)
        qs = np.nanquantile(v[tr], [0.2, 0.4, 0.6, 0.8])
        b = np.searchsorted(qs, v, side="right")
        b = np.where(np.isfinite(v), b, -1)
        lift = np.ones(5)
        for i in range(5):
            m = (b == i) & tr
            if m.sum() >= 100:
                lift[i] = max(y_all[m].mean() / base, 1e-6)
        order = np.argsort(np.argsort(lift)) / 4.0  # 0~1 리프트 순위
        sc = np.where(b >= 0, order[np.clip(b, 0, 4)], 0.5)
        score += sc
        detail[f] = {"lifts": {f"Q{i+1}": float(lift[i]) for i in range(5)}, "rank_q": int(np.argmax(lift)) + 1}
    return pd.Series(score, index=p.index), detail


def ml_models(p: pd.DataFrame, feats: list[str]):
    tr = (p["year"] <= 2020).to_numpy()
    X = p[feats].to_numpy(float)
    X = np.where(np.isfinite(X), X, np.nan)
    med = np.nanmedian(X[tr], axis=0)
    X = np.where(np.isfinite(X), X, med)
    sc = StandardScaler().fit(X[tr])
    Xs = sc.transform(X)
    out = {}
    y = (p["r20"].to_numpy(float) >= SURGE).astype(int)
    lr = LogisticRegression(max_iter=2000, C=0.5).fit(Xs[tr], y[tr])
    out["logit_prob"] = lr.predict_proba(Xs)[:, 1]
    out["logit_coef"] = dict(zip(feats, lr.coef_[0]))
    gb = HistGradientBoostingClassifier(max_iter=250, learning_rate=0.05, max_depth=3,
                                        min_samples_leaf=100, l2_regularization=1.0).fit(Xs[tr], y[tr])
    out["gb_prob"] = gb.predict_proba(Xs)[:, 1]
    reg = HistGradientBoostingRegressor(max_iter=250, learning_rate=0.05, max_depth=3,
                                        min_samples_leaf=100, l2_regularization=1.0).fit(Xs[tr], p["r20"].to_numpy(float)[tr])
    out["gb_reg"] = reg.predict(Xs)
    perm = {}
    rng = np.random.default_rng(0)
    idx_val = np.flatnonzero(~tr)
    sample = rng.choice(idx_val, size=min(8000, idx_val.size), replace=False)
    base_pred = gb.predict_proba(Xs[sample])[:, 1]
    for j, f in enumerate(feats):
        Xp = Xs[sample].copy()
        Xp[:, j] = np.random.default_rng(1).permutation(Xp[:, j])
        perm[f] = float(np.mean(np.abs(gb.predict_proba(Xp)[:, 1] - base_pred)))
    out["perm_importance"] = perm
    return out


# ---------------------------------------------------------------- 포트폴리오
def kospi_fwd(idx: pd.DataFrame, d0: str, d1: str):
    try:
        a = idx.index.searchsorted(pd.Timestamp(d0))
        b = idx.index.searchsorted(pd.Timestamp(d1))
        if b >= len(idx):
            return np.nan
        return float(idx["close_k"].iloc[b] / idx["open_k"].iloc[a] - 1)
    except Exception:  # noqa: BLE001
        return np.nan


def portfolio(p: pd.DataFrame, score: np.ndarray, topn: int, hold: int, start: int, end: int,
              idx: pd.DataFrame, mode: str = "always", seed: int | None = None) -> dict:
    """월말 스냅샷 → 다음 거래일 시가 → hold거래일 보유. 리밸런싱 간격 = hold거래일 이상."""
    p = p.copy()
    p["score"] = score
    months = sorted(p["M"].unique())
    months = [m for m in months if start <= int(m[:4]) <= end]
    last_T = -10 ** 9
    recs = []
    rng = np.random.default_rng(seed if seed is not None else 0)
    for m in months:
        g = p[p["M"] == m]
        if g.empty:
            continue
        T = int(g["T"].iloc[0])
        if T - last_T < hold:
            continue
        g = g[np.isfinite(g[f"r{hold}"].to_numpy(float))]
        if len(g) < 5:
            continue
        if mode == "random":
            pick = g.sample(n=min(topn, len(g)), random_state=int(rng.integers(1e9)))
        else:
            if mode == "bull" and not bool(g["bull"].iloc[0] > 0):
                last_T = T
                recs.append({"M": m, "ret": 0.0, "n": 0, "invested": 0.0, "kospi": kospi_fwd(idx, g["entry_date"].iloc[0],
                                                                                        g["exit_date"].iloc[0] if "exit_date" in g else g["entry_date"].iloc[0])})
                continue
            pick = g.nlargest(min(topn, len(g)), "score")
        r = float(pick[f"r{hold}"].mean())
        recs.append({"M": m, "ret": r, "n": len(pick), "invested": 1.0,
                     "codes": ",".join(pick["code"].tolist())})
        last_T = T
    if not recs:
        return {"n_periods": 0}
    df = pd.DataFrame(recs)
    eq = (1 + df["ret"]).cumprod()
    yrs = len(df) * hold / 250.0
    cagr = eq.iloc[-1] ** (1 / yrs) - 1 if yrs > 0 else np.nan
    peak = eq.cummax()
    mdd = float((eq / peak - 1).min())
    sharpe = float(df["ret"].mean() / df["ret"].std(ddof=0) * np.sqrt(250 / hold)) if df["ret"].std(ddof=0) > 0 else np.nan
    return {"n_periods": int(len(df)), "trades_per_year": len(df) / yrs if yrs else np.nan,
            "avg_hold_days": hold, "exposure": float(df["invested"].mean()),
            "cum": float(eq.iloc[-1] - 1), "cagr": cagr, "mdd": mdd, "sharpe": sharpe,
            "win_months": float((df["ret"] > 0).mean()), "mean_period": float(df["ret"].mean()),
            "median_period": float(df["ret"].median()), "years": yrs,
            "series": df}


def buyhold(series: pd.Series, start: str, end: str) -> dict:
    s = series.dropna()
    s = s[(s.index >= start) & (s.index <= end)]
    if len(s) < 2:
        return {}
    eq = s / s.iloc[0]
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    peak = eq.cummax()
    return {"cum": float(eq.iloc[-1] - 1), "cagr": float(eq.iloc[-1] ** (1 / yrs) - 1),
            "mdd": float((eq / peak - 1).min()),
            "sharpe": float(np.log(eq).diff().mean() / np.log(eq).diff().std(ddof=0) * np.sqrt(250)),
            "win_months": np.nan, "years": yrs}


def exit_of(idx: pd.DataFrame, entry_date: str, hold: int) -> str:
    pos = idx.index.searchsorted(pd.Timestamp(entry_date))
    k = min(pos + hold - 1, len(idx) - 1)
    return idx.index[k].strftime("%Y-%m-%d")


def cmd_analyze(args):
    p = pd.read_pickle(PANEL)
    idx = pd.read_pickle(CACHE / "index.pkl")
    sam = pd.read_pickle(OHLCV / "005930.pkl")["aclose"]
    T = []
    out_json = {}
    feats = build_features(p)
    p["surge"] = (p["r20"] >= SURGE).astype(float)

    tr = p["year"] <= 2020
    va = ~tr
    base = {"train": float(p.loc[tr, "r20"].mean()), "val": float(p.loc[va, "r20"].mean()),
            "all": float(p["r20"].mean()),
            "base30": {"train": float(p.loc[tr, "r30"].mean()), "val": float(p.loc[va, "r30"].mean())},
            "base40": {"train": float(p.loc[tr, "r40"].mean()), "val": float(p.loc[va, "r40"].mean())},
            "surge_rate": float(p["surge"].mean()), "n": len(p),
            "stocks": int(p["code"].nunique()), "months": int(p["M"].nunique()),
            "kospi20": float(np.nanmean([kospi_fwd(idx, r.entry_date, exit_of(idx, r.entry_date, 20))
                                         for r in p.sample(4000, random_state=0).itertuples()])),
            "kospi": {}, }
    out_json["base"] = base

    # ---------- 1) 기준선 표
    rows = []
    for h in HORIZONS:
        r = p[f"r{h}"].to_numpy(float)
        r = r[np.isfinite(r)]
        pos = r > 0
        rows.append({"보유": f"{h}거래일", "n": len(r), "평균": pct(r.mean()), "중앙값": pct(np.median(r)),
                     "승률": pct(pos.mean(), 1, False),
                     "손익비": num((r[pos].mean() if pos.any() else 0) / abs(r[~pos].mean())) if (~pos).any() else "–"})
    T.append("### 월말 스냅샷 기준선 (전 종목·전 월, 비용 차감 순수익)\n\n" + md_table(pd.DataFrame(rows)))

    # ---------- 2) 조합 탐색
    pool = cond_pool(p)
    out_json["cond_hits"] = {k: int(np.nansum(v)) for k, v in pool.items()}
    sw, swbase = sweep(p, pool, 5, 200)
    sw = sw.sort_values("mean_train", ascending=False).reset_index(drop=True)
    n_combos = len(sw)
    from scipy.stats import norm
    z_bonf = float(norm.ppf(1 - 0.025 / max(n_combos, 1)))
    out_json["sweep"] = {"n_combos": int(n_combos), "base_train": swbase, "z_bonferroni": z_bonf}
    top = sw.head(50).copy()
    # 검증 순위
    val_rank = sw.sort_values("mean_val", ascending=False).reset_index(drop=True)
    val_rank["rank_val"] = val_rank.index + 1
    top = top.merge(val_rank[["combo", "rank_val"]], on="combo", how="left")
    rows = []
    for i, r in top.iterrows():
        rows.append({"학습순위": i + 1, "조합": r["combo"], "학습 n": int(r["n_train"]),
                     "학습 평균": pct(r["mean_train"]), "학습 중앙": pct(r["med_train"]),
                     "학습 σ": num(r["sig_train"], 1), "검증순위": int(r["rank_val"]) if np.isfinite(r["rank_val"]) else "–",
                     "검증 n": int(r["n_val"]) if np.isfinite(r["n_val"]) else 0,
                     "검증 평균": pct(r["mean_val"]), "검증 σ": num(r["sig_val"], 1)})
    T.append("### 학습 상위 50개 조합의 검증 구간 순위\n\n" + md_table(pd.DataFrame(rows)))
    good = sw[(sw["sig_val"] >= 2) & (sw["mean_val"] > 0)]
    good_bf = good[good["sig_val"] >= z_bonf]
    out_json["survivors"] = {"n_2sigma": int(len(good)), "n_bonferroni": int(len(good_bf)),
                             "z_bonferroni": z_bonf,
                             "list": good.sort_values("mean_val", ascending=False).head(30)[
                                 ["combo", "n_train", "mean_train", "mean_val", "sig_val", "med_val", "win_val"]
                             ].to_dict("records")}
    out_json["top50"] = top[["combo", "n_train", "mean_train", "mean_val", "sig_val", "rank_val"]].to_dict("records")

    # ---------- 3) 점수 모델
    wb, wb_detail = whitebox_score(p, feats, "surge")
    p["wb"] = wb.to_numpy()
    ml = ml_models(p, feats)
    for k in ("logit_prob", "gb_prob", "gb_reg"):
        p[k] = ml[k]
    out_json["whitebox_feature"] = {f: v for f, v in sorted(wb_detail.items(),
                                    key=lambda kv: -np.nanmax(list(kv[1]["lifts"].values())))}
    out_json["logit_coef"] = ml["logit_coef"]
    out_json["perm_importance"] = ml["perm_importance"]
    # 상위 10% 리프트 (검증)
    imp_rows = []
    for f in feats:
        d = wb_detail[f]
        lift_max = max(d["lifts"].values())
        imp_rows.append({"특징": f, "최고 분위 리프트": num(lift_max), "최고 분위": f"Q{d['rank_q']}",
                         "로지스틱 계수": num(ml["logit_coef"][f]), "GBM 중요도(검증)": num(ml["perm_importance"][f], 4)})
    imp = pd.DataFrame(imp_rows).sort_values("GBM 중요도(검증)", ascending=False)
    T.append("### 특징 중요도 (학습 2016–2020 기준, 검증 2021–2026 평가)\n\n" + md_table(imp))
    out_json["importance_table"] = imp.to_dict("records")

    # ---------- 4) 회전 포트폴리오 (점수 상위 N · 20/30거래일 보유 · 갈아타기)
    def ran_score(seed):
        return np.random.default_rng(seed).random(len(p))

    def add_port(key, st, hold):
        if not st.get("n_periods"):
            return
        port[key] = {k: v for k, v in st.items() if k != "series"}
        eq_rows.append((key, st["series"]))

    port = {}
    eq_rows = []
    for hold in (20, 30):
        for name, col in (("whitebox", "wb"), ("logit", "logit_prob"), ("gbm", "gb_prob")):
            for topn in (5, 10, 20):
                for lo, lab in ((2016, "all"), (2021, "val")):
                    add_port(f"{name}|{topn}|{hold}|{lab}",
                             portfolio(p, p[col].to_numpy(), topn, hold, lo, 2026, idx), hold)
        # 무작위 10종목 (20회 평균)
        for lo, lab in ((2016, "all"), (2021, "val")):
            acc, metas = [], []
            for seed in range(20):
                st = portfolio(p, ran_score(seed), 10, hold, lo, 2026, idx)
                if st.get("n_periods"):
                    acc.append(st["series"]["ret"].to_numpy())
                    metas.append(st)
            if acc:
                L = min(len(a) for a in acc)
                A = np.array([a[:L] for a in acc])
                ret = A.mean(axis=0)
                eq = (1 + ret).cumprod()
                yrs = L * hold / 250.0
                port[f"random|10|{hold}|{lab}"] = {
                    "n_periods": L, "cum": float(eq[-1] - 1), "cagr": float(eq[-1] ** (1 / yrs) - 1),
                    "mdd": float((eq / np.maximum.accumulate(eq) - 1).min()),
                    "sharpe": float(ret.mean() / ret.std(ddof=0) * np.sqrt(250 / hold)),
                    "win_months": float((ret > 0).mean()), "mean_period": float(ret.mean()),
                    "median_period": float(np.median(ret)), "trades_per_year": L / yrs,
                    "avg_hold_days": hold, "exposure": 1.0, "years": yrs}
                eq_rows.append((f"random10_{hold}_{lab}",
                                pd.DataFrame({"M": range(L), "ret": ret, "invested": 1.0})))

    bh = {}
    for lab, d0 in (("all", "2016-01-04"), ("val", "2021-01-04")):
        bh[f"kospi_{lab}"] = buyhold(idx["close_k"], d0, "2026-11-20")
        bh[f"samsung_{lab}"] = buyhold(sam, d0, "2026-11-20")
    out_json["portfolios"] = port
    out_json["buyhold"] = bh

    # 회전 vs 보유 비교표
    rows = []
    def prow(lab, key):
        st = port.get(key)
        if not st:
            return None
        return {"전략": lab, "기간": "2016–2026" if key.endswith("all") else "2021–2026",
                "보유": f"{st['avg_hold_days']}일", "연 회전": num(st["trades_per_year"], 1),
                "노출도": pct(st["exposure"], 0, False),
                "누적": pct(st["cum"], 1), "CAGR": pct(st["cagr"], 2), "MDD": pct(st["mdd"], 1),
                "샤프": num(st["sharpe"], 2), "월 승률": pct(st["win_months"], 1, False),
                "1회 평균": pct(st["mean_period"])}
    for key, lab in ((("whitebox|10|20|all"), "점수(화이트박스) 상위10"), (("whitebox|10|30|all"), "점수(화이트박스) 상위10"),
                     (("logit|10|20|all"), "로지스틱 상위10"), (("gbm|10|20|all"), "GBM 상위10"),
                     (("whitebox|10|20|val"), "점수(화이트박스) 상위10"), (("whitebox|10|30|val"), "점수(화이트박스) 상위10"),
                     (("logit|10|20|val"), "로지스틱 상위10"), (("gbm|10|20|val"), "GBM 상위10"),
                     (("random|10|20|all"), "무작위 10종목"), (("random|10|30|all"), "무작위 10종목"),
                     (("random|10|20|val"), "무작위 10종목"), (("random|10|30|val"), "무작위 10종목")):
        r = prow(lab, key)
        if r:
            rows.append(r)
    tab = pd.DataFrame(rows)
    T.append("### 회전 포트폴리오 vs 단순 보유 (같은 기간·같은 비용)\n\n" + md_table(tab))
    rows2 = []
    for lab, key in (("코스피 지수 보유", "kospi_all"), ("코스피 지수 보유", "kospi_val"),
                     ("삼성전자 보유", "samsung_all"), ("삼성전자 보유", "samsung_val")):
        b = bh[key]
        if b:
            rows2.append({"전략": lab, "기간": "2016–2026" if key.endswith("all") else "2021–2026",
                          "누적": pct(b["cum"], 1), "CAGR": pct(b["cagr"], 2), "MDD": pct(b["mdd"], 1),
                          "샤프": num(b["sharpe"], 2)})
    T.append("### 단순 보유 벤치마크 (배당 미반영)\n\n" + md_table(pd.DataFrame(rows2)))

    # ---------- 5) 급등 전 과정: 평균 프로파일
    prof_metrics = ["disp20", "disp60", "vr", "yang5", "atr14"]
    lags = list(range(-20, 1))
    surge = p["surge"] > 0
    prof = {}
    fig, axes = plt.subplots(1, 5, figsize=(22, 3.6))
    for ax, mt in zip(axes, prof_metrics):
        cols = [f"p_{mt}_{l}" for l in lags]
        a = np.nanmean(p.loc[surge, cols].to_numpy(float), axis=0) * 100
        b = np.nanmean(p.loc[~surge, cols].to_numpy(float), axis=0) * 100
        ax.plot(lags, a, label="사건(+20% 달)", lw=2, color="#c0392b")
        ax.plot(lags, b, label="통제(전체)", lw=1.6, color="#2c3e50", ls="--")
        ax.axvline(0, color="gray", lw=0.6)
        ax.set_title({"disp20": "종가/MA20 −1 (%)", "disp60": "종가/MA60 −1 (%)", "vr": "거래량/20일평균",
                      "yang5": "양봉비율5일 (%)", "atr14": "ATR14/종가 (%)"}[mt], fontsize=10)
        ax.grid(alpha=0.3)
        prof[mt] = {"event": a.tolist(), "control": b.tolist()}
    axes[0].legend(fontsize=9)
    plt.tight_layout()
    plt.savefig(WORK / "surge_profile.png", dpi=130)
    plt.close()
    out_json["profile"] = {"lags": lags, "data": prof}

    # ---------- 6) 국면 분해
    ph_rows = []
    for ph, lab in (("ph_collect", "1국면 매집(10일 횡보+거래량 기울기+)"), ("ph_break", "2국면 MA20 상향 돌파"),
                    ("ph_pull", "3국면 돌파 후 MA20 재접촉"), ("ph_rebound", "4국면 반등(양봉+거래량)")):
        e = p.loc[surge, ph].mean()
        c = p.loc[~surge, ph].mean()
        d1 = p.loc[surge & (p[ph] > 0), "r20"].mean()
        d0 = p.loc[surge & (p[ph] == 0), "r20"].mean()
        n1 = int((surge & (p[ph] > 0)).sum())
        n0 = int((surge & (p[ph] == 0)).sum())
        ph_rows.append({"국면": lab, "사건 통과율": pct(e, 1, False), "통제 통과율": pct(c, 1, False),
                        "리프트": num(e / c if c else np.nan), "통과 사건 n": n1,
                        "통과 사건 20일평균": pct(d1), "미통과 사건 n": n0, "미통과 20일평균": pct(d0)})
    ph_tab = pd.DataFrame(ph_rows)
    T.append("### 급등 전 4국면 (사건 = 월 +20% 이상, 국면 판정은 급등월 직전 20거래일)\n\n" + md_table(ph_tab))
    out_json["phases"] = ph_rows
    # 국면 조합
    combs = []
    for k in range(0, 5):
        for cs in combinations(["ph_collect", "ph_break", "ph_pull", "ph_rebound"], k):
            m = np.ones(len(p), bool)
            for c_ in cs:
                m &= p[c_].to_numpy() > 0
            combs.append({"국면수": k, "조합": "+".join(c_.replace("ph_", "") for c_ in cs) or "없음",
                          "사건 통과율": pct(p.loc[surge.to_numpy() & m].shape[0] / max(int(surge.sum()), 1), 1, False),
                          "통제 통과율": pct((~surge.to_numpy() & m).sum() / max(int((~surge).sum()), 1), 1, False),
                          "n(전체)": int(m.sum()),
                          "20일평균": pct(np.nanmean(p["r20"].to_numpy()[m]) if m.any() else np.nan)})
    out_json["phase_combos"] = combs

    # ---------- 7) 점수 상위 vs 하위 분위 성과
    q_rows = []
    for col, lab in (("wb", "화이트박스 점수"), ("logit_prob", "로지스틱 확률"), ("gb_prob", "GBM 확률"), ("gb_reg", "GBM 회귀")):
        for span, sl in ((p["year"] <= 2020, "학습"), (~(p["year"] <= 2020), "검증")):
            v = p.loc[span, col].to_numpy(float)
            r = p.loc[span, "r20"].to_numpy(float)
            q = pd.qcut(pd.Series(v), 5, labels=False, duplicates="drop")
            for i in range(5):
                m = (q == i).to_numpy() & np.isfinite(r)
                q_rows.append({"모델": lab, "구간": sl, "분위": f"Q{i+1}", "n": int(m.sum()),
                               "20일평균": pct(r[m].mean()), "승률": pct((r[m] > 0).mean(), 1, False)})
    out_json["quantile"] = q_rows
    T.append("### 점수 분위별 성과\n\n" + md_table(pd.DataFrame(q_rows)))

    with open(WORK / "scoring_analysis.json", "w") as f:
        json.dump(out_json, f, ensure_ascii=False, indent=1, default=float)
    with open(WORK / "scoring_tables.md", "w") as f:
        f.write("\n\n".join(T))
    p.to_pickle(WORK / "scoring_panel_scored.pkl")
    print("[done] analyze", flush=True)


def cmd_examples(args):
    """예시 3건: HD현대일렉트릭 2025-09 + 급등 사례 2건. 국면 차트 저장."""
    p = pd.read_pickle(PANEL)
    idx = pd.read_pickle(CACHE / "index.pkl")
    # HD현대일렉트릭
    code = "267260"
    d = pd.read_pickle(OHLCV / f"{code}.pkl")
    x = prep(d, 1.0, idx)
    seg = x.loc["2025-08-20":"2025-10-31"]
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(seg.index, seg["c"], color="#111", lw=1.4, label="종가")
    ax.plot(seg.index, seg["ma20"], color="#c0392b", lw=1.2, label="MA20")
    ax.plot(seg.index, seg["ma60"], color="#2980b9", lw=1.2, label="MA60")
    for d0, d1, col, lab in (("2025-08-20", "2025-09-09", "#f1c40f", "1 매집"),
                             ("2025-09-10", "2025-09-12", "#27ae60", "2 MA20 돌파(신고가)"),
                             ("2025-09-13", "2025-09-26", "#e67e22", "3 눌림(MA20 접촉)"),
                             ("2025-09-29", "2025-10-02", "#8e44ad", "4 반등")):
        ax.axvspan(pd.Timestamp(d0), pd.Timestamp(d1), alpha=0.15, color=col, label=lab)
    ax.legend(fontsize=9, loc="upper left")
    ax.set_title("267260 HD현대일렉트릭 2025-08~10 — 4국면")
    ax.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(WORK / "surge_phases_example.png", dpi=130)
    plt.close()
    ex = {}
    s = x.loc["2025-09-08":"2025-09-30"]
    ex["hd"] = {"dates": {k: {"close": float(v["c"]), "ma20": float(v["ma20"]), "disp20": float(v["disp20"]),
                              "vr": float(v["vr"]), "depth10": float(v["depth10"]), "d52": float(v["d52"]),
                              "low_rel20": float(v["l"] / v["ma20"] - 1)} for k, v in s.iterrows()},
                "oct_ret": float(d["aclose"].loc["2025-10-31"] / d["aclose"].loc["2025-09-30"] - 1)}
    with open(WORK / "example_json.json", "w") as f:
        json.dump(ex, f, ensure_ascii=False, indent=1, default=float)
    print("[done] examples", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["panel", "analyze", "examples"])
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    {"panel": cmd_panel, "analyze": cmd_analyze, "examples": cmd_examples}[args.cmd](args)
