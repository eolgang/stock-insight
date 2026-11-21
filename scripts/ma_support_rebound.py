#!/usr/bin/env python3
"""과제 1: 신고가 근처 눌림 → MA20 지지 → 반등 패턴 실측 (MA20·MA60 축, 보유 5/10/20/30/40일).

사용자 지시(2026-11-21 통화):
  - 21일선 없음. MA20만. (MA60은 관계·지지 축으로 추가)
  - 캔들과 MA20의 '관계 흐름'(접촉 횟수·이격도 추이·기울기)과 '거래량의 점진적 증가'가 핵심 관찰 대상
  - 보유기간 5·10·20·30·40거래일 확장, MFE/MAE도 30·40일로
데이터: work/backtest/cache/ohlcv/*.pkl (수정주가 OHLC, 2015-01-02~2026-11-20). 새로 수집하지 않음.
출력: data/ma-support-rebound.json, work/backtest/ma_support_raw.json,
      work/backtest/ma_support_example.json (HD현대일렉트릭 2025-09), work/backtest/ma_support_checklist.csv

사용법: python3 scripts/ma_support_rebound.py [--workers 2] [--limit 0]
"""
from __future__ import annotations

import argparse
import json
import os
import re
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

MIN_MKTCAP = 50_000_000_000
MIN_VALUE = 10_000_000_000
SLIP = 0.001
COMM = 0.00015
TAX_BY_YEAR = {2015: 0.0030, 2016: 0.0030, 2017: 0.0030, 2018: 0.0030, 2019: 0.0030,
               2020: 0.0025, 2021: 0.0023, 2022: 0.0023, 2023: 0.0020, 2024: 0.0018,
               2025: 0.0015, 2026: 0.0020}
HORIZONS = (5, 10, 20, 30, 40)
SIGNAL_START = "2016-01-01"

PREFERRED_RE = re.compile(r"(?:\d?우B?C?)$|우\(|우B$")
NON_COMMON_KEYWORDS = ("스팩", "리츠", "인프라", "ETN", "ETF", "선물", "레버리지", "인버스", "합성")

# 히스토그램(중앙값 계산용) — 수익률
RB_LO, RB_BW, RB_N = -0.80, 0.002, 800
R_CENTERS = RB_LO + (np.arange(RB_N) + 0.5) * RB_BW


# ---------------------------------------------------------------------------
# 누적기
# ---------------------------------------------------------------------------
def _blank() -> dict:
    return {"n": 0, "s": 0.0, "ss": 0.0, "hist": None, "npos": 0, "spos": 0.0, "nneg": 0, "sneg": 0.0,
            "mfe_s": 0.0, "mae_s": 0.0, "kos_s": 0.0, "kos_n": 0, "kos_win": 0,
            "yn": None, "ys": None, "yw": None}


class Acc:
    """(variant|h) 별 스트리밍 통계. 메모리 고정."""

    def __init__(self, nyear: int):
        self.d: dict[str, dict] = {}
        self.nyear = nyear

    def add(self, key: str, r: np.ndarray, mfe: np.ndarray, mae: np.ndarray,
            kos: np.ndarray | None, years: np.ndarray) -> None:
        if r.size == 0:
            return
        ok = np.isfinite(r)
        if not ok.all():
            r, mfe, mae, years = r[ok], mfe[ok], mae[ok], years[ok]
            kos = kos[ok] if kos is not None else None
        n = r.size
        if n == 0:
            return
        a = self.d.get(key)
        if a is None:
            a = _blank()
            a["hist"] = np.zeros(RB_N, np.int32)
            a["yn"] = np.zeros(self.nyear, np.int64)
            a["ys"] = np.zeros(self.nyear)
            a["yw"] = np.zeros(self.nyear)
            self.d[key] = a
        a["n"] += n
        a["s"] += float(r.sum())
        a["ss"] += float((r * r).sum())
        pos = r > 0
        a["npos"] += int(pos.sum())
        if pos.any():
            a["spos"] += float(r[pos].sum())
        neg = r < 0
        a["nneg"] += int(neg.sum())
        if neg.any():
            a["sneg"] += float(r[neg].sum())
        bins = np.clip(((r - RB_LO) / RB_BW).astype(np.int32), 0, RB_N - 1)
        a["hist"] += np.bincount(bins, minlength=RB_N)
        a["mfe_s"] += float(np.nansum(mfe))
        a["mae_s"] += float(np.nansum(mae))
        if kos is not None:
            ko = np.isfinite(kos)
            if ko.any():
                a["kos_s"] += float(kos[ko].sum())
                a["kos_n"] += int(ko.sum())
                a["kos_win"] += int((kos[ko] > 0).sum())
        yi = np.clip(years - 2016, 0, self.nyear - 1)
        a["yn"] += np.bincount(yi, minlength=self.nyear)
        a["ys"] += np.bincount(yi, weights=r, minlength=self.nyear)
        a["yw"] += np.bincount(yi, weights=pos.astype(float), minlength=self.nyear)

    def merge(self, other: "Acc") -> None:
        for k, v in other.d.items():
            a = self.d.get(k)
            if a is None:
                self.d[k] = v
                continue
            for f in ("n", "s", "ss", "npos", "spos", "nneg", "sneg", "mfe_s", "mae_s",
                      "kos_s", "kos_n", "kos_win"):
                a[f] += v[f]
            a["hist"] += v["hist"]
            a["yn"] += v["yn"]
            a["ys"] += v["ys"]
            a["yw"] += v["yw"]

    def stats(self, key: str, base: dict | None = None, kos_mean: float | None = None) -> dict:
        a = self.d.get(key)
        if a is None or a["n"] == 0:
            return {"n": 0}
        n = a["n"]
        mean = a["s"] / n
        var = max(a["ss"] / n - mean * mean, 0.0)
        sd = var ** 0.5
        cum = np.cumsum(a["hist"])
        med = float(R_CENTERS[min(int(np.searchsorted(cum, n / 2)), RB_N - 1)])
        out = {"n": int(n), "mean": mean, "median": med, "sd": sd, "se": sd / n ** 0.5,
               "win": a["npos"] / n,
               "payoff": (a["spos"] / a["npos"]) / abs(a["sneg"] / a["nneg"]) if a["npos"] and a["nneg"] and a["sneg"] else None,
               "mfe": a["mfe_s"] / n, "mae": a["mae_s"] / n,
               "kos": (a["kos_s"] / a["kos_n"]) if a["kos_n"] else None,
               "excess_kos": (mean - a["kos_s"] / a["kos_n"]) if a["kos_n"] else None}
        if base and base.get("n"):
            out["excess"] = mean - base["mean"]
            out["sig"] = out["excess"] / out["se"] if out["se"] > 0 else None
            out["sig_kos"] = ((mean - kos_mean) / out["se"]) if kos_mean is not None and out["se"] > 0 else None
        yi = a["yn"]
        out["years"] = {str(2016 + i): {"n": int(yi[i]), "mean": float(a["ys"][i] / yi[i]),
                                        "win": float(a["yw"][i] / yi[i])}
                        for i in range(self.nyear) if yi[i] > 0}
        yi2 = np.arange(2016, 2016 + self.nyear)
        for lab, lo, hi in (("split_2016_2020", 2016, 2020), ("split_2021_2026", 2021, 2026)):
            m = (yi2 >= lo) & (yi2 <= hi) & (yi > 0)
            nn = int(yi[m].sum())
            out[lab] = {"n": nn, "mean": float(a["ys"][m].sum() / nn) if nn else None,
                        "win": float(a["yw"][m].sum() / nn) if nn else None}
        return out


