#!/usr/bin/env python3
"""월봉 +20% 급등 달의 '직전 1~2주' 국면 분석 + 거래량 증거 + 돌파/눌림목 진입 기대수익 실측.

질문(사용자 지시):
  1) 개별 종목의 월봉 수익률이 +20% 이상인 달을 사건으로 잡고, 그 달이 시작되기
     직전 10거래일(T-9~T, T=전월 마지막 거래일)의 차트 상태를 뜯어 공통점을 찾는다.
     모든 특징은 반드시 '같은 유니버스의 모든 (종목,월)' 통제집단과 비교한다.
  2) 캔들이 최근 10일 안에 넘은 '선'을 순위로 내고, 돌파 후 경과일 분포를 낸다.
  3) 거래량: 배수 구간, 패턴 유형, 시점별 위치, 절대 거래대금, 매집 강도.
  4) 선 돌파 후 눌림목(±2% 근접 후 반등) 진입의 순수익을 실측한다.
  5) 모든 유형·진입 타이밍을 하나의 표로: n, 5/10/20일 순수익 평균·중앙값·승률·손익비,
     무작위 진입 기준선 대비 차이(표준오차·σ), 연도별 부호.

데이터: work/backtest/cache/ohlcv/*.pkl (수정주가 OHLC, 2015-01-02~2026-11-20)
사용법:
  python3 scripts/monthly_surge.py collect --workers 2
  python3 scripts/monthly_surge.py analyze
"""
from __future__ import annotations

import argparse
import os
import pickle
import re
import sys
import time
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

REPO = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("BACKTEST_CACHE", REPO.parent / "work" / "backtest" / "cache"))
OHLCV = CACHE / "ohlcv"
WORK = REPO.parent / "work" / "backtest"
OUT_MONTHLY = WORK / "surge_monthly.pkl"
OUT_ACC = WORK / "surge_acc.pkl"

MIN_MKTCAP = 50_000_000_000
MIN_VALUE = 10_000_000_000
SURGE = 0.20
SLIP = 0.001
COMM = 0.00015
TAX_BY_YEAR = {2015: 0.0030, 2016: 0.0030, 2017: 0.0030, 2018: 0.0030, 2019: 0.0030,
               2020: 0.0025, 2021: 0.0023, 2022: 0.0023, 2023: 0.0020, 2024: 0.0018,
               2025: 0.0015, 2026: 0.0020}
YEARS = list(range(2015, 2027))

PREFERRED_RE = re.compile(r"(?:\d?우B?C?)$|우\(|우B$")
NON_COMMON_KEYWORDS = ("스팩", "리츠", "인프라", "ETN", "ETF", "선물", "레버리지", "인버스", "합성")

MA_NS = (5, 20, 60, 120, 240)
HI_NS = (60, 120, 250)

NB = 2100
BW = 0.001
LO = -0.6


def acc_add(acc: dict, key: str, r: np.ndarray, yidx: np.ndarray) -> None:
    if r.size == 0:
        return
    ok = np.isfinite(r)
    r = r[ok]
    y = yidx[ok]
    if r.size == 0:
        return
    a = acc.get(key)
    if a is None:
        a = {"n": 0, "s": 0.0, "ss": 0.0,
             "hist": np.zeros(NB, np.int32),
             "yn": np.zeros(len(YEARS), np.int64), "ys": np.zeros(len(YEARS))}
        acc[key] = a
    a["n"] += int(r.size)
    a["s"] += float(r.sum())
    a["ss"] += float((r * r).sum())
    bins = np.clip(((r - LO) / BW).astype(np.int32), 0, NB - 1)
    a["hist"] += np.bincount(bins, minlength=NB).astype(np.int32)
    a["yn"] += np.bincount(y, minlength=len(YEARS))
    a["ys"] += np.bincount(y, weights=r, minlength=len(YEARS))


def merge_acc(dst: dict, src: dict) -> None:
    for k, v in src.items():
        a = dst.get(k)
        if a is None:
            dst[k] = v
        else:
            a["n"] += v["n"]
            a["s"] += v["s"]
            a["ss"] += v["ss"]
            a["hist"] += v["hist"]
            a["yn"] += v["yn"]
            a["ys"] += v["ys"]


CENTERS = LO + (np.arange(NB) + 0.5) * BW


