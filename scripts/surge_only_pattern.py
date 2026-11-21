#!/usr/bin/env python3
"""월봉 +20% 급등 '종목만' 뽑아서, 그 직전 2~3주 형태만 분석한다.

사용자 지시(반복 강조): 통제집단·전 종목 평균과 비교하지 말고, 급등한 종목의
급등 직전 3주 모양만 사진 찍듯 기술하고 유형화한 뒤, 유형별 '급등 달 시작 전 매수'
성과와 상위(크게 오른 쪽) vs 하위(겨우 +20%)의 형태 차이를 본다.

산출물
  stock-insight/data/surge-pattern.json   (사례별 특징 + 유형 + 유형 요약)
  work/backtest/surge-only-pattern-report.md (표는 build_report 단계에서)

사용법
  python3 scripts/surge_only_pattern.py build --workers 4
  python3 scripts/surge_only_pattern.py types      # 유형 분포만 빠르게 확인
  python3 scripts/surge_only_pattern.py rules      # 매매법 학습/검증
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import re
import time
import warnings
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

REPO = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("BACKTEST_CACHE", REPO.parent / "work" / "backtest" / "cache"))
OHLCV = CACHE / "ohlcv"
WORK = REPO.parent / "work" / "backtest"
OUT_JSON = REPO / "data" / "surge-pattern.json"
OUT_CASES = WORK / "surge_only_cases.pkl"

MIN_MKTCAP = 50_000_000_000
MIN_VALUE = 10_000_000_000
SURGE = 0.20
SLIP = 0.001
COMM = 0.00015
TAX_BY_YEAR = {2015: 0.0030, 2016: 0.0030, 2017: 0.0030, 2018: 0.0030, 2019: 0.0030,
               2020: 0.0025, 2021: 0.0023, 2022: 0.0023, 2023: 0.0020, 2024: 0.0018,
               2025: 0.0015, 2026: 0.0020}

PREFERRED_RE = re.compile(r"(?:\d?우B?C?)$|우\(|우B$")
NON_COMMON_KEYWORDS = ("스팩", "리츠", "인프라", "ETN", "ETF", "선물", "레버리지", "인버스", "합성")

W = 15          # 급등 달 직전 15거래일 = 약 3주
HOLDS = (20, 30, 40)   # 보유·평가 기간: 한 달(20) · 30 · 40거래일
SNAP_AT = (15, 10, 5, 0)  # T-15(3주 전) · T-10(2주 전) · T-5 · T


# ----------------------------------------------------------------- 유니버스
def load_universe() -> pd.DataFrame:
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
    return pd.DataFrame(keep, columns=["code", "name", "market", "shares"]).set_index("code")


def days_since(b: np.ndarray) -> np.ndarray:
    idx = np.arange(b.size)
    last = np.maximum.accumulate(np.where(b, idx, -1))
    return np.where(last >= 0, idx - last, 9999)


def rolling_max_prev(a: np.ndarray, n: int) -> np.ndarray:
    """직전 n개(자기 자신 제외) 최고값."""
    s = pd.Series(a).rolling(n).max().shift(1).to_numpy(float)
    return s


def prep(d: pd.DataFrame) -> dict:
    c = d["aclose"].to_numpy(float)
    o = d["aopen"].to_numpy(float)
    h = d["ahigh"].to_numpy(float)
    l = d["alow"].to_numpy(float)
    v = d["volume"].to_numpy(float)
    tf = d["turnover"].to_numpy(float)
    n = c.size

    def rmax(a, k):
        return pd.Series(a).rolling(k).max().to_numpy(float)

    ma = {k: pd.Series(c).rolling(k).mean().to_numpy(float) for k in (5, 20, 60, 120, 240)}
    ret1 = np.r_[np.nan, c[1:] / c[:-1] - 1]
    pc = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.vstack([h - l, np.abs(h - pc), np.abs(l - pc)]), axis=0)
    atr14 = pd.Series(tr).rolling(14).mean().to_numpy(float) / c

    v20 = pd.Series(v).rolling(20).mean().to_numpy(float)
    v10 = pd.Series(v).rolling(10).mean().to_numpy(float)
    v5 = pd.Series(v).rolling(5).mean().to_numpy(float)
    v3 = pd.Series(v).rolling(3).mean().to_numpy(float)
    vr = v / v20
    t20 = pd.Series(tf).rolling(20).mean().to_numpy(float)
    t5 = pd.Series(tf).rolling(5).mean().to_numpy(float)

    hi = {k: rolling_max_prev(h, k) for k in (20, 60, 120, 250)}
    body = np.abs(c - o)
    rng = np.where((h - l) > 0, h - l, np.nan)
    upshadow = h - np.maximum(c, o)
    dnshadow = np.minimum(c, o) - l
    gapup = np.r_[False, o[1:] > h[:-1]]

    return dict(c=c, o=o, h=h, l=l, v=v, tf=tf, n=n, ma=ma, ret1=ret1, atr14=atr14,
                v20=v20, v10=v10, v5=v5, v3=v3, vr=vr, t20=t20, t5=t5, hi=hi,
                body=body, rng=rng, upshadow=upshadow, dnshadow=dnshadow, gapup=gapup)


def slope_pct(a: np.ndarray, i: int, k: int = 20) -> float:
    if i - k < 0:
        return np.nan
    x, y = a[i - k], a[i]
    if not (np.isfinite(x) and np.isfinite(y) and x > 0):
        return np.nan
    return y / x - 1


def breakout_day(c: np.ndarray, line: np.ndarray, T: int, w: int = W):
    """T-w ~ T 사이에 종가가 line을 아래→위로 넘은 가장 최근 날의 '며칠 전' (없으면 None)."""
    for k in range(0, w + 1):
        i = T - k
        if i - 1 < 0:
            break
        if not (np.isfinite(line[i]) and np.isfinite(line[i - 1])):
            continue
        if c[i] > line[i] and c[i - 1] <= line[i - 1]:
            return k
    return None


def window_features(p: dict, T: int) -> dict:
    c, o, h, l, v = p["c"], p["o"], p["h"], p["l"], p["v"]
    W0 = max(0, T - W)
    f: dict = {}
    f["T"] = T
    f["win_start"] = W0

    # --- 이평선 위치/이격/배열 ---
    for k in (5, 20, 60, 120, 240):
        m = p["ma"][k][T]
        f[f"ma{k}"] = m
        f[f"disp{k}"] = c[T] / m - 1 if (np.isfinite(m) and m > 0) else np.nan
        f[f"above{k}"] = bool(np.isfinite(m) and c[T] > m)
    ma5, ma20, ma60, ma120, ma240 = (p["ma"][k][T] for k in (5, 20, 60, 120, 240))
    f["align3"] = bool(np.isfinite(ma60) and ma5 > ma20 > ma60)
    f["align4"] = bool(np.isfinite(ma120) and ma5 > ma20 > ma60 > ma120)
    f["align5"] = bool(np.isfinite(ma240) and ma5 > ma20 > ma60 > ma120 > ma240)
    f["inverse3"] = bool(np.isfinite(ma60) and ma5 < ma20 < ma60)
    f["sl20_20"] = slope_pct(p["ma"][20], T, 20)
    f["sl60_20"] = slope_pct(p["ma"][60], T, 20)
    f["sl120_20"] = slope_pct(p["ma"][120], T, 20)

    def cls(s):
        if not np.isfinite(s):
            return "na"
        return "up" if s > 0.01 else ("down" if s < -0.01 else "flat")
    f["sl60_cls"] = cls(f["sl60_20"])
    f["sl20_cls"] = cls(f["sl20_20"])

    # --- 캔들이 넘은 선 ---
    f["br_ma20"] = breakout_day(c, p["ma"][20], T)
    f["br_ma60"] = breakout_day(c, p["ma"][60], T)
    f["br_ma120"] = breakout_day(c, p["ma"][120], T)
    f["br_ma240"] = breakout_day(c, p["ma"][240], T)
    f["br_h20"] = breakout_day(c, p["hi"][20], T)
    f["br_h60"] = breakout_day(c, p["hi"][60], T)
    f["br_h120"] = breakout_day(c, p["hi"][120], T)
    f["br_h250"] = breakout_day(c, p["hi"][250], T)
    # 하락추세선(60일 로그회귀) 상향 이탈
    dt = None
    if T >= 60:
        y = np.log(np.maximum(c[T - 59:T + 1], 1e-9))
        x = np.arange(60)
        b, a = np.polyfit(x, y, 1)
        fit = a + b * x
        f["reg_slope60"] = b * 20  # 20일당 로그 변화
        if b < 0:
            for k in range(0, W + 1):
                i = T - k
                if i - 59 < 0 or i - 60 < 0:
                    break
                yi = np.log(max(c[i], 1e-9))
                yi1 = np.log(max(c[i - 1], 1e-9))
                # 각 시점의 회귀선 값(그 시점까지 60일로 재적합하지 않고, T 기준 선을 연장)
                off = a + b * (59 - k)
                off1 = a + b * (59 - (k + 1))
                if yi > off and yi1 <= off1:
                    dt = k
                    break
    if dt is None:
        dt = None
    f["br_dtl"] = dt
    f["reg_slope60"] = f.get("reg_slope60", np.nan)
    f["n_break"] = int(sum(1 for key in ("br_ma20", "br_ma60", "br_ma120", "br_ma240",
                                         "br_h20", "br_h60", "br_h120", "br_h250", "br_dtl")
                           if f[key] is not None))
    f["n_break_price"] = int(sum(1 for key in ("br_h20", "br_h60", "br_h120", "br_h250")
                                 if f[key] is not None))

    # --- 눌림 ---
    wc = c[W0:T + 1]
    hi_i = int(np.argmax(wc))
    f["win_ret"] = c[T] / c[W0] - 1 if c[W0] > 0 else np.nan
    f["from_win_high"] = c[T] / wc[hi_i] - 1
    if hi_i < wc.size - 1:
        f["pull_depth"] = wc[hi_i:].min() / wc[hi_i] - 1
        f["pull_days"] = int(wc.size - 1 - hi_i)
    else:
        f["pull_depth"] = 0.0
        f["pull_days"] = 0
    f["win_range"] = (h[W0:T + 1].max() - l[W0:T + 1].min()) / l[W0:T + 1].min()
    f["atr"] = p["atr14"][T]
    f["vol_real"] = np.nanstd(p["ret1"][W0 + 1:T + 1])

    # --- 거래량 ---
    f["v5_20"] = p["v5"][T] / p["v20"][T]
    f["v10_20"] = p["v10"][T] / p["v20"][T]
    f["v3_20"] = p["v3"][T] / p["v20"][T]
    f["vmax15"] = float(np.nanmax(p["vr"][W0:T + 1]))
    f["nday2x"] = int(np.nansum((p["vr"][W0:T + 1] >= 2.0).astype(float)))
    f["t5_20"] = p["t5"][T] / p["t20"][T]
    f["tmax15"] = float(np.nanmax((p["tf"][W0:T + 1] / p["t20"][T])))

    vshape = "중립"
    if f["vmax15"] >= 3.0 and f["nday2x"] <= 2:
        vshape = "단발폭발"
    elif f["v10_20"] < 0.85 and f["vmax15"] < 2.0:
        vshape = "조용·마름"
    elif (p["v3"][T] > p["v5"][T] > p["v10"][T]) and f["v10_20"] >= 1.2:
        vshape = "점증"
    elif f["v5_20"] >= 1.5:
        vshape = "확산"
    f["vshape"] = vshape

    # --- 연속 상승일 ---
    r = p["ret1"][W0:T + 1]
    best = cur = 0
    for x_ in r:
        if np.isfinite(x_) and x_ > 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    f["up_streak_max"] = best
    f["up_streak_now"] = cur
    f["nday_up"] = int(np.nansum((r > 0).astype(float)))
    f["nday_dn"] = int(np.nansum((r < 0).astype(float)))

    # --- 신고가 대비 ---
    f["d52w"] = c[T] / np.nanmax(h[max(0, T - 249):T + 1]) - 1
    f["dh60"] = c[T] / np.nanmax(h[max(0, T - 59):T + 1]) - 1
    f["dh120"] = c[T] / np.nanmax(h[max(0, T - 119):T + 1]) - 1
    f["dh15"] = f["from_win_high"]

    # --- 캔들 모양 ---
    bw = p["body"][W0:T + 1]
    rw = p["rng"][W0:T + 1]
    f["n_bigyang"] = int(np.nansum((p["ret1"][W0:T + 1] >= 0.05).astype(float)))
    f["n_yang"] = int(np.nansum((c[W0:T + 1] > o[W0:T + 1]).astype(float)))
    with np.errstate(invalid="ignore"):
        f["n_lower_tail"] = int(np.nansum(((p["dnshadow"][W0:T + 1] >= 2 * bw) &
                                           (p["dnshadow"][W0:T + 1] / c[W0:T + 1] >= 0.015) &
                                           (p["dnshadow"][W0:T + 1] > 0)).astype(float)))
        f["n_doji"] = int(np.nansum(((bw <= 0.15 * rw) & np.isfinite(rw)).astype(float)))
    f["n_gapup"] = int(np.nansum(p["gapup"][W0 + 1:T + 1].astype(float)))
    f["max_ret1"] = float(np.nanmax(p["ret1"][W0:T + 1]))
    f["ret10"] = c[T] / c[T - 10] - 1 if T >= 10 else np.nan
    f["ret5"] = c[T] / c[T - 5] - 1 if T >= 5 else np.nan
    f["candle_last"] = "양봉" if c[T] > o[T] else ("음봉" if c[T] < o[T] else "보합")
    return f


def has(x) -> bool:
    """breakout_day 결과(며칠 전 또는 None)가 실제로 존재하는지 — NaN은 없음."""
    try:
        return x is not None and np.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def classify(f: dict) -> str:
    """급등 직전 3주 형태 — 규칙 기반 배타 배정(위에서부터 먼저 걸리는 유형, T1~T8)."""
    a3 = f["align3"]
    win_ret = f["win_ret"]
    d52 = f["d52w"]
    disp20 = f["disp20"]
    above60 = f["above60"]
    near_high = np.isfinite(d52) and d52 > -0.05

    # T1 신고가·전고점 돌파
    if has(f["br_h250"]) or has(f["br_h120"]) or (near_high and f["dh60"] > -0.03 and win_ret > 0):
        return "T1_신고가·전고점 돌파"
    # T2 과열 연속상승 (3주에 이미 +20% 이상 달림)
    if win_ret >= 0.20 and np.isfinite(disp20) and disp20 > 0.08:
        return "T2_과열 연속상승"
    # T3 정배열 가속 (눌림 후 재상승 포함)
    if a3 and np.isfinite(disp20) and -0.02 <= disp20 <= 0.08 and (
            f["sl60_cls"] == "up" or (f["pull_depth"] <= -0.05 and f["from_win_high"] > -0.04)):
        return "T3_정배열 가속"
    # T4 박스권 상단 돌파
    if (has(f["br_h20"]) or (np.isfinite(f["dh15"]) and f["dh15"] > -0.03)) and f["win_range"] <= 0.30 \
            and (has(f["br_h60"]) or f["dh60"] > -0.08):
        return "T4_박스 상단 돌파"
    # T5 역배열 탈출 (60일선 아래였는데 3주 안에 60/120일선 회복)
    if above60 and not a3 and (has(f["br_ma60"]) or has(f["br_ma120"])):
        return "T5_역배열 탈출"
    # T6 조용한 횡보·수축
    if f["win_range"] <= 0.15 and f["v10_20"] < 1.0 and abs(win_ret) < 0.05:
        return "T6_조용한 횡보"
    # T7 60일선 아래·20일선 회복 (단기 반등)
    if (not above60) and np.isfinite(disp20) and disp20 > 0:
        return "T7_MA60 아래·단기 반등"
    # T8 60일선·20일선 모두 아래 (하락 지속 중)
    return "T8_MA60·MA20 모두 아래"


def snap_at(p: dict, i: int) -> dict:
    """i일 종가 시점의 차트 상태(그 시점까지의 정보만)."""
    c = p["c"]
    if i < 0 or i >= p["n"]:
        return {}
    d = {}
    d["date"] = None
    d["close"] = float(c[i])
    for k in (20, 60, 120):
        m = p["ma"][k][i]
        d[f"disp{k}"] = float(c[i] / m - 1) if (np.isfinite(m) and m > 0) else None
        d[f"above{k}"] = bool(np.isfinite(m) and c[i] > m)
    ma5, ma20, ma60 = p["ma"][5][i], p["ma"][20][i], p["ma"][60][i]
    d["align3"] = bool(np.isfinite(ma60) and ma5 > ma20 > ma60)
    d["v5_20"] = float(p["v5"][i] / p["v20"][i]) if p["v20"][i] else None
    d["v10_20"] = float(p["v10"][i] / p["v20"][i]) if p["v20"][i] else None
    d["d52w"] = float(c[i] / np.nanmax(p["h"][max(0, i - 249):i + 1]) - 1)
    d["dh60"] = float(c[i] / np.nanmax(p["h"][max(0, i - 59):i + 1]) - 1)
    d["ret10"] = float(c[i] / c[i - 10] - 1) if i >= 10 else None
    j0 = max(0, i - 19)
    d["win_range20"] = float((p["h"][j0:i + 1].max() - p["l"][j0:i + 1].min()) / p["l"][j0:i + 1].min())
    brs = []
    for label, key in (("MA20", 20), ("MA60", 60), ("MA120", 120)):
        if has(breakout_day(c, p["ma"][key], i, 10)):
            brs.append(label)
    if has(breakout_day(c, p["hi"][60], i, 10)):
        brs.append("60일고점")
    if has(breakout_day(c, p["hi"][250], i, 10)):
        brs.append("52주신고가")
    d["brs"] = brs
    j0 = max(0, i - 10)
    wc = c[j0:i + 1]
    hi_i = int(np.argmax(wc))
    d["pull"] = float(wc[hi_i:].min() / wc[hi_i] - 1) if hi_i < wc.size - 1 else 0.0
    return d


def build_case(p: dict, code, name, market, dates, T, M_end, Mlab, ret, mktcap, cost_arr, shares):
    c, o = p["c"], p["o"]
    n = p["n"]
    f = window_features(p, T)
    f["code"] = code
    f["name"] = name
    f["market"] = market
    f["M"] = Mlab
    f["T_date"] = str(dates[T].date())
    f["ret"] = ret
    f["mktcap"] = mktcap
    f["mktcap_class"] = "대형" if mktcap >= 1e12 else ("중형" if mktcap >= 3e11 else "소형")
    # 유형은 창 특징이 모두 채워진 뒤 분류
    f["type"] = None
    e = T + 1                                   # 급등 달 첫 거래일 = 진입일(시가)
    f["entry_date"] = str(dates[e].date()) if e < n else None
    for hd in HOLDS:
        f[f"r{hd}"] = np.nan
        f[f"mfe{hd}"] = np.nan
        f[f"mae{hd}"] = np.nan
        if e < n and e + hd - 1 < n:
            f[f"r{hd}"] = float(c[e + hd - 1] / o[e] - 1 - cost_arr[e])
            f[f"mfe{hd}"] = float(np.nanmax(p["h"][e:e + hd]) / o[e] - 1)
            f[f"mae{hd}"] = float(np.nanmin(p["l"][e:e + hd]) / o[e] - 1)
    # 참고: 급등이 '확인된 뒤'(급등 달 종료 다음 거래일 시가) 매수했을 때의 20일 수익
    e2 = M_end + 1
    for hd in HOLDS:
        f[f"r{hd}_after"] = np.nan
        if e2 < n and e2 + hd - 1 < n:
            f[f"r{hd}_after"] = float(c[e2 + hd - 1] / o[e2] - 1 - cost_arr[e2])
    # 시점별 스냅샷 4장 (T-15 / T-10 / T-5 / T) — 차트·서술용
    f["snap"] = [snap_at(p, T - k) for k in SNAP_AT]
    # 정규화 경로 (T-40 ~ T+40) — 유형 평균 차트용
    path = []
    for off in range(-40, 41):
        j = T + off
        path.append(float(c[j] / c[T]) if 0 <= j < n and c[T] > 0 else np.nan)
    f["path"] = path
    f["open_next"] = float(o[e]) if e < n else np.nan
    return f


def work_one(code: str):
    row = UNI.loc[code]
    path = OHLCV / f"{code}.pkl"
    if not path.exists():
        return None
    d = pd.read_pickle(path)
    if d.empty or len(d) < 300:
        return None
    p = prep(d)
    n = p["n"]
    c = p["c"]
    dates = d.index
    mktcap = d["close"].to_numpy(float) * float(row["shares"])
    t20 = p["t20"]
    uni_ok = (mktcap >= MIN_MKTCAP) & (t20 >= MIN_VALUE) & (p["v"] > 0)
    pm = dates.year.to_numpy() * 12 + dates.month.to_numpy()
    nlast = np.r_[np.flatnonzero(pm[:-1] != pm[1:]), n - 1]
    cost = np.array([2 * SLIP + 2 * COMM + TAX_BY_YEAR.get(int(y), 0.0020) for y in dates.year])
    out = []
    for k in range(1, len(nlast)):
        T = int(nlast[k - 1])
        Mm = int(nlast[k])
        if pm[Mm] - pm[T] != 1:
            continue
        Mlab = f"{dates[Mm].year:04d}-{dates[Mm].month:02d}"
        if Mlab >= "2026-11":
            continue
        if not uni_ok[T]:
            continue
        if not (np.isfinite(c[T]) and c[T] > 0 and np.isfinite(c[Mm]) and c[Mm] > 0):
            continue
        if not np.isfinite(p["ma"][240][T]):
            continue
        ret = float(c[Mm] / c[T] - 1)
        if ret < SURGE:
            continue
        out.append(build_case(p, code, row["name"], row["market"], dates, T, Mm, Mlab, ret,
                              float(mktcap[T]), cost, float(row["shares"])))
    return out


UNI = None


def init_worker(uni):
    global UNI
    UNI = uni


def cmd_build(args):
    global UNI
    uni = load_universe()
    UNI = uni
    codes = uni.index.tolist()
    if args.limit:
        codes = codes[: args.limit]
    t0 = time.time()
    cases = []
    if args.workers <= 1:
        init_worker(uni)
        for i, code in enumerate(codes):
            r = work_one(code)
            if r:
                cases.extend(r)
            if (i + 1) % 200 == 0:
                print(f"[build] {i+1}/{len(codes)} cases={len(cases)} {time.time()-t0:.0f}s", flush=True)
    else:
        import concurrent.futures as cf
        with ProcessPoolExecutor(max_workers=args.workers, initializer=init_worker,
                                 initargs=(uni,)) as ex:
            futs = {ex.submit(work_one, cd): cd for cd in codes}
            done = 0
            for fu in cf.as_completed(futs):
                done += 1
                try:
                    r = fu.result()
                except Exception as e:  # noqa: BLE001
                    print(f"[fail] {futs[fu]}: {e}", flush=True)
                    continue
                if r:
                    cases.extend(r)
                if done % 300 == 0:
                    print(f"[build] {done}/{len(codes)} cases={len(cases)} {time.time()-t0:.0f}s", flush=True)
    df = pd.DataFrame(cases)
    df["type"] = [classify(r) for r in df.to_dict("records")]
    df.to_pickle(OUT_CASES)
    print(f"[done] {len(df)}건 · {df['code'].nunique()}종목 · {time.time()-t0:.0f}s")
    print(df["type"].value_counts().to_string())
    print(df["M"].value_counts().sort_index().tail(10).to_string())


def cmd_types(args):
    df = pd.read_pickle(OUT_CASES)
    print(len(df), df["code"].nunique())
    print(df["type"].value_counts().to_string())
    print(df["vshape"].value_counts().to_string())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "types", "rules"])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    if args.cmd == "build":
        cmd_build(args)
    elif args.cmd == "types":
        cmd_types(args)
    else:
        import surge_playbook_rules as rl
        rl.run()


if __name__ == "__main__":
    main()