# ---------------------------------------------------------------------------
# 지표
# ---------------------------------------------------------------------------
def reg_slope(y: np.ndarray, win: int = 10) -> np.ndarray:
    """최근 win일 선형회귀 기울기 / 창 평균 (하루당 변화율). 완전 벡터화."""
    y = np.asarray(y, dtype=float)
    n = len(y)
    out = np.full(n, np.nan)
    if n < win:
        return out
    idx = np.arange(n, dtype=float)
    cy = np.cumsum(np.nan_to_num(y))
    ciy = np.cumsum(idx * np.nan_to_num(y))
    Cn = np.cumsum(np.isfinite(y).astype(float))
    t = np.arange(win - 1, n)
    S1 = cy[t] - np.where(t - win >= 0, cy[t - win], 0.0)
    SI = ciy[t] - np.where(t - win >= 0, ciy[t - win], 0.0)
    cnt = Cn[t] - np.where(t - win >= 0, Cn[t - win], 0.0)
    start = t - win + 1
    S2 = SI - start * S1
    D = win * (win * win - 1) / 12.0
    slope = (S2 - (win - 1) / 2.0 * S1) / D
    mean = S1 / win
    with np.errstate(divide="ignore", invalid="ignore"):
        out[t] = np.where((mean > 0) & (cnt == win), slope / mean, np.nan)
    return out