def acc_stats(a: dict) -> dict:
    n = a["n"]
    if n == 0:
        return {"n": 0}
    h = a["hist"]
    mean = a["s"] / n
    sd = max(a["ss"] / n - mean * mean, 0.0) ** 0.5
    cum = np.cumsum(h)
    med_i = int(np.searchsorted(cum, n / 2))
    pos = CENTERS > 0
    npos = int(h[pos].sum())
    nneg = int(h[~pos].sum())
    spos = float((h[pos] * CENTERS[pos]).sum())
    sneg = float((h[~pos] * CENTERS[~pos]).sum())
    ymean = []
    for i in range(len(YEARS)):
        ni = int(a["yn"][i])
        ymean.append({"year": YEARS[i], "n": ni,
                      "mean": float(a["ys"][i] / ni) if ni else None})
    return {"n": n, "mean": mean, "se": sd / n ** 0.5, "sd": sd,
            "median": float(CENTERS[med_i]), "win": npos / n,
            "avg_win": spos / npos if npos else None,
            "avg_loss": sneg / nneg if nneg else None,
            "payoff": (spos / npos) / abs(sneg / nneg) if npos and nneg and sneg else None,
            "yearly": ymean}


UNI = None
IDX = None


def init_worker(uni, idx):
    global UNI, IDX
    UNI, IDX = uni, idx


def days_since(b: pd.Series) -> np.ndarray:
    arr = np.asarray(b.fillna(False).to_numpy(dtype=bool))
    idx = np.arange(arr.size)
    last = np.maximum.accumulate(np.where(arr, idx, -1))
    return np.where(last >= 0, idx - last, 9999)


