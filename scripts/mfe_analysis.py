#!/usr/bin/env python3
"""21일선/61일선 돌파 매수 후 '최대 도달 수익률(MFE)' 실측.

질문: 21선(또는 61선)을 뚫고 캔들까지 뚫은 날 매수하면, 며칠 안에 최고 수익이
얼마나 나는가? 그 최고점이 비용을 빼고도 쓸 만한가? 진입 직후가 고점인 경우는 얼마나 되는가?

데이터: work/backtest/cache/ohlcv/*.pkl (수정주가 OHLC, 2015-01-02~2026-11-20)
비용  : 슬리피지 0.10%*2 + 수수료 0.015%*2 + 증권거래세(매도 연도별)

사용법:
  python3 scripts/mfe_analysis.py --workers 2
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
OUT_JSON = REPO / "data" / "ma-breakout-mfe.json"
WORK = REPO.parent / "work" / "backtest"

MIN_MKTCAP = 50_000_000_000
MIN_VALUE = 10_000_000_000
MAX_DISP20 = 0.15
WINDOWS = (1, 2, 3, 5, 10, 20)
TAX_BY_YEAR = {2015: 0.0030, 2016: 0.0030, 2017: 0.0030, 2018: 0.0030, 2019: 0.0030,
               2020: 0.0025, 2021: 0.0023, 2022: 0.0023, 2023: 0.0020, 2024: 0.0018,
               2025: 0.0015, 2026: 0.0020}
SLIP = 0.001
COMM = 0.00015

PREFERRED_RE = re.compile(r"(?:\d?우B?C?)$|우\(|우B$")
NON_COMMON_KEYWORDS = ("스팩", "리츠", "인프라", "ETN", "ETF", "선물", "레버리지", "인버스", "합성")

FIELDS = ("sd", "mfc", "mfh", "fin", "cost", "argmax", "gap", "year", "entry", "rc20", "code")


def is_common_stock(name: str, end_type: str) -> bool:
    if end_type and end_type != "stock":
        return False
    n = name or ""
    if any(k in n for k in NON_COMMON_KEYWORDS):
        return False
    if PREFERRED_RE.search(n):
        return False
    return True


def _apply_entry(df: pd.DataFrame, mask: np.ndarray, entry_mode: str, code: str = "") -> dict:
    """mask(신호일) → 진입 후 창별 MFE/수익률 배열."""
    ac = df["aclose"].to_numpy(dtype=float)
    ao = df["aopen"].to_numpy(dtype=float)
    ah = df["ahigh"].to_numpy(dtype=float)
    dates = df.index
    rt = 2 * SLIP + 2 * COMM
    tax_arr = np.array([TAX_BY_YEAR.get(int(d.year), 0.0020) for d in dates])

    if entry_mode == "next_open":
        idxs = np.flatnonzero(mask)
        idxs = idxs[idxs + 21 <= len(ac) - 1]
        if idxs.size == 0:
            return {}
        e0s = idxs + 1
        entry = ao[e0s]
        good = entry > 0
        idxs, e0s, entry = idxs[good], e0s[good], entry[good]
    else:  # 신호일 종가 매수
        idxs = np.flatnonzero(mask)
        idxs = idxs[idxs + 21 <= len(ac) - 1]
        if idxs.size == 0:
            return {}
        e0s = idxs + 1
        entry = ac[idxs]
        good = entry > 0
        idxs, e0s, entry = idxs[good], e0s[good], entry[good]
    if idxs.size == 0:
        return {}

    m = idxs.size
    mfc = np.full((m, len(WINDOWS)), np.nan)
    mfh = np.full((m, len(WINDOWS)), np.nan)
    fin = np.full((m, len(WINDOWS)), np.nan)
    cst = np.full((m, len(WINDOWS)), np.nan)
    argmax = np.zeros(m, dtype=int)
    gaps = np.full(m, np.nan)
    rc20 = np.full((m, 20), np.nan)
    for k in range(m):
        s = e0s[k]
        rc = ac[s:s + 20] / entry[k] - 1.0
        rh = ah[s:s + 20] / entry[k] - 1.0
        if rc.size < 20:
            continue
        rc20[k] = rc
        for wi, w in enumerate(WINDOWS):
            mfc[k, wi] = rc[:w].max()
            mfh[k, wi] = rh[:w].max()
            fin[k, wi] = rc[w - 1]
            cst[k, wi] = rt + tax_arr[s + w - 1]
        argmax[k] = int(np.argmax(rc)) + 1
        gaps[k] = rc.max() - rc[-1]
    ok = ~np.isnan(mfc[:, -1])
    return dict(sd=np.array([int(dates[i].strftime("%Y%m%d")) for i in idxs[ok]], dtype=np.int64),
                mfc=mfc[ok], mfh=mfh[ok], fin=fin[ok], cost=cst[ok],
                argmax=argmax[ok], gap=gaps[ok], entry=entry[ok], rc20=rc20[ok],
                code=np.array([code] * int(ok.sum())),
                year=np.array([dates[i].year for i in idxs[ok]]))


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
    turn_ma_ok = tv_ma20 >= MIN_VALUE
    turn_d_ok = tv >= MIN_VALUE
    disp_ok = disp20 <= MAX_DISP20

    res = {}
    for tag, ma in (("21", ma21), ("61", ma61)):
        above = ac > ma
        prev_above = np.r_[False, above[:-1]]
        cross = above & ~prev_above & np.isfinite(ma)
        body = cross & (ao <= ma)
        cross[:62] = False
        body[:62] = False
        variants = {
            f"A{tag}": cross & base_ok & turn_ma_ok & disp_ok,
            f"B{tag}": body & base_ok & turn_ma_ok & disp_ok,
            f"A{tag}_turnD": cross & base_ok & turn_d_ok & disp_ok,
            f"B{tag}_turnD": body & base_ok & turn_d_ok & disp_ok,
            f"A{tag}_noDisp": cross & base_ok & turn_ma_ok,
            f"B{tag}_noDisp": body & base_ok & turn_ma_ok,
        }
        for key, m in variants.items():
            d = _apply_entry(df, m, "next_open", code)
            if d:
                res[key] = d
        d = _apply_entry(df, variants[f"A{tag}"], "close", code)
        if d:
            res[f"A{tag}_close"] = d
        d = _apply_entry(df, variants[f"B{tag}"], "close", code)
        if d:
            res[f"B{tag}_close"] = d
    return res


COST_APX = 2 * SLIP + 2 * COMM + 0.0019   # 평균 왕복비용 근사(0.46%) — 경로 지표용


def _days_to(rc: np.ndarray, thr: float) -> np.ndarray:
    hit = rc >= thr
    any_hit = hit.any(axis=1)
    d = np.zeros(rc.shape[0], dtype=int)
    d[any_hit] = hit.argmax(axis=1)[any_hit] + 1
    return d


def _rule(rc: np.ndarray, tgt: float, stop: float):
    """종가 기준 목표/손절 규칙: 목표 +tgt 먼저 닿으면 익절, -stop 먼저 닿으면 손절, 아니면 20일 종가 청산."""
    hit = rc >= tgt
    ls = rc <= -stop
    a_hit, a_ls = hit.any(axis=1), ls.any(axis=1)
    i_hit = np.where(a_hit, hit.argmax(axis=1), 10**6)
    i_ls = np.where(a_ls, ls.argmax(axis=1), 10**6)
    ret = np.where(np.isfinite(rc[:, -1]), rc[:, -1], 0.0)
    tgt_win = a_hit & (i_hit <= i_ls)
    stop_win = a_ls & (i_ls < i_hit)
    ret[tgt_win] = rc[tgt_win, i_hit[tgt_win]]
    ret[stop_win] = rc[stop_win, i_ls[stop_win]]
    return ret - COST_APX, tgt_win, stop_win


def baseline_for(df: pd.DataFrame, shares: float) -> dict:
    """같은 유니버스에서 '아무 날이나 매수'했을 때의 MFE 기준선(전 종목·전 날짜 집계)."""
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
    rt = 2 * SLIP + 2 * COMM
    tax_arr = np.array([TAX_BY_YEAR.get(int(d.year), 0.0020) for d in dates])

    sw_c = {w: np.lib.stride_tricks.sliding_window_view(ac, w) for w in WINDOWS}
    sw_h = {w: np.lib.stride_tricks.sliding_window_view(ah, w) for w in WINDOWS}
    sw_arg = np.lib.stride_tricks.sliding_window_view(ac[1:], 20)

    out = {}
    for w in WINDOWS:
        i = np.arange(len(ac) - w)          # 진입일(다음 거래일 시가) = i+1
        use = ok[i] & ok[i + 1]
        i = i[use]
        if i.size == 0:
            out[str(w)] = None
            continue
        entry = ao[i + 1]
        mfc = sw_c[w].max(axis=1)[i + 1] / entry - 1
        mfh = sw_h[w].max(axis=1)[i + 1] / entry - 1
        fin = ac[i + w] / entry - 1
        cost = rt + tax_arr[i + w]
        out[str(w)] = dict(n=int(i.size),
                           sum_c=float(mfc.sum()), sum_h=float(mfh.sum()),
                           sum_c_net=float((mfc - cost).sum()),
                           sum_fin_net=float((fin - cost).sum()),
                           hit=float((mfc - cost > 0).sum()), hit_h=float((mfh - cost > 0).sum()),
                           p1=float((mfc - cost > 0.01).sum()), p3=float((mfc - cost > 0.03).sum()),
                           p5=float((mfc - cost > 0.05).sum()), p10=float((mfc - cost > 0.10).sum()),
                           p3h=float((mfh - cost > 0.03).sum()), p5h=float((mfh - cost > 0.05).sum()),
                           fin_win=float((fin - cost > 0).sum()))
    # 경로 지표: 20일 전방 종가수익률 행렬 → 목표 도달 소요일·목표/손절 규칙
    i = np.arange(len(ac) - 20)
    use = ok[i] & ok[i + 1]
    i = i[use]
    entry = ao[i + 1]
    sw20 = np.lib.stride_tricks.sliding_window_view(ac[1:], 20)[i]
    rc20 = sw20 / entry[:, None] - 1
    path = {"n": int(i.size)}
    for t in (0.01, 0.02, 0.03, 0.05):
        thr = t + COST_APX
        d = _days_to(rc20, thr)
        hit = d > 0
        path[f"d{int(t*100)}"] = {"hit": int(hit.sum()),
                                  "days_sum": int(d[hit].sum()),
                                  "le3": int((d[hit] <= 3).sum()),
                                  "le5": int((d[hit] <= 5).sum())}
    for tgt, stop in ((0.03, 0.05), (0.05, 0.05), (0.03, 0.03)):
        r, tw, sw_ = _rule(rc20, tgt + COST_APX, stop)
        path[f"rule{int(tgt*100)}_{int(stop*100)}"] = {"sum": float(r.sum()), "win": float((r > 0).sum()),
                                                       "tgt": float(tw.sum()), "stop": float(sw_.sum())}
    out["path"] = path

    # 20일 창 argmax 분포(진입 다음날=1일째)
    i = np.arange(len(ac) - 21)
    use = ok[i] & ok[i + 1]
    i = i[use]
    if i.size:
        am = sw_arg.argmax(axis=1)[i] + 1
        hist = [int((am == k).sum()) for k in range(1, 21)]
        out["argmax"] = {"n": int(i.size), "hist": hist}
    return out


def _worker(args):
    code, path, shares = args
    try:
        df = pd.read_pickle(path)
        return code, variants_for(df, shares, code), baseline_for(df, shares)
    except Exception as e:  # noqa: BLE001
        print(f"[skip] {code}: {e}", file=sys.stderr)
        return code, {}, {}


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

    acc, codes, basel = {}, [], []
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(_worker, j) for j in jobs]
        for i, f in enumerate(as_completed(futs)):
            code, r, b = f.result()
            if b:
                basel.append(b)
            if not r:
                continue
            codes.append(code)
            for key, d in r.items():
                acc.setdefault(key, []).append(d)
            if (i + 1) % 400 == 0:
                print(f"[eval] {i+1}/{len(jobs)}  {time.time()-t0:.0f}s", flush=True)

    merged = {}
    for key, lst in acc.items():
        merged[key] = {f: np.concatenate([x[f] for x in lst]) for f in FIELDS if f in lst[0]}

    # 시장상태: 신호일 코스피 종가 > 코스피 MA20
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
    # 코스피 20거래일 보유 수익률(같은 기간, 비용 없음)
    kv = idx_close.to_numpy(dtype=float)
    kf = kv[20:] / kv[:-20] - 1
    baseline["kospi_20d"] = {"mean": float(np.nanmean(kf)), "median": float(np.nanmedian(kf)),
                            "win": float(np.mean(kf > 0)), "n": int(len(kf))}

    meta = {"codes": len(codes), "source": "work/backtest/cache/ohlcv (수정주가 aclose/ahigh/aopen)",
            "period": "2015-01-02~2026-11-20", "windows": list(WINDOWS),
            "cost": {"slip": SLIP, "comm": COMM, "tax_by_year": TAX_BY_YEAR},
            "universe": {"min_mktcap": MIN_MKTCAP, "min_turnover_ma20": MIN_VALUE, "max_disp20": MAX_DISP20}}
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "mfe_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps({"meta": meta, "summary": summary, "baseline": baseline},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
    np.savez_compressed(WORK / "mfe_signals.npz", **{f"{k}__{f}": v for k, d in merged.items() for f, v in d.items()})
    print(f"[done] {time.time()-t0:.0f}s")
    print(json.dumps({k: v["n"] for k, v in summary.items()}, ensure_ascii=False, indent=0))


def aggregate_baseline(basel: list) -> dict:
    out = {}
    for w in [str(x) for x in WINDOWS]:
        rows = [r[w] for r in basel if r.get(w)]
        if not rows:
            continue
        n = sum(r["n"] for r in rows)
        agg = {k: sum(r[k] for r in rows) / n for k in
               ("sum_c", "sum_h", "sum_c_net", "sum_fin_net", "hit", "hit_h",
                "p1", "p3", "p5", "p10", "p3h", "p5h", "fin_win")}
        out[w] = {"n": n,
                  "mfe_c_gross": agg["sum_c"], "mfe_h_gross": agg["sum_h"],
                  "mfe_c_net": agg["sum_c_net"], "hold_net": agg["sum_fin_net"],
                  "hit_net": agg["hit"], "hit_h_net": agg["hit_h"],
                  "p1": agg["p1"], "p3": agg["p3"], "p5": agg["p5"], "p10": agg["p10"],
                  "p3h": agg["p3h"], "p5h": agg["p5h"], "hold_win": agg["fin_win"]}
    ph = [r["path"] for r in basel if r.get("path")]
    if ph:
        n = sum(p["n"] for p in ph)
        pb = {"n": int(n)}
        for t in (1, 2, 3, 5):
            k = f"d{t}"
            hit = sum(p[k]["hit"] for p in ph)
            pb[k] = {"hit": float(hit / n),
                     "med_days": None,
                     "le3": float(sum(p[k]["le3"] for p in ph) / hit) if hit else None,
                     "le5": float(sum(p[k]["le5"] for p in ph) / hit) if hit else None,
                     "days_sum": int(sum(p[k]["days_sum"] for p in ph)),
                     "mean_days": float(sum(p[k]["days_sum"] for p in ph) / hit) if hit else None}
        for tgt, stop in ((3, 5), (5, 5), (3, 3)):
            k = f"rule{tgt}_{stop}"
            sm = sum(p[k]["sum"] for p in ph)
            pb[k] = {"ret": float(sm / n), "win": float(sum(p[k]["win"] for p in ph) / n),
                     "tgt": float(sum(p[k]["tgt"] for p in ph) / n),
                     "stop": float(sum(p[k]["stop"] for p in ph) / n)}
        out["path"] = pb

    am = [r["argmax"] for r in basel if r.get("argmax")]
    if am:
        n = sum(a["n"] for a in am)
        hist = np.sum([a["hist"] for a in am], axis=0)
        out["argmax"] = {"n": int(n), "dist": {str(k + 1): float(hist[k] / n) for k in range(20)},
                            "le3": float(hist[:3].sum() / n)}
    return out


def summarize(d: dict, bull: np.ndarray) -> dict:
    n = int(d["mfc"].shape[0])
    mfc, mfh, fin, cost = d["mfc"], d["mfh"], d["fin"], d["cost"]
    net_c = mfc - cost
    net_h = mfh - cost
    out = {"n": n, "windows": {}}
    for wi, w in enumerate(WINDOWS):
        out["windows"][str(w)] = {
            "mfe_c_gross": float(np.mean(mfc[:, wi])),
            "mfe_c_med": float(np.median(mfc[:, wi])),
            "mfe_c_net": float(np.mean(net_c[:, wi])),
            "mfe_c_net_med": float(np.median(net_c[:, wi])),
            "mfe_h_gross": float(np.mean(mfh[:, wi])),
            "mfe_h_net": float(np.mean(net_h[:, wi])),
            "hit_net": float(np.mean(net_c[:, wi] > 0)),
            "hit_h_net": float(np.mean(net_h[:, wi] > 0)),
            "p1": float(np.mean(net_c[:, wi] > 0.01)),
            "p3": float(np.mean(net_c[:, wi] > 0.03)),
            "p5": float(np.mean(net_c[:, wi] > 0.05)),
            "p10": float(np.mean(net_c[:, wi] > 0.10)),
            "p1h": float(np.mean(net_h[:, wi] > 0.01)),
            "p3h": float(np.mean(net_h[:, wi] > 0.03)),
            "p5h": float(np.mean(net_h[:, wi] > 0.05)),
            "p10h": float(np.mean(net_h[:, wi] > 0.10)),
            "hold_net": float(np.mean(fin[:, wi] - cost[:, wi])),
            "hold_net_med": float(np.median(fin[:, wi] - cost[:, wi])),
            "hold_win": float(np.mean(fin[:, wi] - cost[:, wi] > 0)),
        }
    am = d["argmax"]
    out["argmax"] = {
        "1": float(np.mean(am == 1)), "2": float(np.mean(am == 2)), "3": float(np.mean(am == 3)),
        "4-5": float(np.mean((am >= 4) & (am <= 5))), "6-10": float(np.mean((am >= 6) & (am <= 10))),
        "11-20": float(np.mean(am >= 11)), "le3": float(np.mean(am <= 3)),
    }
    never = mfc[:, -1] <= 0
    out["never_up"] = {"share": float(np.mean(never)),
                       "mfe_mean": float(np.mean(mfc[never, -1])) if never.any() else None,
                       "net20": float(np.mean(net_c[never, -1])) if never.any() else None}
    pat = (am <= 3) & (fin[:, -1] < 0)
    out["peak_early_down"] = {"share": float(np.mean(pat)),
                              "hold20": float(np.mean(fin[pat, -1] - cost[pat, -1])) if pat.any() else None}
    posf = (fin[:, -1] - cost[:, -1]) > 0
    out["pos_final"] = {"share": float(np.mean(posf)),
                        "mfe20": float(np.mean(mfc[posf, -1])) if posf.any() else None,
                        "final20": float(np.mean(fin[posf, -1])) if posf.any() else None}
    rc20 = d.get("rc20")
    if rc20 is not None and rc20.size:
        rc = rc20[~np.isnan(rc20[:, -1])]
        pb = {"n": int(rc.shape[0])}
        for t in (0.01, 0.02, 0.03, 0.05):
            dd = _days_to(rc, t + COST_APX)
            hit = dd > 0
            pb[f"d{int(t*100)}"] = {"hit": float(hit.mean()),
                                    "med_days": float(np.median(dd[hit])) if hit.any() else None,
                                    "q25": float(np.percentile(dd[hit], 25)) if hit.any() else None,
                                    "q75": float(np.percentile(dd[hit], 75)) if hit.any() else None,
                                    "le3": float((dd[hit] <= 3).mean()) if hit.any() else None,
                                    "le5": float((dd[hit] <= 5).mean()) if hit.any() else None,
                                    "mean_days": float(dd[hit].mean()) if hit.any() else None}
        for tgt, stop in ((0.03, 0.05), (0.05, 0.05), (0.03, 0.03)):
            r, tw, sw_ = _rule(rc, tgt + COST_APX, stop)
            pb[f"rule{int(tgt*100)}_{int(stop*100)}"] = {"ret": float(r.mean()),
                                                         "med": float(np.median(r)),
                                                         "win": float((r > 0).mean()),
                                                         "tgt": float(tw.mean()),
                                                         "stop": float(sw_.mean())}
        out["path"] = pb
    out["gap20"] = float(np.mean(d["gap"]))
    out["cost20"] = float(np.mean(cost[:, -1]))
    out["by_year"] = {}
    for y in sorted(set(int(x) for x in d["year"])):
        sel = d["year"] == y
        out["by_year"][str(y)] = {"n": int(sel.sum()),
                                  "mfe_net20": float(np.mean(net_c[sel, -1])),
                                  "hold20": float(np.mean(fin[sel, -1] - cost[sel, -1])),
                                  "p3": float(np.mean(net_c[sel, -1] > 0.03)),
                                  "p5": float(np.mean(net_c[sel, -1] > 0.05))}
    out["by_market"] = {}
    for label, sel in (("bull", bull), ("bear", ~bull)):
        if sel.sum() == 0:
            continue
        out["by_market"][label] = {"n": int(sel.sum()),
                                   "mfe_net20": float(np.mean(net_c[sel, -1])),
                                   "p3": float(np.mean(net_c[sel, -1] > 0.03)),
                                   "p5": float(np.mean(net_c[sel, -1] > 0.05)),
                                   "hold20": float(np.mean(fin[sel, -1] - cost[sel, -1])),
                                   "le3": float(np.mean(am[sel] <= 3))}
    return out


if __name__ == "__main__":
    main()