def prep(d: pd.DataFrame, shares: float, idx: pd.DataFrame) -> pd.DataFrame:
    x = pd.DataFrame(index=d.index)
    c = d["aclose"].astype(float)
    o = d["aopen"].astype(float)
    h = d["ahigh"].astype(float)
    l = d["alow"].astype(float)
    v = d["volume"].astype(float)
    tf = d["turnover"].astype(float)
    x["c"], x["o"], x["h"], x["l"] = c, o, h, l
    x["ret1"] = c.pct_change()

    for n in (5, 20, 30, 60, 120):
        m = c.rolling(n).mean()
        x[f"ma{n}"] = m
        x[f"disp{n}"] = c / m - 1
    x["align3"] = (x["ma5"] > x["ma20"]) & (x["ma20"] > x["ma60"])
    x["align4"] = x["align3"] & (x["ma60"] > x["ma120"])
    x["sl20"] = x["ma20"] / x["ma20"].shift(20) - 1
    x["sl30"] = x["ma30"] / x["ma30"].shift(20) - 1
    x["sl60"] = x["ma60"] / x["ma60"].shift(20) - 1

    x["d52"] = c / h.rolling(250).max() - 1
    x["hh10"] = h.rolling(10).max()
    x["depth10"] = c / x["hh10"] - 1
    x["ll10"] = l.rolling(10).min()
    x["above20"] = c > x["ma20"]
    x["above60"] = c > x["ma60"]
    x["cross20"] = x["above20"] & (~x["above20"].shift(1).fillna(True).astype(bool))
    x["cross60"] = x["above60"] & (~x["above60"].shift(1).fillna(True).astype(bool))
    x["up_candle"] = (c > o) | (c > c.shift(1))

    v20 = v.rolling(20).mean()
    v10 = v.rolling(10).mean()
    v5 = v.rolling(5).mean()
    x["vr"] = v / v20
    x["v520"] = v5 / v20
    x["v10_20"] = v10 / v20
    x["vs10"] = reg_slope(v.to_numpy(), 10)
    x["vs20"] = reg_slope(v.to_numpy(), 20)
    x["vr_max10"] = x["vr"].rolling(10).max()

    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    x["atr14"] = tr.rolling(14).mean() / c
    x["yang5"] = (c > o).rolling(5).mean()

    # MA20 접촉 횟수(최근 10일, |종가/MA20-1|<=2%)
    near = (x["disp20"].abs() <= 0.02).fillna(False).astype(int)
    x["touch10"] = near.rolling(10).sum()
    # MA20 이격도 5일 전 대비
    x["d20_chg5"] = x["disp20"] - x["disp20"].shift(5)

    fp = d["foreign_pct"].astype(float)
    x["f5"] = fp - fp.shift(5)
    x["mktcap"] = d["close"].astype(float) * shares
    x["t20"] = tf.rolling(20).mean()
    x["vol0"] = v > 0
    k = idx["close_k"].reindex(x.index).ffill()
    x["kospi_ma20"] = k.rolling(20).mean()
    x["bull"] = (k > x["kospi_ma20"]).fillna(False)
    return x


def dy(years: np.ndarray) -> np.ndarray:
    return np.where(years >= 2016, np.clip(years - 2016, 0, 10), 0)


# ---------------------------------------------------------------------------
# 종목 1개 처리
# ---------------------------------------------------------------------------
UNI = None
IDX = None
SINCE = pd.Timestamp(SIGNAL_START)


def init_worker(uni, idx):
    global UNI, IDX
    UNI, IDX = uni, idx