def prep(d: pd.DataFrame, shares: float, idxdf: pd.DataFrame) -> pd.DataFrame:
    x = pd.DataFrame(index=d.index)
    c = d["aclose"].astype(float)
    o = d["aopen"].astype(float)
    h = d["ahigh"].astype(float)
    l = d["alow"].astype(float)
    v = d["volume"].astype(float)
    tf = d["turnover"].astype(float)
    x["c"], x["o"], x["h"], x["l"] = c, o, h, l
    x["ret1"] = c.pct_change()

    ma = {}
    for n in MA_NS:
        m = c.rolling(n).mean()
        ma[n] = m
        x[f"ma{n}"] = m
        x[f"disp{n}"] = c / m - 1
    x["align3"] = (ma[5] > ma[20]) & (ma[20] > ma[60])
    x["align5"] = (ma[5] > ma[20]) & (ma[20] > ma[60]) & (ma[60] > ma[120]) & (ma[120] > ma[240])
    x["sl20_5"] = ma[20] / ma[20].shift(5) - 1
    x["sl20_20"] = ma[20] / ma[20].shift(20) - 1
    x["sl60_5"] = ma[60] / ma[60].shift(5) - 1
    x["sl60_20"] = ma[60] / ma[60].shift(20) - 1

    for n in MA_NS:
        ab = (c > ma[n]).fillna(False)
        x[f"x{n}"] = ab & (~ab.shift(1).fillna(True).astype(bool))
        x[f"ds{n}"] = pd.Series(days_since(x[f"x{n}"]), index=x.index)
    for n in HI_NS:
        prevhi = h.rolling(n).max().shift(1)
        x[f"xh{n}"] = (c > prevhi).fillna(False)
        x[f"dsh{n}"] = pd.Series(days_since(x[f"xh{n}"]), index=x.index)
    x["d52w"] = c / h.rolling(250).max() - 1

    v20 = v.rolling(20).mean()
    v10 = v.rolling(10).mean()
    v5 = v.rolling(5).mean()
    v3 = v.rolling(3).mean()
    vr = v / v20
    x["vr"] = vr
    x["v520"] = v5 / v20
    x["v10_20"] = v10 / v20
    x["vmax10"] = vr.rolling(10).max()
    x["vmax5"] = vr.rolling(5).max()
    t20 = tf.rolling(20).mean()
    x["t20"] = t20
    x["t520"] = tf.rolling(5).mean() / t20
    x["tmax10"] = (tf / t20).rolling(10).max()
    x["tmax5"] = (tf / t20).rolling(5).max()

    sd20 = c.rolling(20).std(ddof=0)
    bbw = 4 * sd20 / ma[20]
    x["bbw"] = bbw
    x["bbwpct"] = bbw.rolling(250).rank(pct=True)

    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    x["atr14"] = tr.rolling(14).mean() / c
    x["tr20"] = tr.rolling(20).mean() / c

    x["box20"] = (h.rolling(20).max() - l.rolling(20).min()) / c
    x["box60"] = (h.rolling(60).max() - l.rolling(60).min()) / c
    x["box120"] = (h.rolling(120).max() - l.rolling(120).min()) / c
    x["dh60"] = c / h.rolling(60).max() - 1
    x["dd120"] = c / h.rolling(120).max() - 1
    x["rb60"] = c / l.rolling(60).min() - 1

    yang = c > o
    x["yang10"] = yang.rolling(10).mean()
    body = (c - o).abs()
    rng = (h - l).replace(0, np.nan)
    big = (x["ret1"] >= 0.05) | (body / rng >= 0.7)
    x["big10"] = big.fillna(False).astype(float).rolling(10).max()
    x["gap10"] = (o > h.shift(1)).fillna(False).astype(float).rolling(10).max()

    fp = d["foreign_pct"].astype(float)
    x["f5"] = fp - fp.shift(5)
    x["f20"] = fp - fp.shift(20)

    x["mktcap"] = d["close"].astype(float) * shares
    x["vol0"] = (v > 0)

    k = idxdf["close_k"].reindex(x.index).ffill()
    x["bull"] = (k > k.rolling(20).mean())

    # --- 거래량 패턴 (최근 10거래일) ---
    ret10 = c / c.shift(10) - 1
    x["ret10"] = ret10
    # 점증형: 최근 3일 > 5일 > 10일 평균, 10일 평균이 20일 평균의 1.2배 이상
    x["vp_gradual"] = ((v3 > v5) & (v5 > v10) & (v10 >= 1.2 * v20)).fillna(False)
    # 단발 폭발형: 최근 10일 내 3배 이상이 1번뿐이고 그 뒤 급감
    big_days = ((vr >= 2.0).fillna(False).astype(float).rolling(10).sum())
    x["vp_burst"] = ((x["vmax10"] >= 3.0) & (big_days <= 1)).fillna(False)
    # 조용한 매집형: 10일 수익률 -3~+3%인데 거래량 10일 평균이 1.5배 이상
    x["vp_quiet"] = ((ret10 >= -0.03) & (ret10 <= 0.03) & (x["v10_20"] >= 1.5)).fillna(False)
    x["vp_quiet2"] = ((ret10 >= -0.05) & (ret10 <= 0.05) & (x["v10_20"] >= 1.3)).fillna(False)
    # 거래량 동반 상승형: 같은 날 +5% 이상 & 2배 이상
    volup = ((x["ret1"] >= 0.05) & (vr >= 2.0)).fillna(False)
    x["vp_volup"] = volup.astype(float).rolling(10).max().fillna(0)
    # 거래량 없는 상승형: 10일 수익률 +0% 이상인데 10일 거래량 평균 1배 이하
    x["vp_novol"] = ((ret10 > 0) & (x["v10_20"] <= 1.0)).fillna(False)

    # 매집 강도: 최근 20일 상승일 거래량 합 / 하락일 거래량 합
    upv = v.where(x["ret1"] > 0, 0.0)
    dnv = v.where(x["ret1"] < 0, 0.0)
    su = upv.rolling(20).sum()
    sd = dnv.rolling(20).sum()
    x["vol_ud"] = su / sd.replace(0, np.nan)
    return x


FEAT_FIELDS = ["disp5", "disp20", "disp60", "disp120", "disp240",
               "align3", "align5", "sl20_5", "sl20_20", "sl60_5", "sl60_20",
               "x20", "x60", "x120", "x240", "xh60", "xh120", "xh250",
               "ds20", "ds60", "ds120", "ds240", "dsh60", "dsh120", "dsh250",
               "d52w", "v520", "v10_20", "vmax10", "t520", "tmax10", "t20",
               "bbw", "bbwpct", "atr14", "tr20",
               "box20", "box60", "box120", "dh60", "dd120", "rb60",
               "yang10", "big10", "gap10", "f5", "f20", "bull",
               "ret10", "vol_ud", "vp_gradual", "vp_burst", "vp_quiet", "vp_quiet2", "vp_volup", "vp_novol"]
