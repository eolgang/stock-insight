#!/usr/bin/env python3
"""21일선/61일선 돌파 후 보유기간 확장 검증: 20일 → 30일·40일.

기존 mfe_analysis.py(20일 창)와 **완전히 같은 데이터·유니버스·신호 정의·비용**을 쓰되,
보유 창을 20·30·40거래일로 늘려 같은 표본에서 다시 계산한다.

추가 산출
  - 30/40일 보유 평균·중앙값·승률·손익비
  - 30/40일 창의 최고점(MFE) 도달 시점 분포·중앙값, MFE 크기
  - 20일 대비 같은 거래의 40일 보유 손익 변화(개별 거래 단위)
  - +3% 익절/−5% 손절 규칙을 20/30/40일 프레임에 각각 적용(프레임 끝 청산)
  - 무작위 진입 대조군의 30/40일 버전
  - 연도별(하락연도 포함) 분해

사용법: python3 scripts/mfe_analysis_30_40d.py --workers 2
출력  : stock-insight/data/ma-breakout-mfe-30-40d.json
        work/backtest/mfe_30_40d_signals.npz, work/backtest/mfe_30_40d_numbers.txt
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("BACKTEST_CACHE", REPO.parent / "work" / "backtest" / "cache"))
OHLCV = CACHE / "ohlcv"
OUT_JSON = REPO / "data" / "ma-breakout-mfe-30-40d.json"
WORK = REPO.parent / "work" / "backtest"

MIN_MKTCAP = 50_000_000_000
MIN_VALUE = 10_000_000_000
MAX_DISP20 = 0.15
WINDOWS = (5, 10, 20, 30, 40)
FRAMES = (20, 30, 40)
MAXF = 40
TAX_BY_YEAR = {2015: 0.0030, 2016: 0.0030, 2017: 0.0030, 2018: 0.0030, 2019: 0.0030,
               2020: 0.0025, 2021: 0.0023, 2022: 0.0023, 2023: 0.0020, 2024: 0.0018,
               2025: 0.0015, 2026: 0.0020}
SLIP = 0.001
COMM = 0.00015
RT = 2 * SLIP + 2 * COMM
COST_APX = RT + 0.0019            # 평균 왕복비용 근사 0.46% (기존 보고서와 동일)

MAIN_VARIANTS = ("A21", "B21", "A61", "B61")

PREFERRED_RE = re.compile(r"(?:\d?우B?C?)$|우\(|우B$")
NON_COMMON_KEYWORDS = ("스팩", "리츠", "인프라", "ETN", "ETF", "선물", "레버리지", "인버스", "합성")


def is_common_stock(name: str, end_type: str) -> bool:
    if end_type and end_type != "stock":
        return False
    n = name or ""
    if any(k in n for k in NON_COMMON_KEYWORDS):
        return False
    if PREFERRED_RE.search(n):
        return False
    return True


# ---------------------------------------------------------------- 신호 계산
def _apply(df: pd.DataFrame, mask: np.ndarray, entry_mode: str, code: str = "") -> dict:
    ac = df["aclose"].to_numpy(dtype=float)
    ao = df["aopen"].to_numpy(dtype=float)
    ah = df["ahigh"].to_numpy(dtype=float)
    dates = df.index
    tax_arr = np.array([TAX_BY_YEAR.get(int(d.year), 0.0020) for d in dates])
    n = len(ac)

    idxs = np.flatnonzero(mask)
    n_sig = int(idxs.size)
    e0s = idxs + 1
    okfwd = e0s + MAXF <= n
    if entry_mode == "next_open":
        entry_all = ao[np.clip(e0s, 0, n - 1)]
    else:
        entry_all = ac[idxs]
    good = okfwd & (entry_all > 0)
    idxs, e0s, entry = idxs[good], e0s[good], entry_all[good]
    if idxs.size == 0:
        return {"n_sig": n_sig, "n_used": 0, "n_cut": n_sig}

    m = idxs.size
    mfc = np.full((m, len(WINDOWS)), np.nan)
    mfh = np.full((m, len(WINDOWS)), np.nan)
    fin = np.full((m, len(WINDOWS)), np.nan)
    cst = np.full((m, len(WINDOWS)), np.nan)
    am = {f: np.zeros(m, dtype=int) for f in FRAMES}
    gap = {f: np.full(m, np.nan) for f in FRAMES}
    mx = {f: np.full(m, np.nan) for f in FRAMES}
    rc_all = np.full((m, MAXF), np.nan)
    for k in range(m):
        s = e0s[k]
        rc = ac[s:s + MAXF] / entry[k] - 1.0
        rh = ah[s:s + MAXF] / entry[k] - 1.0
        rc_all[k] = rc
        for wi, w in enumerate(WINDOWS):
            mfc[k, wi] = rc[:w].max()
            mfh[k, wi] = rh[:w].max()
            fin[k, wi] = rc[w - 1]
            cst[k, wi] = RT + tax_arr[s + w - 1]
        for f in FRAMES:
            seg = rc[:f]
            j = int(np.argmax(seg))
            am[f][k] = j + 1
            gap[f][k] = seg.max() - seg[j]
            mx[f][k] = seg.max()
    return dict(n_sig=n_sig, n_used=int(m), n_cut=n_sig - int(m),
                sd=np.array([int(dates[i].strftime("%Y%m%d")) for i in idxs], dtype=np.int64),
                mfc=mfc, mfh=mfh, fin=fin, cost=cst, rc=rc_all,
                am20=am[20], am30=am[30], am40=am[40],
                gap20=gap[20], gap30=gap[30], gap40=gap[40],
                mx20=mx[20], mx30=mx[30], mx40=mx[40],
                entry=entry, code=np.array([code] * m),
                year=np.array([dates[i].year for i in idxs]))


def variants_for(df: pd.DataFrame, shares: float, code: str = "") -> dict:
    n = len(df)
    if n < 320:
        return {}
    ac = df["aclose"].to_numpy(dtype=float)
    ao = df["aopen"].to_numpy(dtype=float)
    vol = df["volume"].to_numpy(dtype=float)
    tv = df["turnover"].to_numpy(dtype=float)

    s_ac = pd.Series(ac)
    ma20 = s_ac.rolling(20).mean().to_numpy()
    ma21 = s_ac.rolling(21).mean().to_numpy()
    ma61 = s_ac.rolling(61).mean().to_numpy()
    tv_ma20 = pd.Series(tv).rolling(20).mean().to_numpy()
    mktcap = ac * shares
    disp20 = ac / ma20 - 1.0

    base_ok = (mktcap >= MIN_MKTCAP) & (vol > 0) & np.isfinite(ac) & (ac > 0) & (ao > 0)
    turn_ok = tv_ma20 >= MIN_VALUE
    disp_ok = disp20 <= MAX_DISP20

    res = {}
    for tag, ma in (("21", ma21), ("61", ma61)):
        above = ac > ma
        prev_above = np.r_[False, above[:-1]]
        cross = above & ~prev_above & np.isfinite(ma)
        body = cross & (ao <= ma)
        cross[:62] = False
        body[:62] = False
        for key, m in ((f"A{tag}", cross & base_ok & turn_ok & disp_ok),
                       (f"B{tag}", body & base_ok & turn_ok & disp_ok)):
            d = _apply(df, m, "next_open", code)
            if d.get("n_used", 0):
                res[key] = d
            else:
                res[key] = d
        d = _apply(df, cross & base_ok & turn_ok & disp_ok, "close", code)
        if d.get("n_used", 0):
            res[f"A{tag}_close"] = d
    return res


# ---------------------------------------------------------------- 경로 지표
def _days_to(rc: np.ndarray, thr: float) -> np.ndarray:
    hit = rc >= thr
    any_hit = hit.any(axis=1)
    d = np.zeros(rc.shape[0], dtype=int)
    d[any_hit] = hit.argmax(axis=1)[any_hit] + 1
    return d


def _rule(rc: np.ndarray, tgt: float, stop: float):
    """프레임 안에서 목표 먼저 → 익절, 손절 먼저 → 손절, 둘 다 아니면 마지막 날 종가 청산."""
    hit = rc >= tgt
    ls = rc <= -stop
    a_hit, a_ls = hit.any(axis=1), ls.any(axis=1)
    i_hit = np.where(a_hit, hit.argmax(axis=1), 10**6)
    i_ls = np.where(a_ls, ls.argmax(axis=1), 10**6)
    ret = rc[:, -1].copy()
    tgt_win = a_hit & (i_hit <= i_ls)
    stop_win = a_ls & (i_ls < i_hit)
    ret[tgt_win] = rc[tgt_win, i_hit[tgt_win]]
    ret[stop_win] = rc[stop_win, i_ls[stop_win]]
    return ret - COST_APX, tgt_win, stop_win


def _payoff(net: np.ndarray) -> dict:
    g = net[net > 0]
    l = net[net < 0]
    return {"avg_gain": float(g.mean()) if g.size else 0.0,
            "avg_loss": float(l.mean()) if l.size else 0.0,
            "payoff": float(g.mean() / abs(l.mean())) if g.size and l.size and l.mean() != 0 else None}


def _am_stats(am: np.ndarray) -> dict:
    out = {"1": float(np.mean(am == 1)), "2": float(np.mean(am == 2)), "3": float(np.mean(am == 3)),
           "4-5": float(np.mean((am >= 4) & (am <= 5))), "6-10": float(np.mean((am >= 6) & (am <= 10))),
           "11-20": float(np.mean((am >= 11) & (am <= 20))),
           "21-30": float(np.mean((am >= 21) & (am <= 30))),
           "31-40": float(np.mean(am >= 31)),
           "le3": float(np.mean(am <= 3)), "le5": float(np.mean(am <= 5)),
           "le10": float(np.mean(am <= 10)),
           "median": float(np.median(am)), "mean": float(np.mean(am))}
    return out


def summarize(d: dict, bull: np.ndarray) -> dict:
    n = int(d["mfc"].shape[0])
    mfc, mfh, fin, cost = d["mfc"], d["mfh"], d["fin"], d["cost"]
    net_c = mfc - cost
    net_h = mfh - cost
    net_fin = fin - cost
    out = {"n": n, "windows": {}}
    for wi, w in enumerate(WINDOWS):
        p = _payoff(net_fin[:, wi])
        out["windows"][str(w)] = {
            "mfe_c_net": float(np.mean(net_c[:, wi])),
            "mfe_c_net_med": float(np.median(net_c[:, wi])),
            "mfe_h_net": float(np.mean(net_h[:, wi])),
            "mfe_h_net_med": float(np.median(net_h[:, wi])),
            "hold_net": float(np.mean(net_fin[:, wi])),
            "hold_net_med": float(np.median(net_fin[:, wi])),
            "hold_win": float(np.mean(net_fin[:, wi] > 0)),
            "avg_gain": p["avg_gain"], "avg_loss": p["avg_loss"], "payoff": p["payoff"],
            "p1": float(np.mean(net_c[:, wi] > 0.01)),
            "p3": float(np.mean(net_c[:, wi] > 0.03)),
            "p5": float(np.mean(net_c[:, wi] > 0.05)),
            "p10": float(np.mean(net_c[:, wi] > 0.10)),
            "p3h": float(np.mean(net_h[:, wi] > 0.03)),
            "p5h": float(np.mean(net_h[:, wi] > 0.05)),
            "p10h": float(np.mean(net_h[:, wi] > 0.10)),
            "hold_p10": float(np.percentile(net_fin[:, wi], 10)),
            "hold_p25": float(np.percentile(net_fin[:, wi], 25)),
            "hold_p75": float(np.percentile(net_fin[:, wi], 75)),
            "hold_p90": float(np.percentile(net_fin[:, wi], 90)),
            "hold_std": float(np.std(net_fin[:, wi])),
        }
    # 최고점 위치·크기 (프레임별)
    out["argmax"] = {str(f): _am_stats(d[f"am{f}"]) for f in FRAMES}
    out["never_up"] = {}
    for f in FRAMES:
        never = d[f"mx{f}"] <= 0
        out["never_up"][str(f)] = {
            "share": float(np.mean(never)),
            "hold_net": float(np.mean(net_fin[never, FRAMES.index(f)])) if never.any() else None}
    # 20일 대비 30/40일 보유 변화 (같은 거래)
    out["extend"] = {}
    for f in (30, 40):
        wi_f, wi_20 = WINDOWS.index(f), WINDOWS.index(20)
        delta = net_fin[:, wi_f] - net_fin[:, wi_20]
        out["extend"][f"{f}-20"] = {
            "mean": float(np.mean(delta)), "median": float(np.median(delta)),
            "share_up": float(np.mean(delta > 0)),
            "share_down": float(np.mean(delta < 0)),
            "q25": float(np.percentile(delta, 25)), "q75": float(np.percentile(delta, 75)),
        }
    # 20일 안에 +3%를 본 거래만: 40일까지 들면 더 좋아지나
    p3_20 = net_c[:, WINDOWS.index(20)] > 0.03
    if p3_20.any():
        w20, w40 = WINDOWS.index(20), WINDOWS.index(40)
        sub = net_fin[p3_20]
        out["extend_p3"] = {
            "n": int(p3_20.sum()),
            "hold20": float(np.mean(sub[:, w20])), "hold40": float(np.mean(sub[:, w40])),
            "mean_delta": float(np.mean(sub[:, w40] - sub[:, w20])),
            "share_up": float(np.mean(sub[:, w40] > sub[:, w20])),
        }
    else:
        out["extend_p3"] = None
    # 경로 지표: 프레임별 목표 도달 소요일·규칙
    rc = d["rc"]
    out["path"] = {}
    for f in FRAMES:
        seg = rc[:, :f]
        pb = {"n": int(seg.shape[0])}
        for t in (0.01, 0.02, 0.03, 0.05):
            dd = _days_to(seg, t + COST_APX)
            hit = dd > 0
            pb[f"d{int(t*100)}"] = {
                "hit": float(hit.mean()),
                "med_days": float(np.median(dd[hit])) if hit.any() else None,
                "q25": float(np.percentile(dd[hit], 25)) if hit.any() else None,
                "q75": float(np.percentile(dd[hit], 75)) if hit.any() else None,
                "le3": float((dd[hit] <= 3).mean()) if hit.any() else None,
                "le5": float((dd[hit] <= 5).mean()) if hit.any() else None,
                "le10": float((dd[hit] <= 10).mean()) if hit.any() else None,
                "mean_days": float(dd[hit].mean()) if hit.any() else None}
        for tgt, stop in ((0.03, 0.05), (0.05, 0.05), (0.03, 0.03), (0.05, 0.03)):
            r, tw, sw_ = _rule(seg, tgt + COST_APX, stop)
            pb[f"rule{int(tgt*100)}_{int(stop*100)}"] = {
                "ret": float(r.mean()), "med": float(np.median(r)),
                "win": float((r > 0).mean()), "tgt": float(tw.mean()), "stop": float(sw_.mean()),
                "payoff": _payoff(r)["payoff"],
                "avg_gain": _payoff(r)["avg_gain"], "avg_loss": _payoff(r)["avg_loss"]}
        out["path"][str(f)] = pb
    out["gap"] = {str(f): float(np.mean(d[f"gap{f}"])) for f in FRAMES}
    out["cost"] = {str(w): float(np.mean(cost[:, i])) for i, w in enumerate(WINDOWS)}
    # 연도별
    out["by_year"] = {}
    for y in sorted(set(int(x) for x in d["year"])):
        sel = d["year"] == y
        rec = {"n": int(sel.sum())}
        for w in (20, 30, 40):
            wi = WINDOWS.index(w)
            rec[f"hold{w}"] = float(np.mean(net_fin[sel, wi]))
            rec[f"win{w}"] = float(np.mean(net_fin[sel, wi] > 0))
            rec[f"mfe{w}"] = float(np.mean(net_c[sel, wi]))
        rec["p3_20"] = float(np.mean(net_c[sel, WINDOWS.index(20)] > 0.03))
        rec["p3_40"] = float(np.mean(net_c[sel, WINDOWS.index(40)] > 0.03))
        out["by_year"][str(y)] = rec
    out["by_market"] = {}
    for label, sel in (("bull", bull), ("bear", ~bull)):
        if sel.sum() == 0:
            continue
        out["by_market"][label] = {
            "n": int(sel.sum()),
            "hold20": float(np.mean(net_fin[sel, WINDOWS.index(20)])),
            "hold30": float(np.mean(net_fin[sel, WINDOWS.index(30)])),
            "hold40": float(np.mean(net_fin[sel, WINDOWS.index(40)])),
            "mfe40": float(np.mean(net_c[sel, WINDOWS.index(40)])),
            "p3_40": float(np.mean(net_c[sel, WINDOWS.index(40)] > 0.03))}
    return out


# ---------------------------------------------------------------- 무작위 대조군
def baseline_for(df: pd.DataFrame, shares: float) -> dict:
    n = len(df)
    if n < 320:
        return {}
    ac = df["aclose"].to_numpy(dtype=float)
    ao = df["aopen"].to_numpy(dtype=float)
    ah = df["ahigh"].to_numpy(dtype=float)
    vol = df["volume"].to_numpy(dtype=float)
    tv = df["turnover"].to_numpy(dtype=float)
    dates = df.index
    mktcap = ac * shares
    tv_ma20 = pd.Series(tv).rolling(20).mean().to_numpy()
    ok = (mktcap >= MIN_MKTCAP) & (vol > 0) & np.isfinite(ac) & (ac > 0) & (ao > 0) & (tv_ma20 >= MIN_VALUE)
    tax_arr = np.array([TAX_BY_YEAR.get(int(d.year), 0.0020) for d in dates])

    i = np.arange(n - MAXF)                 # 진입일 = i+1
    use = ok[i] & ok[i + 1]
    i = i[use]
    if i.size == 0:
        return {}
    entry = ao[i + 1]
    good = entry > 0
    i, entry = i[good], entry[good]
    swc = np.lib.stride_tricks.sliding_window_view(ac, MAXF)[i + 1]
    swh = np.lib.stride_tricks.sliding_window_view(ah, MAXF)[i + 1]
    rc = swc / entry[:, None] - 1
    rh = swh / entry[:, None] - 1

    out = {"n_rc": []}
    out["base"] = {}
    for w in WINDOWS:
        mfc = rc[:, :w].max(axis=1)
        mfh = rh[:, :w].max(axis=1)
        fin = rc[:, w - 1]
        cost = RT + tax_arr[i + w]
        netf = fin - cost
        netc = mfc - cost
        g, l = netf[netf > 0], netf[netf < 0]
        out["base"][str(w)] = {
            "n": int(i.size),
            "mfe_net_sum": float(netc.sum()), "mfe_net_med_sum": None,
            "hold_sum": float(netf.sum()), "win": float((netf > 0).sum()),
            "gain_sum": float(g.sum()), "n_gain": int(g.size),
            "loss_sum": float(l.sum()), "n_loss": int(l.size),
            "p1": float((netc > 0.01).sum()), "p3": float((netc > 0.03).sum()),
            "p5": float((netc > 0.05).sum()), "p10": float((netc > 0.10).sum()),
            "p3h": float((mfh - cost > 0.03).sum()), "p5h": float((mfh - cost > 0.05).sum()),
            "p10h": float((mfh - cost > 0.10).sum()),
            "netf_sorted": None,
        }
    # 경로 지표(프레임별) — 합계만 넘긴다
    out["path"] = {}
    for f in FRAMES:
        seg = rc[:, :f]
        pb = {"n": int(seg.shape[0])}
        for t in (0.01, 0.02, 0.03, 0.05):
            dd = _days_to(seg, t + COST_APX)
            hit = dd > 0
            pb[f"d{int(t*100)}"] = {"hit": int(hit.sum()), "days_sum": int(dd[hit].sum()),
                                    "le3": int((dd[hit] <= 3).sum()), "le5": int((dd[hit] <= 5).sum()),
                                    "le10": int((dd[hit] <= 10).sum())}
        for tgt, stop in ((0.03, 0.05), (0.05, 0.05), (0.03, 0.03), (0.05, 0.03)):
            r, tw, sw_ = _rule(seg, tgt + COST_APX, stop)
            g, l = r[r > 0], r[r < 0]
            pb[f"rule{int(tgt*100)}_{int(stop*100)}"] = {
                "sum": float(r.sum()), "win": float((r > 0).sum()),
                "tgt": float(tw.sum()), "stop": float(sw_.sum()),
                "gain_sum": float(g.sum()), "n_gain": int(g.size),
                "loss_sum": float(l.sum()), "n_loss": int(l.size)}
        out["path"][str(f)] = pb
    # 최고점 위치 분포 (프레임별)
    out["argmax"] = {}
    for f in FRAMES:
        amv = rc[:, :f].argmax(axis=1) + 1
        hist = [int((amv == k).sum()) for k in range(1, f + 1)]
        out["argmax"][str(f)] = {"n": int(amv.size), "hist": hist}
    # 20일 대비 40일 변화(같은 진입)
    w20, w40 = WINDOWS.index(20), WINDOWS.index(40)
    n20 = rc[:, w20 - 1] - (RT + tax_arr[i + 20])
    n40 = rc[:, w40 - 1] - (RT + tax_arr[i + 40])
    delta = n40 - n20
    out["extend"] = {"n": int(delta.size), "sum": float(delta.sum()),
                     "up": int((delta > 0).sum()), "down": int((delta < 0).sum())}
    out["hold_sorted"] = {}
    for w in (20, 30, 40):
        v = rc[:, w - 1] - (RT + tax_arr[i + w])
        out["hold_sorted"][str(w)] = {"sorted": np.sort(v)[::max(1, v.size // 4000)].tolist()}
    return out


def _worker(args):
    code, path, shares = args
    try:
        df = pd.read_pickle(path)
        return code, variants_for(df, shares, code), baseline_for(df, shares)
    except Exception as e:  # noqa: BLE001
        print(f"[skip] {code}: {e}", file=sys.stderr)
        return code, {}, {}


# ---------------------------------------------------------------- 대조군 집계
def aggregate_baseline(basel: list) -> dict:
    out = {}
    rows = [b for b in basel if b.get("base")]
    if not rows:
        return out
    for w in [str(x) for x in WINDOWS]:
        rs = [r["base"][w] for r in rows if r["base"].get(w)]
        n = sum(r["n"] for r in rs)
        agg = {k: sum(r[k] for r in rs) for k in
               ("mfe_net_sum", "hold_sum", "win", "gain_sum", "n_gain", "loss_sum", "n_loss",
                "p1", "p3", "p5", "p10", "p3h", "p5h", "p10h")}
        out[w] = {"n": n,
                  "mfe_c_net": agg["mfe_net_sum"] / n, "hold_net": agg["hold_sum"] / n,
                  "hold_win": agg["win"] / n,
                  "avg_gain": agg["gain_sum"] / agg["n_gain"] if agg["n_gain"] else 0.0,
                  "avg_loss": agg["loss_sum"] / agg["n_loss"] if agg["n_loss"] else 0.0,
                  "payoff": (agg["gain_sum"] / agg["n_gain"]) / abs(agg["loss_sum"] / agg["n_loss"])
                  if agg["n_gain"] and agg["n_loss"] else None,
                  "p1": agg["p1"] / n, "p3": agg["p3"] / n, "p5": agg["p5"] / n, "p10": agg["p10"] / n,
                  "p3h": agg["p3h"] / n, "p5h": agg["p5h"] / n, "p10h": agg["p10h"] / n}
    pb = {}
    for f in FRAMES:
        ps = [r["path"][str(f)] for r in rows if r.get("path", {}).get(str(f))]
        if not ps:
            continue
        n = sum(p["n"] for p in ps)
        d = {"n": int(n)}
        for t in (1, 2, 3, 5):
            hit = sum(p[f"d{t}"]["hit"] for p in ps)
            d[f"d{t}"] = {"hit": hit / n,
                          "med_days": None,
                          "le3": sum(p[f"d{t}"]["le3"] for p in ps) / hit if hit else None,
                          "le5": sum(p[f"d{t}"]["le5"] for p in ps) / hit if hit else None,
                          "le10": sum(p[f"d{t}"]["le10"] for p in ps) / hit if hit else None,
                          "mean_days": sum(p[f"d{t}"]["days_sum"] for p in ps) / hit if hit else None}
        for tgt, stop in ((3, 5), (5, 5), (3, 3), (5, 3)):
            k = f"rule{tgt}_{stop}"
            sm = sum(p[k]["sum"] for p in ps)
            gn = sum(p[k]["n_gain"] for p in ps)
            ls = sum(p[k]["n_loss"] for p in ps)
            gs = sum(p[k]["gain_sum"] for p in ps)
            lo = sum(p[k]["loss_sum"] for p in ps)
            d[k] = {"ret": sm / n, "win": sum(p[k]["win"] for p in ps) / n,
                    "tgt": sum(p[k]["tgt"] for p in ps) / n,
                    "stop": sum(p[k]["stop"] for p in ps) / n,
                    "avg_gain": gs / gn if gn else 0.0,
                    "avg_loss": lo / ls if ls else 0.0,
                    "payoff": (gs / gn) / abs(lo / ls) if gn and ls else None}
        pb[str(f)] = d
    out["path"] = pb
    am = {}
    for f in FRAMES:
        als = [r["argmax"][str(f)] for r in rows if r.get("argmax", {}).get(str(f))]
        if not als:
            continue
        n = sum(a["n"] for a in als)
        hist = np.sum([a["hist"] for a in als], axis=0)
        med = int(np.searchsorted(np.cumsum(hist), n / 2) + 1)
        am[str(f)] = {"n": int(n),
                      "1": hist[0] / n, "2": hist[1] / n, "3": hist[2] / n,
                      "4-5": hist[3:5].sum() / n, "6-10": hist[5:10].sum() / n,
                      "11-20": hist[10:20].sum() / n,
                      "21-30": hist[20:30].sum() / n if f >= 30 else None,
                      "31-40": hist[30:40].sum() / n if f >= 40 else None,
                      "le3": hist[:3].sum() / n, "le5": hist[:5].sum() / n,
                      "le10": hist[:10].sum() / n, "median": med}
    out["argmax"] = am
    ex = [r["extend"] for r in rows if r.get("extend")]
    if ex:
        nn = sum(e["n"] for e in ex)
        out["extend"] = {"n": int(nn), "mean": sum(e["sum"] for e in ex) / nn,
                         "share_up": sum(e["up"] for e in ex) / nn,
                         "share_down": sum(e["down"] for e in ex) / nn}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    uni = pd.read_pickle(CACHE / "universe.pkl")
    uni = uni[uni.apply(lambda r: is_common_stock(r["name"], r["end_type"]), axis=1)].copy()
    uni["last_close"] = pd.to_numeric(uni["last_close"], errors="coerce")
    uni["mktcap_eok"] = pd.to_numeric(uni["mktcap_eok"], errors="coerce")
    uni = uni[uni["last_close"] > 0]
    uni["shares"] = uni["mktcap_eok"] * 1e8 / uni["last_close"]
    uni = uni.dropna(subset=["shares"])
    names = dict(zip(uni["code"], uni["name"]))

    idxdf = pd.read_pickle(CACHE / "index.pkl")
    idx_close = idxdf["close_k"]

    jobs = [(r.code, str(OHLCV / f"{r.code}.pkl"), float(r.shares))
            for r in uni.itertuples() if (OHLCV / f"{r.code}.pkl").exists()]
    if args.limit:
        jobs = jobs[:args.limit]
    print(f"[start] 종목 {len(jobs)}개, workers={args.workers}", flush=True)

    acc, basel, cut = {}, [], {}
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(_worker, j) for j in jobs]
        for i, f in enumerate(as_completed(futs)):
            code, r, b = f.result()
            if b:
                basel.append(b)
            if not r:
                continue
            for key, d in r.items():
                if d.get("n_used", 0) == 0:
                    cut[key] = cut.get(key, 0) + d.get("n_cut", 0)
                    continue
                acc.setdefault(key, []).append(d)
                cut[key] = cut.get(key, 0) + d.get("n_cut", 0)
            if (i + 1) % 400 == 0:
                print(f"[eval] {i+1}/{len(jobs)}  {time.time()-t0:.0f}s", flush=True)

    fields = ("sd", "mfc", "mfh", "fin", "cost", "rc", "am20", "am30", "am40",
              "gap20", "gap30", "gap40", "mx20", "mx30", "mx40", "year", "entry", "code")
    merged = {k: {fl: np.concatenate([x[fl] for x in lst]) for fl in fields} for k, lst in acc.items()}

    k = idx_close.copy()
    k.index = pd.to_datetime(k.index)
    kma20 = k.rolling(20).mean()
    bull_map = (k > kma20)

    summary = {}
    for key, d in merged.items():
        sd = pd.to_datetime([str(x) for x in d["sd"]], format="%Y%m%d")
        bull = bull_map.reindex(sd).fillna(False).to_numpy()
        summary[key] = summarize(d, bull)

    baseline = aggregate_baseline(basel)
    kv = idx_close.to_numpy(dtype=float)
    for w in (20, 30, 40):
        kf = kv[w:] / kv[:-w] - 1
        baseline[f"kospi_{w}d"] = {"mean": float(np.nanmean(kf)), "median": float(np.nanmedian(kf)),
                                   "win": float(np.mean(kf > 0)), "n": int(len(kf))}

    meta = {"codes": len(acc), "variants": list(merged.keys()), "signal_cut_insufficient_fwd": cut,
            "source": "work/backtest/cache/ohlcv (수정주가 aclose/ahigh/aopen)",
            "period": "2015-01-02~2026-11-20", "windows": list(WINDOWS), "frames": list(FRAMES),
            "cost": {"slip": SLIP, "comm": COMM, "tax_by_year": TAX_BY_YEAR},
            "universe": {"min_mktcap": MIN_MKTCAP, "min_turnover_ma20": MIN_VALUE, "max_disp20": MAX_DISP20},
            "names": names}
    WORK.mkdir(parents=True, exist_ok=True)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps({"meta": meta, "summary": summary, "baseline": baseline},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
    np.savez_compressed(WORK / "mfe_30_40d_signals.npz",
                        **{f"{k2}__{f}": v for k2, d in merged.items() for f, v in d.items() if f != "code"})
    print(f"[done] {time.time()-t0:.0f}s -> {OUT_JSON}")
    print(json.dumps({k2: v["n"] for k2, v in summary.items()}, ensure_ascii=False, indent=0))


if __name__ == "__main__":
    main()