def build_variants(x: pd.DataFrame) -> dict[str, np.ndarray]:
    """모든 변형의 불리언 마스크(길이 len(x)). 유니버스 필터는 여기서 적용하지 않음."""
    c = x["c"].to_numpy(float)
    m20 = x["ma20"].to_numpy(float)
    m30 = x["ma30"].to_numpy(float)
    m60 = x["ma60"].to_numpy(float)
    d20 = x["disp20"].to_numpy(float)
    d30 = x["disp30"].to_numpy(float)
    d60 = x["disp60"].to_numpy(float)
    d52 = x["d52"].to_numpy(float)
    depth = x["depth10"].to_numpy(float)
    vr = x["vr"].to_numpy(float)
    v520 = x["v520"].to_numpy(float)
    v10_20 = x["v10_20"].to_numpy(float)
    vs10 = x["vs10"].to_numpy(float)
    sl20 = x["sl20"].to_numpy(float)
    sl60 = x["sl60"].to_numpy(float)
    close_rel20 = d20
    low_rel20 = x["l"].to_numpy(float) / m20 - 1
    low_rel60 = x["l"].to_numpy(float) / m60 - 1
    rebound = x["up_candle"].to_numpy(bool)
    align3 = x["align3"].to_numpy(bool)
    align4 = x["align4"].to_numpy(bool)
    above20 = x["above20"].to_numpy(bool)
    above60 = x["above60"].to_numpy(bool)
    touch10 = x["touch10"].to_numpy(float)
    d20_chg5 = x["d20_chg5"].to_numpy(float)
    bull = x["bull"].to_numpy(bool)
    gap = (m20 / m60 - 1)
    nan = np.isnan

    V = {}

    def put(k, m):
        V[k] = np.nan_to_num(m.astype(bool), nan=False) if m.dtype != bool else m

    sup_low = lambda w: (low_rel20 >= -w) & (low_rel20 <= w)          # 저가가 MA20 밴드 안
    sup_close = lambda w: (close_rel20 >= -w) & (close_rel20 <= w)    # 종가가 MA20 밴드 안
    sup_any = lambda w: sup_low(w) | sup_close(w)
    pull = (depth >= -0.12) & (depth <= -0.03)

    # --- 1) 단계 사다리 (a)→(e), 신고가 기준별
    for nh in (0.10, 0.05, 0.03, 0.02):
        tag = f"nh{int(nh*100)}"
        a = d52 >= -nh
        put(f"STEP|{tag}|1_a", a)
        put(f"STEP|{tag}|2_ab", a & align3)
        put(f"STEP|{tag}|3_abc", a & align3 & pull)
        put(f"STEP|{tag}|4_abcd", a & align3 & pull & sup_low(0.02))
        put(f"STEP|{tag}|5_abcde", a & align3 & pull & sup_low(0.02) & rebound)
        put(f"STEP|{tag}|4c_abcd_close", a & align3 & pull & sup_close(0.02))
        put(f"STEP|{tag}|5c_abcde_close", a & align3 & pull & sup_close(0.02) & rebound)
        put(f"STEP|{tag}|4noreb", a & align3 & pull & sup_low(0.02))
        put(f"STEP|{tag}|4_align4", a & align4 & pull & sup_low(0.02) & rebound)

    # --- 2) MA20 지지 정의 민감도 (기저: 신고가 −5%, 정배열, 눌림)
    base = (d52 >= -0.05) & align3 & pull
    for w in (0.01, 0.02, 0.03, 0.05):
        put(f"SUP|low{wint(w)}", base & sup_low(w))
        put(f"SUP|low{wint(w)}_reb", base & sup_low(w) & rebound)
        put(f"SUP|close{wint(w)}", base & sup_close(w))
        put(f"SUP|close{wint(w)}_reb", base & sup_close(w) & rebound)
        put(f"SUP|any{wint(w)}_reb", base & sup_any(w) & rebound)
    put("SUP|below20_only", base & above20)                 # '종가가 MA20 위'만
    put("SUP|below20_only_5", (d52 >= -0.05) & align3 & above20)
    put("SUP|pull_only", base)                              # 지지 조건 없이 눌림만
    put("SUP|touch_no_reb_check", base & sup_low(0.02) & (~rebound))

    # --- 3) MA20 기울기 (기저: 최종 5단계)
    core = base & sup_low(0.02) & rebound
    for lab, m in (("up1", sl20 > 0.01), ("flat", (sl20 >= -0.01) & (sl20 <= 0.01)),
                   ("down", sl20 < -0.01), ("up3", sl20 > 0.03)):
        put(f"SL20|{lab}", core & m)
        put(f"SL20|{lab}_noNH", align3 & pull & sup_low(0.02) & rebound & m)
    put("SL20|core", core)

    # --- 4) 지지일 거래량 배수
    for lab, m in (("lt1", vr < 1.0), ("1_15", (vr >= 1.0) & (vr < 1.5)),
                   ("15_2", (vr >= 1.5) & (vr < 2.0)), ("ge2", vr >= 2.0)):
        put(f"VR|{lab}", core & m)
        put(f"VR|{lab}_noNH", align3 & pull & sup_low(0.02) & rebound & m)

    # --- 5) 거래량 추세(점진 증가 vs 마름)
    gradual = (vs10 > 0) & (x["v520"].to_numpy(float) > v10_20) & (v10_20 > 1.0)
    burst = (x["vr_max10"].to_numpy(float) >= 3.0) & (v520 < v10_20)
    for lab, m in (("up", vs10 > 0), ("down", vs10 < 0),
                   ("gradual", gradual), ("burst_fade", burst),
                   ("vr_up_15", (vs10 > 0) & (vr >= 1.5)),
                   ("vr_down_lt1", (vs10 < 0) & (vr < 1.0))):
        put(f"VS|{lab}", core & m)
        put(f"VS|{lab}_noNH", align3 & pull & sup_low(0.02) & rebound & m)
        put(f"VS|{lab}_nh3", (d52 >= -0.03) & align3 & pull & sup_low(0.02) & rebound & m)

    # --- 6) MA20 접촉 횟수(최근 10일)
    for lab, m in (("t0", touch10 == 0), ("t1", touch10 == 1), ("t2", touch10 == 2), ("t3p", touch10 >= 3)):
        put(f"TOUCH|{lab}", core & m)

    # --- 7) 이격도 추이(벌어짐 vs 좁혀짐)
    put("D20T|narrow", core & (d20_chg5 < 0))
    put("D20T|widen", core & (d20_chg5 >= 0))
    put("D20T|narrow_nh3", (d52 >= -0.03) & align3 & pull & sup_low(0.02) & rebound & (d20_chg5 < 0))

    # --- 8) 눌림 깊이
    put("DEPTH|d3_5", (d52 >= -0.05) & align3 & sup_low(0.02) & rebound & (depth >= -0.05) & (depth <= -0.03))
    put("DEPTH|d5_8", (d52 >= -0.05) & align3 & sup_low(0.02) & rebound & (depth >= -0.08) & (depth < -0.05))
    put("DEPTH|d8_12", (d52 >= -0.05) & align3 & sup_low(0.02) & rebound & (depth >= -0.12) & (depth < -0.08))
    put("DEPTH|shallow2", (d52 >= -0.05) & align3 & sup_low(0.02) & rebound & (depth > -0.02))

    # --- 9) MA60 축
    put("MA60|above", above60)
    put("MA60|below", ~above60)
    for lab, m in (("up1", sl60 > 0.01), ("flat", (sl60 >= -0.01) & (sl60 <= 0.01)), ("down", sl60 < -0.01)):
        put(f"MA60|slope_{lab}", m)
        put(f"MA60|slope_{lab}_nh10", m & (d52 >= -0.10))
    for w in (0.02, 0.03):
        put(f"MA60|touch{wint(w)}_reb", (low_rel60 >= -w) & (low_rel60 <= w) & rebound & (c > m60))
    for lab, m in (("gap_neg", gap < 0), ("gap_0_3", (gap >= 0) & (gap < 0.03)),
                   ("gap_3_8", (gap >= 0.03) & (gap < 0.08)), ("gap_8p", gap >= 0.08)):
        put(f"MA20_60|{lab}", m)
    # MA20·MA60 둘 다 위 + MA60 상승 + MA20 상승
    both_up = (sl20 > 0.01) & (sl60 > 0.01)
    put("MA60|both_up_above20", both_up & above20)
    put("MA60|both_up_above20_nh10", both_up & above20 & (d52 >= -0.10))

    # --- 10) 범주 A~F
    A = (m20 > m60) & both_up & above20
    B = (m20 > m60) & both_up & (c >= m20) & (c <= m20 * 1.03)
    C = (m20 > m60) & (sl60 > 0.01) & (~above20) & above60
    D = (m20 > m60) & (low_rel60 >= -0.02) & (low_rel60 <= 0.02) & rebound
    E = (m20 < m60) & above20
    F = (m20 < m60) & (~above60)
    for lab, m in (("A", A), ("B", B), ("C", C), ("D", D), ("E", E), ("F", F)):
        put(f"CAT|{lab}", m)
        put(f"CAT|{lab}_nh10", m & (d52 >= -0.10))
        put(f"CAT|{lab}_nh5", m & (d52 >= -0.05))
        put(f"CAT|{lab}_nh3", m & (d52 >= -0.03))

    # --- 11) 히트맵: disp20 구간 × disp60 구간
    e20 = [-9.9, -0.05, -0.02, 0.0, 0.02, 0.05, 0.10, 0.20, 9.9]
    e60 = [-9.9, -0.10, -0.05, 0.0, 0.05, 0.10, 0.20, 9.9]
    for i in range(len(e20) - 1):
        for j in range(len(e60) - 1):
            put(f"HM|{i}_{j}", (d20 >= e20[i]) & (d20 < e20[i + 1]) & (d60 >= e60[j]) & (d60 < e60[j + 1]))

    # --- 12) 체크리스트(사용자 가설 그대로, 누적)
    item = {
        "1_MA20>MA60": m20 > m60,
        "2_MA20상승": sl20 > 0.01,
        "3_MA60상승": sl60 > 0.0,
        "4_신고가10%내": d52 >= -0.10,
        "5_눌림3_12": pull,
        "6_MA20지지": sup_low(0.02) | sup_close(0.02),
        "7_반등": rebound,
        "8_거래량점증": gradual,
    }
    order = list(item)
    cum = np.ones(len(x), bool)
    for k, name in enumerate(order, 1):
        cum = cum & item[name]
        put(f"CHK|{k}", cum)
        put(f"CHK|{k}_noVol", np.logical_and.reduce([item[o] for o in order[:k] if o != "8_거래량점증"]))
    # 항목별 단독
    for name, m in item.items():
        put(f"ITEM|{name}", m)

    # --- 13) MA30 부록
    for w in (0.02,):
        put(f"MA30|touch{wint(w)}_reb", (x["l"].to_numpy(float) / m30 - 1 >= -w) & (x["l"].to_numpy(float) / m30 - 1 <= w) & rebound)
    put("MA30|above", c > m30)
    put("MA30|below", c < m30)
    for lab, m in (("up1", x["sl30"].to_numpy(float) > 0.01), ("flat", (x["sl30"].to_numpy(float) >= -0.01) & (x["sl30"].to_numpy(float) <= 0.01)),
                   ("down", x["sl30"].to_numpy(float) < -0.01)):
        put(f"MA30|slope_{lab}", m)
    e30 = [-9.9, -0.05, -0.02, 0.0, 0.02, 0.05, 0.10, 9.9]
    for i in range(len(e30) - 1):
        put(f"MA30|bin{i}", (d30 >= e30[i]) & (d30 < e30[i + 1]))

    # --- 14) 시장 필터 × 핵심
    put("MKT|core_bull", core & bull)
    put("MKT|core_bear", core & (~bull))

    return V