VR_FIELDS = [f"vr_m{k}" for k in range(10, -1, -1)]
MROW_FIELDS = (["code", "name", "market", "T", "M", "y", "ret", "mktcap"]
               + FEAT_FIELDS + VR_FIELDS + ["ret_prev", "presurge"])


def f2(v) -> float:
    if v is None:
        return np.nan
    if isinstance(v, (bool, np.bool_)):
        return float(v)
    try:
        v = float(v)
    except (TypeError, ValueError):
        return np.nan
    return v if np.isfinite(v) else np.nan


def work_one(code: str):
    row = UNI.loc[code]
    p = OHLCV / f"{code}.pkl"
    if not p.exists():
        return None
    d = pd.read_pickle(p)
    if d.empty or len(d) < 300:
        return None
    x = prep(d, float(row["shares"]), IDX)
    n = len(x)
    c = x["c"].to_numpy(float)
    o = x["o"].to_numpy(float)
    uni_ok = ((x["mktcap"].to_numpy(float) >= MIN_MKTCAP)
              & (x["t20"].to_numpy(float) >= MIN_VALUE)
              & (x["vol0"].to_numpy(bool)))
    dates = x.index
    pm = dates.year.to_numpy() * 12 + dates.month.to_numpy()
    mlabel = np.array([f"{y:04d}-{m:02d}" for y, m in zip(dates.year, dates.month)])
    nlast = np.r_[np.flatnonzero(pm[:-1] != pm[1:]), n - 1]
    yidx = dates.year.to_numpy() - YEARS[0]
    cost = np.array([2 * SLIP + 2 * COMM + TAX_BY_YEAR.get(int(y), 0.0020) for y in dates.year])
    vr = x["vr"].to_numpy(float)

    arrs = {f: x[f].to_numpy(float) for f in FEAT_FIELDS}

    rows = []
    Ts, Ms, Rets, Mpm = [], [], [], []
    for k in range(1, len(nlast)):
        T = int(nlast[k - 1])
        M = int(nlast[k])
        if pm[M] - pm[T] != 1:
            continue
        if mlabel[M] >= "2026-11":
            continue
        if not uni_ok[T]:
            continue
        if not (np.isfinite(c[T]) and c[T] > 0 and np.isfinite(c[M]) and c[M] > 0):
            continue
        if not np.isfinite(x["ma240"].iat[T]) or not np.isfinite(x["bbwpct"].iat[T]):
            continue
        ret = c[M] / c[T] - 1
        vals = [f2(arrs[f][T]) for f in FEAT_FIELDS]
        vrs = [f2(vr[T - kk]) if T - kk >= 0 else np.nan for kk in range(10, -1, -1)]
        rows.append([code, row["name"], row["market"], T, mlabel[M], int(dates[T].year), ret,
                     f2(x["mktcap"].iat[T])] + vals + vrs + [np.nan, 0.0])
        Ts.append(T)
        Ms.append(mlabel[M])
        Rets.append(ret)
        Mpm.append(int(pm[M]))

    mdf = pd.DataFrame(rows, columns=MROW_FIELDS) if rows else None
    presurge_dates = np.zeros(n, bool)
    if mdf is not None and len(mdf) > 0:
        rets = np.array(Rets)
        surge = rets >= SURGE
        prev_ret = np.full(len(rets), np.nan)
        pre = np.zeros(len(rets), bool)
        for i in range(1, len(rets)):
            if Mpm[i] - Mpm[i - 1] == 1:
                prev_ret[i] = rets[i - 1]
                pre[i - 1] = surge[i]
        mdf["ret_prev"] = prev_ret
        mdf["presurge"] = pre.astype(np.float32)
        mdf["T"] = mdf["T"].astype(np.int32)
        for i in np.flatnonzero(pre):
            T = int(mdf["T"].iloc[i])
            presurge_dates[max(0, T - 9):T + 1] = True

    # ---- 일봉 신호 + 전방 수익률 ----
    acc = {}

    def fwd_and_add(key: str, e: np.ndarray, holds=(5, 10, 20)):
        ok = (e >= 0) & (e < n)
        e = e[ok]
        for h in holds:
            ok2 = e + h - 1 < n
            ee = e[ok2]
            if ee.size:
                r = c[ee + h - 1] / o[ee] - 1 - cost[ee]
                acc_add(acc, f"{key}|h{h}", r, yidx[ee])

    def add_signal(key: str, mask: np.ndarray, offsets=(1, 3, 5, 10), extra_ok=None):
        m = np.asarray(mask, bool) & uni_ok
        if extra_ok is not None:
            m &= extra_ok
        idxs = np.flatnonzero(m)
        if idxs.size == 0:
            return
        for k in offsets:
            fwd_and_add(f"{key}|e{k}", idxs + k)

    ma20 = x["ma20"].to_numpy(float)
    ma60 = x["ma60"].to_numpy(float)
    ma120 = x["ma120"].to_numpy(float)
    above60 = (c > ma60) & np.isfinite(ma60)
    above20 = (c > ma20) & np.isfinite(ma20)

    base_days = np.flatnonzero(uni_ok)
    for _off in (1, 3, 5, 10):
        fwd_and_add(f"RANDOM|e{_off}", base_days + _off)

    x20 = x["x20"].to_numpy(bool)
    x60 = x["x60"].to_numpy(bool)
    x120 = x["x120"].to_numpy(bool)
    xh60 = x["xh60"].to_numpy(bool)
    xh250 = x["xh250"].to_numpy(bool)
    bbwpct = x["bbwpct"].to_numpy(float)
    t520 = x["t520"].to_numpy(float)
    align3 = x["align3"].to_numpy(bool)
    align5 = x["align5"].to_numpy(bool)
    disp20 = x["disp20"].to_numpy(float)
    dd120 = x["dd120"].to_numpy(float)
    rb60 = x["rb60"].to_numpy(float)
    sl60_20 = x["sl60_20"].to_numpy(float)

    add_signal("S1_MA20돌파", x20)
    add_signal("S2_MA20돌파_MA60위", x20, extra_ok=above60)
    add_signal("S3_MA60돌파", x60)
    add_signal("S4_MA120돌파", x120)
    add_signal("S5_60일고점돌파", xh60)
    add_signal("S6_52주신고가", xh250)
    add_signal("S7_박스수축_거래대금2배", (bbwpct < 0.30) & (t520 >= 2.0) & above20 & align3)
    add_signal("S8_낙폭반전", (dd120 <= -0.25) & (rb60 >= 0.10) & above20)
    add_signal("S9_역배열탈출", x60, extra_ok=(sl60_20 < 0))
    add_signal("S10_정배열순항", align5 & (disp20 < 0.05) & above20)
    # 거래량 패턴 단독 신호(전략 후보)
    d52w = x["d52w"].to_numpy(float)
    dh60 = x["dh60"].to_numpy(float)
    ret10 = x["ret10"].to_numpy(float)
    add_signal("S11_신고가근접_거래대금2배", (d52w > -0.05) & (t520 >= 2.0))
    add_signal("S12_60일고점근접_거래대금2배", (dh60 > -0.03) & (t520 >= 2.0))
    add_signal("S13_정배열_모멘텀_거래대금2배", align3 & (ret10 > 0.05) & (t520 >= 2.0))
    add_signal("V1_조용한매집", x["vp_quiet"].to_numpy(bool) & above20)
    add_signal("V2_점증형", x["vp_gradual"].to_numpy(bool) & above20)
    add_signal("V3_거래량동반상승", x["vp_volup"].to_numpy(float) > 0)

    # ---- 눌림목 ----
    for line, mav, cm in (("MA20", ma20, x20), ("MA60", ma60, x60), ("MA120", ma120, x120)):
        br = np.flatnonzero(cm & uni_ok)
        last_b = -999
        for b in br:
            if b - last_b < 10:
                continue
            last_b = b
            if not (np.isfinite(mav[b]) and mav[b] > 0):
                continue
            touch = -1
            mind = 1.0
            for j in range(1, 11):
                i = b + j
                if i >= n - 1:
                    break
                if not (np.isfinite(mav[i]) and mav[i] > 0):
                    break
                rel = c[i] / mav[i] - 1
                if rel < mind:
                    mind = rel
                if abs(rel) <= 0.02 and (c[i] > o[i] or c[i] > c[i - 1]):
                    touch = i
                    break
            ctx = "상승" if (np.isfinite(ma60[b]) and c[b] > ma60[b]) else "비상승"
            if touch > 0:
                depth = mind
                if depth >= -0.02:
                    dkey = "A_얕음(2%이내)"
                elif depth >= -0.05:
                    dkey = "B_보통(2~5%)"
                elif depth >= -0.10:
                    dkey = "C_깊음(5~10%)"
                else:
                    dkey = "D_이탈(10%초과)"
                e = touch + 1
                if e < n:
                    r_sets = {}
                    for h in (5, 10, 20):
                        if e + h - 1 < n:
                            r = np.array([c[e + h - 1] / o[e] - 1 - cost[e]])
                            r_sets[h] = r
                            acc_add(acc, f"PB_{line}|{dkey}|h{h}", r, yidx[[e]])
                            acc_add(acc, f"PB_{line}|전체|h{h}", r, yidx[[e]])
                            if line == "MA20":
                                acc_add(acc, f"PB_MA20|{ctx}|h{h}", r, yidx[[e]])
                            acc_add(acc, f"PB_{line}|{'급등직전' if presurge_dates[e] else '일반'}|h{h}",
                                    r, yidx[[e]])
            else:
                ok_no = True
                for j in range(1, 11):
                    i = b + j
                    if i >= n - 1:
                        ok_no = False
                        break
                    if np.isfinite(mav[i]) and mav[i] > 0 and abs(c[i] / mav[i] - 1) <= 0.02:
                        ok_no = False
                        break
                if ok_no:
                    fwd_and_add(f"PB_{line}|눌림없음", np.array([b + 5]))
    return mdf, acc