def wint(w: float) -> str:
    return f"{int(round(w*100))}"


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
    h = x["h"].to_numpy(float)
    lo = x["l"].to_numpy(float)
    uni_ok = ((x["mktcap"].to_numpy(float) >= MIN_MKTCAP)
              & (x["t20"].to_numpy(float) >= MIN_VALUE)
              & (x["vol0"].to_numpy(bool))
              & (np.arange(n) >= np.searchsorted(x.index.values, np.datetime64(SIGNAL_START))))
    uni_ok &= np.isfinite(x["ma60"].to_numpy(float)) & np.isfinite(x["d52"].to_numpy(float))
    dates = x.index
    years = dates.year.to_numpy()
    tax = np.array([TAX_BY_YEAR.get(int(y), 0.0020) for y in years])
    kos = IDX["close_k"].reindex(dates).ffill().to_numpy(float)
    kos_o = IDX["open_k"].reindex(dates).ffill().to_numpy(float)

    acc = Acc(11)
    V = build_variants(x)

    # 무작위 기준선(전 유니버스 일자)
    idx_ok = np.flatnonzero(uni_ok)
    for hz in HORIZONS:
        good = idx_ok[(idx_ok + hz < n)]
        e = good + 1
        ok2 = (e + hz - 1 < n) & np.isfinite(o[e]) & (o[e] > 0)
        e = e[ok2]
        if e.size == 0:
            continue
        entry = o[e]
        ex = e + hz - 1
        gross = c[ex] / entry - 1
        cost = 2 * SLIP + 2 * COMM + tax[ex]
        mfe = np.full(e.size, -9.9)
        mae = np.full(e.size, 9.9)
        for j in range(hz):
            k = e + j
            valid = np.isfinite(h[k])
            mfe = np.where(valid, np.maximum(mfe, h[k] / entry - 1), mfe)
            mae = np.where(valid, np.minimum(mae, lo[k] / entry - 1), mae)
        kr = np.where(np.isfinite(kos[ex]) & (kos_o[e] > 0), kos[ex] / kos_o[e] - 1, np.nan)
        acc.add(f"RANDOM|h{hz}", gross - cost, mfe, mae, kr, years[ex])

    # 신호 변형
    for name, m in V.items():
        mm = m & uni_ok
        idxs = np.flatnonzero(mm)
        if idxs.size == 0:
            continue
        e = idxs + 1
        ok = e < n
        e = e[ok]
        if e.size == 0:
            continue
        for hz in HORIZONS:
            sel = e[(e + hz - 1 < n)]
            if sel.size == 0:
                continue
            sel = sel[np.isfinite(o[sel]) & (o[sel] > 0)]
            if sel.size == 0:
                continue
            entry = o[sel]
            ex = sel + hz - 1
            okex = np.isfinite(c[ex])
            sel, entry, ex = sel[okex], entry[okex], ex[okex]
            if sel.size == 0:
                continue
            gross = c[ex] / entry - 1
            cost = 2 * SLIP + 2 * COMM + tax[ex]
            mfe = np.full(sel.size, -9.9)
            mae = np.full(sel.size, 9.9)
            for j in range(hz):
                k = sel + j
                hk, lk = h[k], lo[k]
                fin = np.isfinite(hk)
                mfe = np.where(fin, np.maximum(mfe, hk / entry - 1), mfe)
                mae = np.where(fin, np.minimum(mae, lk / entry - 1), mae)
            kr = np.where(np.isfinite(kos[ex]) & (kos_o[sel] > 0), kos[ex] / kos_o[sel] - 1, np.nan)
            acc.add(f"{name}|h{hz}", gross - cost, mfe, mae, kr, years[ex])
    return acc


def work_chunk(codes) -> Acc:
    acc = Acc(11)
    for code in codes:
        try:
            a = work_one(code)
        except Exception as e:  # noqa: BLE001
            print(f"[fail] {code}: {e}", flush=True)
            continue
        if a is not None:
            acc.merge(a)
    return acc


# ---------------------------------------------------------------------------
def cmd_run(args):
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
    if args.limit:
        codes = codes[: args.limit]
    print(f"[universe] {len(uni)} / run {len(codes)}", flush=True)
    t0 = time.time()
    total = Acc(11)
    done = 0
    chunk = 40
    chunks = [codes[i:i + chunk] for i in range(0, len(codes), chunk)]
    with ProcessPoolExecutor(max_workers=args.workers, initializer=init_worker, initargs=(uni, idx)) as ex:
        futs = {ex.submit(work_chunk, ch): ch for ch in chunks}
        for f in as_completed(futs):
            done += len(futs[f])
            try:
                a = f.result()
            except Exception as e:  # noqa: BLE001
                print(f"[fail] chunk: {e}", flush=True)
                continue
            if a is not None:
                total.merge(a)
            if done % 400 < chunk:
                print(f"[eval] {done}/{len(codes)} {time.time()-t0:.0f}s keys={len(total.d)}", flush=True)
    print(f"[done] {time.time()-t0:.0f}s keys={len(total.d)}", flush=True)

    base = {hz: total.stats(f"RANDOM|h{hz}") for hz in HORIZONS}
    kos_mean = {hz: (base[hz]["kos"] if base[hz]["n"] else None) for hz in HORIZONS}
    out = {"meta": {"stocks": len(codes), "signal_start": SIGNAL_START,
                    "horizons": list(HORIZONS), "slip": SLIP, "comm": COMM, "universe":
                    "시총 500억↑ & 20일 평균 거래대금 100억↑ & 거래량>0, 우선주·스팩·ETF/ETN 제외"},
           "random": base, "kospi_mean": kos_mean, "variants": {}}
    for key in total.d:
        variant, hz = key.rsplit("|h", 1)
        hz = int(hz)
        s = total.stats(key, base[hz], kos_mean[hz])
        out["variants"].setdefault(variant, {})[str(hz)] = s
    # MFE 증분: 30·40일 창에서 20일 이후 추가 상승 여부
    mfe = {}
    for grp, keys in (("random", ["RANDOM"]),):
        pass
    for variant, hs in out["variants"].items():
        if all(str(hh) in hs and hs[str(hh)].get("n") for hh in (20, 30, 40)):
            a20 = hs["20"]["mfe"]
            a30 = hs["30"]["mfe"]
            a40 = hs["40"]["mfe"]
            mfe[variant] = {"mfe20": a20, "mfe30": a30, "mfe40": a40,
                            "add_20_30": a30 - a20, "add_20_40": a40 - a20}
    out["mfe_increment"] = mfe
    out["random_mfe"] = {hz: {"mfe": base[hz].get("mfe"), "mae": base[hz].get("mae"),
                              "mean": base[hz].get("mean"), "median": base[hz].get("median")}
                         for hz in HORIZONS}
    WORK.mkdir(parents=True, exist_ok=True)
    with open(WORK / "ma_support_raw.json", "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("[write]", WORK / "ma_support_raw.json", flush=True)
    for hz in HORIZONS:
        b = base[hz]
        if b.get("n"):
            print(f"  RANDOM h{hz}: n={b['n']} mean={b['mean']*100:.2f}% median={b['median']*100:.2f}% "
                  f"win={b['win']*100:.1f}% kos={kos_mean[hz]*100:.2f}%", flush=True)




# ===========================================================================
# 추가 모드: (1) 급등 확인일 진입의 확률(적중률), (2) 후보 A 익절 스윕
# 사용법: python3 scripts/ma_support_rebound.py --mode prob|exit
# ===========================================================================
def _prep_basic(code, uni, idx):
    row = uni.loc[code]
    d = pd.read_pickle(OHLCV / f"{code}.pkl")
    if d.empty or len(d) < 300:
        return None
    x = prep(d, float(row["shares"]), idx)
    return x


def prob_worker(args):
    codes, uni, idx, mode = args
    res = {}
    for code in codes:
        try:
            x = _prep_basic(code, uni, idx)
        except Exception:  # noqa: BLE001
            continue
        if x is None:
            continue
        n = len(x)
        c = x["c"].to_numpy(float)
        o = x["o"].to_numpy(float)
        h = x["h"].to_numpy(float)
        l = x["l"].to_numpy(float)
        uni_ok = ((x["mktcap"].to_numpy(float) >= MIN_MKTCAP) & (x["t20"].to_numpy(float) >= MIN_VALUE)
                  & (x["vol0"].to_numpy(bool)) & (np.arange(n) >= np.searchsorted(x.index.values, np.datetime64(SIGNAL_START))))
        years = x.index.year.to_numpy()
        tax = np.array([TAX_BY_YEAR.get(int(y), 0.0020) for y in years])
        d52 = x["d52"].to_numpy(float)
        dh60 = x["d52"].to_numpy(float) * 0 + (c / x["h"].rolling(60).max().to_numpy(float) - 1)
        v520 = x["v520"].to_numpy(float)
        xh250 = (d52 >= 0).astype(bool)
        xh60 = (dh60 >= 0).astype(bool)
        masks = {
            "확인|신고가돌파+거래량1.5배": xh250 & (v520 >= 1.5),
            "확인|신고가돌파+거래량2배": xh250 & (v520 >= 2.0),
            "확인|신고가돌파+거래량3배": xh250 & (v520 >= 3.0),
            "확인|60일고점돌파+거래량2배": xh60 & (v520 >= 2.0),
            "확인|신고가돌파만": xh250,
            "확인|거래량2배만": v520 >= 2.0,
        }
        for k, m in masks.items():
            mm = m & uni_ok
            idxs = np.flatnonzero(mm)
            if idxs.size:
                res.setdefault(k, []).append((code, idxs, c, o, h, l, tax, years))
        res.setdefault("BASE", []).append((code, np.flatnonzero(uni_ok), c, o, h, l, tax, years))
    # 집계
    agg = {}
    for k, lst in res.items():
        for code, idxs, c, o, h, l, tax, years in lst:
            n = len(c)
            for entry_mode in ("close", "nextopen"):
                for hz in (10, 15, 20, 30, 40):
                    e = idxs if entry_mode == "close" else idxs + 1
                    ex = e + hz
                    ok = (ex < n)
                    e2, ex2 = e[ok], ex[ok]
                    if e2.size == 0:
                        continue
                    entry = c[e2] if entry_mode == "close" else o[e2]
                    okp = (ex2 >= 0) & (ex2 < n) & np.isfinite(entry) & (entry > 0)
                    okp &= np.isfinite(c[np.clip(ex2, 0, n - 1)])
                    e2, ex2, entry = e2[okp], ex2[okp], entry[okp]
                    if e2.size == 0:
                        continue
                    gross = c[ex2] / entry - 1
                    net = gross - (2 * SLIP + 2 * COMM + tax[ex2])
                    mfe = np.zeros(e2.size)
                    mae = np.zeros(e2.size)
                    for j in range(hz + 1):
                        kk = np.minimum(e2 + j, n - 1)
                        mfe = np.maximum(mfe, h[kk] / entry - 1)
                        mae = np.minimum(mae, l[kk] / entry - 1)
                    key = f"{k}|{entry_mode}|h{hz}"
                    a = agg.setdefault(key, {"n": 0, "hits": 0, "mfe20": 0, "mfe10": 0, "s": 0.0,
                                             "vals": [], "yn": {}, "ys": {}})
                    a["n"] += int(e2.size)
                    a["hits"] += int((gross >= 0.20).sum())
                    a["mfe20"] += int((mfe >= 0.20).sum())
                    a["mfe10"] += int((mfe >= 0.10).sum())
                    a["s"] += float(net.sum())
                    a["vals"].append(net.astype(np.float32))
                    for y in np.unique(years[ex2]):
                        m = years[ex2] == y
                        a["yn"][int(y)] = a["yn"].get(int(y), 0) + int(m.sum())
                        a["ys"][int(y)] = a["ys"].get(int(y), 0.0) + float(net[m].sum())
    return agg


def cmd_prob(args):
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
    chunks = [(codes[i:i + 40], uni, idx, "prob") for i in range(0, len(codes), 40)]
    total = {}
    t0 = time.time()
    done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(prob_worker, ch) for ch in chunks]
        for f in as_completed(futs):
            done += 1
            r = f.result()
            for k, a in r.items():
                t = total.setdefault(k, {"n": 0, "hits": 0, "mfe20": 0, "mfe10": 0, "s": 0.0, "vals": [],
                                         "yn": {}, "ys": {}})
                t["n"] += a["n"]; t["hits"] += a["hits"]; t["mfe20"] += a["mfe20"]; t["mfe10"] += a["mfe10"]
                t["s"] += a["s"]
                t["vals"].extend(a["vals"])
                for y, yy in a["yn"].items():
                    t["yn"][y] = t["yn"].get(y, 0) + yy
                    t["ys"][y] = t["ys"].get(y, 0.0) + a["ys"][y]
            if done % 10 == 0:
                print(f"[prob] {done}/{len(chunks)} {time.time()-t0:.0f}s", flush=True)
    out = {}
    for k, t in total.items():
        if t["n"] == 0:
            continue
        out[k] = {"n": t["n"], "mean": t["s"] / t["n"], "hit20": t["hits"] / t["n"],
                  "mfe20": t["mfe20"] / t["n"], "mfe10": t["mfe10"] / t["n"],
                  "years": {y: {"n": t["yn"][y], "mean": t["ys"][y] / t["yn"][y]} for y in t["yn"]}}
    with open(WORK / "ma_prob.json", "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("[write]", WORK / "ma_prob.json", flush=True)
    for k, v in sorted(out.items()):
        print(f"  {k:36s} n={v['n']:7d} P(종가+20%)={v['hit20']*100:5.2f}% P(MFE+20%)={v['mfe20']*100:5.2f}% "
              f"P(MFE+10%)={v['mfe10']*100:5.2f}% mean={v['mean']*100:6.2f}%", flush=True)


def cmd_exit(args):
    """후보 A: 52주 신고가 −5% 이내 & 5일 거래대금 2배 → 5거래일 뒤 시가 → 익절 스윕."""
    import pickle as _pk
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
    rows = []
    for code in uni.index:
        x = _prep_basic(code, uni, idx)
        if x is None:
            continue
        n = len(x)
        c = x["c"].to_numpy(float); o = x["o"].to_numpy(float); h = x["h"].to_numpy(float); l = x["l"].to_numpy(float)
        tf = pd.read_pickle(OHLCV / f"{code}.pkl")["turnover"].astype(float).reindex(x.index)
        t5 = tf.rolling(5).mean().to_numpy(); t20 = tf.rolling(20).mean().to_numpy()
        uni_ok = ((x["mktcap"].to_numpy(float) >= MIN_MKTCAP) & (x["t20"].to_numpy(float) >= MIN_VALUE)
                  & (x["vol0"].to_numpy(bool)) & (np.arange(n) >= np.searchsorted(x.index.values, np.datetime64(SIGNAL_START))))
        d52 = x["d52"].to_numpy(float)
        sig = uni_ok & np.isfinite(d52) & (d52 >= -0.05) & (t5 >= 2.0 * t20) & (t20 > 0)
        tax = np.array([TAX_BY_YEAR.get(int(y), 0.0020) for y in x.index.year])
        for t in np.flatnonzero(sig):
            e = t + 5
            if e >= n - 2:
                continue
            entry = o[e]
            if not (np.isfinite(entry) and entry > 0):
                continue
            for hz in (20, 30, 40):
                if e + hz - 1 >= n:
                    continue
                exd = e + hz - 1
                cost = 2 * SLIP + 2 * COMM + tax[exd]
                base_ret = c[exd] / entry - 1 - cost
                rec = {"code": code, "signal_date": x.index[t].strftime("%Y-%m-%d"), "entry": entry,
                       "year": int(x.index[exd].year), "hold": hz, "notarget": base_ret,
                       "mfe": float(np.nanmax(h[e:exd + 1]) / entry - 1), "mae": float(np.nanmin(l[e:exd + 1]) / entry - 1)}
                for tg in (0.05, 0.10, 0.15, 0.20):
                    px = entry * (1 + tg)
                    hit = np.flatnonzero(h[e:exd + 1] >= px)
                    if hit.size:
                        j = int(hit[0])
                        fill = max(px, o[e + j]) if j < hz else px
                        rec[f"tg{int(tg*100)}"] = fill / entry - 1 - cost
                        rec[f"tg{int(tg*100)}_day"] = j + 1
                    else:
                        rec[f"tg{int(tg*100)}"] = base_ret
                        rec[f"tg{int(tg*100)}_day"] = np.nan
                rows.append(rec)
    df = pd.DataFrame(rows)
    df.to_pickle(WORK / "exit_sweep.pkl")
    print(f"[exit] trades={len(df)}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--mode", choices=["main", "prob", "exit"], default="main")
    a = ap.parse_args()
    if a.mode == "prob":
        cmd_prob(a)
    elif a.mode == "exit":
        cmd_exit(a)
    else:
        cmd_run(a)