def cmd_collect(args):
    uni_all = pd.read_pickle(CACHE / "universe.pkl")
    keep = []
    for r in uni_all.itertuples():
        name = r.name or ""
        if r.end_type != "stock":
            continue
        if PREFERRED_RE.search(name) or any(k in name for k in NON_COMMON_KEYWORDS):
            continue
        if not (r.last_close and r.last_close > 0) or not (r.mktcap_eok and r.mktcap_eok > 0):
            continue
        if not (OHLCV / f"{r.code}.pkl").exists():
            continue
        keep.append((r.code, name, r.market, r.mktcap_eok * 1e8 / r.last_close))
    uni = pd.DataFrame(keep, columns=["code", "name", "market", "shares"]).set_index("code")
    idxdf = pd.read_pickle(CACHE / "index.pkl")
    print(f"[universe] {len(uni)}종목", flush=True)

    codes = uni.index.tolist()
    if args.limit:
        codes = codes[: args.limit]
    t0 = time.time()
    dfs, acc_all = [], {}
    import concurrent.futures as cf
    done = 0
    with ProcessPoolExecutor(max_workers=args.workers, initializer=init_worker,
                             initargs=(uni, idxdf)) as ex:
        i = 0
        pending = {}
        while i < len(codes) or pending:
            while i < len(codes) and len(pending) < args.workers * 2:
                pending[ex.submit(work_one, codes[i])] = codes[i]
                i += 1
            fin, _ = cf.wait(list(pending), return_when=cf.FIRST_COMPLETED)
            for f in fin:
                code = pending.pop(f)
                done += 1
                try:
                    res = f.result()
                except Exception as e:  # noqa: BLE001
                    print(f"[fail] {code}: {e}", flush=True)
                    continue
                if res is None:
                    continue
                mdf, acc = res
                if mdf is not None:
                    dfs.append(mdf)
                merge_acc(acc_all, acc)
                del res, mdf, acc
                if done % 200 == 0:
                    print(f"[collect] {done}/{len(codes)}  {time.time()-t0:.0f}s", flush=True)
    monthly = pd.concat(dfs, ignore_index=True)
    monthly.to_pickle(OUT_MONTHLY)
    with open(OUT_ACC, "wb") as fh:
        pickle.dump(acc_all, fh)
    print(f"[done] rows={len(monthly)} keys={len(acc_all)} {time.time()-t0:.0f}s", flush=True)
    print(monthly["M"].value_counts().sort_index().to_string())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["collect", "analyze"])
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    if args.cmd == "collect":
        cmd_collect(args)
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import monthly_surge_analyze as an
        an.run()


if __name__ == "__main__":
    main()
