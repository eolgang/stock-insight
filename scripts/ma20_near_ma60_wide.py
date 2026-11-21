#!/usr/bin/env python3
"""MA20 근접 + MA60 이격 큰 자리 — 단독 규칙 검증 + 급등 사건 분석 (2026-11-21 후속 과제).

규칙(앞선 보고서 히트맵 최적 칸을 수치로 고정):
    disp20 = 종가/MA20 - 1  in  [-2%, +2%)
    disp60 = 종가/MA60 - 1  <  -10%      (종가가 60일선보다 10% 이상 아래)
    시총 500억↑ & 20일 평균 거래대금 100억↑ & 거래량>0, 우선주·스팩·ETF/ETN 제외
    진입 = 신호 다음 거래일 시가, 청산 = 보유 마지막 날 종가
    비용 = 슬리피지 0.10%x2 + 수수료 0.015%x2 + 증권거래세(연도별)

모드
  extract : 규칙 신호 1건=1행 + 전 유니버스 임의 날 대조군
            -> work/backtest/ma20_ma60_signals.pkl, ma20_ma60_random.npz
  events  : 월봉 +20% 급등 달 사건(진입 T-10..T-1 / 월초 / +20% 확인 후) + 전 유니버스 5일 간격 패널
            -> work/backtest/ma20_ma60_events.pkl, ma20_ma60_panel.pkl

사용법
  python3 scripts/ma20_near_ma60_wide.py extract --workers 2
  python3 scripts/ma20_near_ma60_wide.py events  --workers 2
"""
from __future__ import annotations

import argparse
import sys
import time
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ma_support_rebound import (  # noqa: E402
    CACHE, OHLCV, WORK, MIN_MKTCAP, MIN_VALUE, SLIP, COMM, TAX_BY_YEAR,
    SIGNAL_START, PREFERRED_RE, NON_COMMON_KEYWORDS, prep,
)

HORIZONS = (5, 10, 15, 20, 30, 40)
D20_LO, D20_HI = -0.02, 0.02
D60_TH = -0.10
TP_SET = (None, 0.05, 0.10, 0.15, 0.20)
SL_SET = (None, -0.05, -0.08)
SWEEP_H = (20, 40)
EPOCH = np.datetime64("2015-01-01")

FEATS = ("disp5", "disp20", "disp60", "disp120", "gap20_60", "log_mktcap", "log_t20", "atr14",
         "box60", "vr", "v520", "vs10", "vr_max10", "d52", "depth10", "sl20", "sl60",
         "ret5", "ret20", "ret60", "since_cross60", "rsi14", "up_streak", "body",
         "range_pct", "tail_lo", "tail_hi", "big_yang", "big_eum", "kospi_dd60",
         "kospi_dd250", "kospi_r20", "kospi_d60")


def universe() -> pd.DataFrame:
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
    return pd.DataFrame(keep, columns=["code", "name", "market", "shares"]).set_index("code")


UNI = None
IDX = None


def init_worker(uni, idx):
    global UNI, IDX
    UNI, IDX = uni, idx


# ---------------------------------------------------------------------------
# 진입 시점에 알 수 있는 특징 행렬
# ---------------------------------------------------------------------------
def feat_matrix(x: pd.DataFrame) -> dict:
    c = x["c"].to_numpy(float); o = x["o"].to_numpy(float)
    h = x["h"].to_numpy(float); lo = x["l"].to_numpy(float)
    dates = x.index
    n = len(x)
    kos = IDX["close_k"].reindex(dates).ffill()
    ks = pd.Series(kos.to_numpy(float), index=dates)
    kma20 = x["kospi_ma20"].to_numpy(float)
    kma60 = ks.rolling(60).mean().to_numpy(float)
    cs = pd.Series(c, index=dates)
    above60 = pd.Series(c > x["ma60"].to_numpy(float), index=dates)
    cross60 = (above60 & (~above60.shift(1).fillna(True))).to_numpy(bool)
    since_cross = np.full(n, 9999.0)
    last = 9999
    for i in range(n):
        if cross60[i]:
            last = 0
        elif last < 9999:
            last += 1
        since_cross[i] = last
    # RSI14
    d = cs.diff()
    up = d.clip(lower=0).rolling(14).mean()
    dn = (-d.clip(upper=0)).rolling(14).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    # 연속 상승일수
    upday = (d > 0).to_numpy()
    streak = np.zeros(n)
    for i in range(n):
        streak[i] = streak[i - 1] + 1 if (i > 0 and upday[i]) else (1 if upday[i] else 0)
    body = np.where(c > 0, (c - o) / c, np.nan)
    rng = np.where(c > 0, (h - lo) / c, np.nan)
    tl = np.where(c > 0, (np.minimum(o, c) - lo) / c, np.nan)
    th = np.where(c > 0, (h - np.maximum(o, c)) / c, np.nan)
    box60 = ((pd.Series(h).rolling(60).max() - pd.Series(lo).rolling(60).min()) / cs)
    return {
        "disp5": x["disp5"].to_numpy(float), "disp20": x["disp20"].to_numpy(float),
        "disp60": x["disp60"].to_numpy(float), "disp120": x["disp120"].to_numpy(float),
        "gap20_60": (x["ma20"].to_numpy(float) / x["ma60"].to_numpy(float) - 1),
        "log_mktcap": np.log10(np.maximum(x["mktcap"].to_numpy(float), 1.0)),
        "log_t20": np.log10(np.maximum(x["t20"].to_numpy(float), 1.0) + 1.0),
        "atr14": x["atr14"].to_numpy(float), "box60": box60.to_numpy(float),
        "vr": x["vr"].to_numpy(float), "v520": x["v520"].to_numpy(float),
        "vs10": x["vs10"].to_numpy(float), "vr_max10": x["vr_max10"].to_numpy(float),
        "d52": x["d52"].to_numpy(float), "depth10": x["depth10"].to_numpy(float),
        "sl20": x["sl20"].to_numpy(float), "sl60": x["sl60"].to_numpy(float),
        "ret5": (cs / cs.shift(5)).to_numpy(float) - 1, "ret20": (cs / cs.shift(20)).to_numpy(float) - 1,
        "ret60": (cs / cs.shift(60)).to_numpy(float) - 1,
        "since_cross60": since_cross, "rsi14": rsi.to_numpy(float),
        "up_streak": streak, "body": body, "range_pct": rng, "tail_lo": tl, "tail_hi": th,
        "big_yang": ((c - o) / np.where(o > 0, o, np.nan) >= 0.05).astype(float),
        "big_eum": ((c - o) / np.where(o > 0, o, np.nan) <= -0.05).astype(float),
        "kospi_dd60": (ks / ks.rolling(60).max() - 1).to_numpy(float),
        "kospi_dd250": (ks / ks.rolling(250).max() - 1).to_numpy(float),
        "kospi_r20": (ks / ks.shift(20) - 1).to_numpy(float),
        "kospi_d60": np.where(np.isfinite(kma60), kos.to_numpy(float) / kma60 - 1, np.nan),
        "kospi_bull": x["bull"].to_numpy(bool),
    }


def _prep_stock(code):
    row = UNI.loc[code]
    d = pd.read_pickle(OHLCV / f"{code}.pkl")
    if d.empty or len(d) < 300:
        return None
    x = prep(d, float(row["shares"]), IDX)
    n = len(x)
    dates = x.index
    start_at = np.searchsorted(dates.values, np.datetime64(SIGNAL_START))
    uni_ok = ((x["mktcap"].to_numpy(float) >= MIN_MKTCAP)
              & (x["t20"].to_numpy(float) >= MIN_VALUE)
              & (x["vol0"].to_numpy(bool))
              & (np.arange(n) >= start_at))
    uni_ok &= (np.isfinite(x["ma20"].to_numpy(float)) & np.isfinite(x["ma60"].to_numpy(float))
               & np.isfinite(x["d52"].to_numpy(float)))
    years = dates.year.to_numpy()
    tax = np.array([TAX_BY_YEAR.get(int(y), 0.0020) for y in years])
    dnum = ((dates.values - EPOCH) / np.timedelta64(1, "D")).astype(np.int32)
    return x, n, dates, uni_ok, years, tax, dnum, row


def _fwd(c, o, h, lo, tax, e, hz, cost=None):
    n = len(c)
    ex = e + hz - 1
    if e >= n or ex >= n or not np.isfinite(c[ex]) or not np.isfinite(o[e]) or o[e] <= 0:
        return np.nan, np.nan, np.nan
    if cost is None:
        cost = 2 * SLIP + 2 * COMM + tax[ex]
    seg_h, seg_l = h[e:ex + 1], lo[e:ex + 1]
    mfe = float(np.nanmax(seg_h) / o[e] - 1) if np.isfinite(seg_h).any() else np.nan
    mae = float(np.nanmin(seg_l) / o[e] - 1) if np.isfinite(seg_l).any() else np.nan
    return float(c[ex] / o[e] - 1 - cost), mfe, mae


def _sweep(h, l, c, e, entry, hz, tp, sl, cost):
    last = min(e + hz - 1, len(c) - 1)
    for k in range(e, last + 1):
        if sl is not None and l[k] / entry - 1 <= sl:
            return sl - cost
        if tp is not None and h[k] / entry - 1 >= tp:
            return tp - cost
    return c[last] / entry - 1 - cost


# ---------------------------------------------------------------------------
def work_signals(code: str):
    p = _prep_stock(code)
    if p is None:
        return None
    x, n, dates, uni_ok, years, tax, dnum, row = p
    c = x["c"].to_numpy(float); o = x["o"].to_numpy(float)
    h = x["h"].to_numpy(float); lo = x["l"].to_numpy(float)
    F = feat_matrix(x)
    sig = uni_ok & (F["disp20"] >= D20_LO) & (F["disp20"] < D20_HI) & (F["disp60"] < D60_TH)
    recs = []
    prev, ep = -99, 0
    base = {"code": code, "name": row["name"], "market": row["market"]}
    for i in np.flatnonzero(sig):
        e = i + 1
        if e >= n or not np.isfinite(o[e]) or o[e] <= 0:
            continue
        rec = dict(base)
        rec.update({"date": dates[i], "entry_date": dates[e], "sig_i": int(i), "yr": int(years[e]),
                    "entry_px": float(o[e]), "dnum": int(dnum[i]), "ep_in_run": int(ep),
                    "ma20": float(x["ma20"].to_numpy(float)[i]), "ma60": float(x["ma60"].to_numpy(float)[i]),
                    "close": float(c[i])})
        for f in FEATS:
            v = F[f][i]
            rec[f] = bool(v) if f == "kospi_bull" else float(v)
        ep = ep + 1 if i - prev <= 5 else 0
        prev = i
        for hz in HORIZONS:
            r, mfe, mae = _fwd(c, o, h, lo, tax, e, hz)
            rec[f"r{hz}"], rec[f"mfe{hz}"], rec[f"mae{hz}"] = r, mfe, mae
            if hz in SWEEP_H and np.isfinite(r):
                cost = 2 * SLIP + 2 * COMM + tax[min(e + hz - 1, n - 1)]
                for tp in TP_SET:
                    for slv in SL_SET:
                        key = f"s{hz}_tp{'-' if tp is None else int(round(tp*100))}_sl{'-' if slv is None else int(round(abs(slv)*100))}"
                        rec[key] = float(_sweep(h, lo, c, e, o[e], hz, tp, slv, cost))
        recs.append(rec)
    # 무작위 대조군
    packs = {}
    idx_ok = np.flatnonzero(uni_ok)
    for hz in (20, 40):
        good = idx_ok[idx_ok + hz < n] + 1
        ok = (good + hz - 1 < n) & np.isfinite(o[good]) & (o[good] > 0)
        e = good[ok]
        if e.size == 0:
            continue
        entry = o[e]
        ex = e + hz - 1
        ok2 = np.isfinite(c[ex])
        e, entry, ex = e[ok2], entry[ok2], ex[ok2]
        if e.size == 0:
            continue
        cost = 2 * SLIP + 2 * COMM + tax[ex]
        r = c[ex] / entry - 1 - cost
        mfe = np.array([np.nanmax(h[a:b + 1]) for a, b in zip(e, ex)], float) / entry - 1
        mae = np.array([np.nanmin(lo[a:b + 1]) for a, b in zip(e, ex)], float) / entry - 1
        packs[hz] = (dnum[e].astype(np.int32), r.astype(np.float32),
                     mfe.astype(np.float32), mae.astype(np.float32))
    return recs, packs


def work_events(code: str):
    p = _prep_stock(code)
    if p is None:
        return None
    x, n, dates, uni_ok, years, tax, dnum, row = p
    c = x["c"].to_numpy(float); o = x["o"].to_numpy(float)
    h = x["h"].to_numpy(float); lo = x["l"].to_numpy(float)
    F = feat_matrix(x)
    base = {"code": code, "name": row["name"], "market": row["market"]}
    per = pd.PeriodIndex(dates, freq="M")
    dfm = pd.DataFrame({"c": c, "per": per, "i": np.arange(n), "uni": uni_ok})
    last = dfm.groupby("per")["i"].max()
    first = dfm.groupby("per")["i"].min()
    closes = {p_: c[i] for p_, i in last.items()}
    cnt = dfm.groupby("per")["i"].size()
    keys = [p_ for p_ in last.index if p_ in first.index]
    rows = []

    def add(kind, i_feat, e, extra):
        if i_feat < 0 or e >= n:
            return
        rec = dict(base)
        rec.update({"kind": kind, "feat_date": dates[i_feat], "entry_date": dates[e],
                    "yr": int(years[e]), "entry_px": float(o[e]) if np.isfinite(o[e]) else np.nan,
                    "dnum": int(dnum[i_feat]), "uni": int(uni_ok[i_feat]) if i_feat < len(uni_ok) else 0})
        for f in FEATS:
            rec[f] = float(F[f][i_feat])
        for hz in (20, 40):
            r, mfe, mae = _fwd(c, o, h, lo, tax, e, hz)
            rec[f"r{hz}"], rec[f"mfe{hz}"], rec[f"mae{hz}"] = r, mfe, mae
        rec.update(extra)
        rows.append(rec)

    ev_id = 0
    for k, p_ in enumerate(keys[1:], start=1):
        prev_p = keys[k - 1]
        if cnt[p_] < 15 or cnt[prev_p] < 15:
            continue
        ret_m = closes[p_] / closes[prev_p] - 1
        if not np.isfinite(ret_m):
            continue
        T = int(first[p_])
        e_month = int(last[p_])
        is_ev = ret_m >= 0.20
        extra0 = {"month": str(p_), "month_ret": float(ret_m), "event": int(is_ev), "event_id": ev_id}
        if is_ev:
            # (a) T-10 .. T-1 진입 (특징은 e-1 종가)
            for off in range(1, 11):
                e = T - off
                if e - 1 < 0:
                    continue
                add("pre", e - 1, e, {**extra0, "offset": off,
                                      "r_monthend": float(c[e_month] / o[e] - 1) if np.isfinite(o[e]) else np.nan})
            ev_id += 1
        # (b) 월초 진입 (T 시가) — 전 월 표본(기준선용)
        if T - 1 >= 0:
            add("monthstart", T - 1, T, {**extra0, "offset": 0,
                                         "r_monthend": float(c[e_month] / o[T]) - 1})
        # (c) +20% 확인 후 진입: 전월 말 종가 대비 누적 +20% 첫 도달일 다음 시가 (모든 달)
        prev_close = closes[prev_p]
        hit = None
        for j in range(T, e_month + 1):
            if c[j] / prev_close - 1 >= 0.20:
                hit = j
                break
        if hit is not None and hit + 1 < n:
            add("confirm", hit, hit + 1, {**extra0, "offset": -1,
                                          "r_monthend": float(c[e_month] / o[hit + 1]) - 1,
                                          "days_to_hit": int(hit - T + 1)})

    # 전 유니버스 5일 간격 패널 (배포 검정·분포 비교용)
    pan = []
    ok_idx = np.flatnonzero(uni_ok)
    for i in ok_idx[::5]:
        e = i + 1
        if e >= n or not np.isfinite(o[e]) or o[e] <= 0:
            continue
        rec = dict(base)
        rec.update({"date": dates[i], "entry_date": dates[e], "yr": int(years[e]), "dnum": int(dnum[i]),
                    "entry_px": float(o[e])})
        for f in FEATS:
            rec[f] = float(F[f][i])
        for hz in (20, 40):
            r, _, _ = _fwd(c, o, h, lo, tax, e, hz)
            rec[f"r{hz}"] = r
        pan.append(rec)
    de = pd.DataFrame(rows)
    dp = pd.DataFrame(pan)
    for f in (de, dp):
        for c_ in f.columns:
            if f[c_].dtype == "float64":
                f[c_] = f[c_].astype(np.float32)
    return de, dp


def work_chunk_signals(codes):
    out = []
    for code in codes:
        try:
            r = work_signals(code)
        except Exception as e:  # noqa: BLE001
            print(f"[fail] {code}: {e}", flush=True)
            continue
        if r is not None:
            out.append(r)
    return out


def work_chunk_events(codes):
    evs, pans = [], []
    for code in codes:
        try:
            r = work_events(code)
        except Exception as e:  # noqa: BLE001
            print(f"[fail] {code}: {e}", flush=True)
            continue
        if r is not None:
            evs.append(r[0]); pans.append(r[1])
    return (pd.concat(evs, ignore_index=True) if evs else pd.DataFrame(),
            pd.concat(pans, ignore_index=True) if pans else pd.DataFrame())


def _run(codes, uni, idx, worker, workers):
    chunk = 60
    chunks = [codes[i:i + chunk] for i in range(0, len(codes), chunk)]
    res_all = []
    done, t0 = 0, time.time()
    with ProcessPoolExecutor(max_workers=workers, initializer=init_worker, initargs=(uni, idx)) as ex:
        futs = {ex.submit(worker, ch): ch for ch in chunks}
        for f in as_completed(futs):
            done += len(futs[f])
            try:
                res_all.append(f.result())
            except Exception as e:  # noqa: BLE001
                print(f"[fail] chunk: {e}", flush=True)
            if done % 600 < chunk:
                print(f"[eval] {done}/{len(codes)} {time.time()-t0:.0f}s", flush=True)
    return res_all


def extract(args):
    uni = universe()
    idx = pd.read_pickle(CACHE / "index.pkl")
    codes = uni.index.tolist()
    if args.limit:
        codes = codes[: args.limit]
    print(f"[universe] {len(uni)} / run {len(codes)}", flush=True)
    t0 = time.time()
    res = [x for chunk in _run(codes, uni, idx, work_chunk_signals, args.workers) for x in chunk]
    sig_rows, acc = [], {20: [], 40: []}
    for recs, packs in res:
        sig_rows.extend(recs)
        for hz, v in packs.items():
            acc[hz].append(v)
    df = pd.DataFrame(sig_rows)
    WORK.mkdir(parents=True, exist_ok=True)
    df.to_pickle(WORK / "ma20_ma60_signals.pkl")
    packed = {}
    for hz in (20, 40):
        packed[f"di{hz}"] = np.concatenate([p[0] for p in acc[hz]])
        packed[f"r{hz}"] = np.concatenate([p[1] for p in acc[hz]])
        packed[f"mfe{hz}"] = np.concatenate([p[2] for p in acc[hz]])
        packed[f"mae{hz}"] = np.concatenate([p[3] for p in acc[hz]])
    np.savez_compressed(WORK / "ma20_ma60_random.npz", **packed)
    print(f"[done] {time.time()-t0:.0f}s signals={len(df)}", flush=True)
    print(df.groupby("yr")["r20"].agg(["count", "mean", "median"]).round(4), flush=True)


def events(args):
    uni = universe()
    idx = pd.read_pickle(CACHE / "index.pkl")
    codes = uni.index.tolist()
    if args.limit:
        codes = codes[: args.limit]
    print(f"[universe] {len(uni)} / run {len(codes)}", flush=True)
    t0 = time.time()
    res = _run(codes, uni, idx, work_chunk_events, args.workers)
    ev = [r[0] for r in res if len(r[0])]
    pan = [r[1] for r in res if len(r[1])]
    de = pd.concat(ev, ignore_index=True) if ev else pd.DataFrame()
    dp = pd.concat(pan, ignore_index=True) if pan else pd.DataFrame()
    de.to_pickle(WORK / "ma20_ma60_events.pkl")
    dp.to_pickle(WORK / "ma20_ma60_panel.pkl")
    print(f"[done] {time.time()-t0:.0f}s events={len(de)} panel={len(dp)}", flush=True)
    print(de.groupby("kind").size(), flush=True)
    sub = de[(de.kind == "pre") & (de.event == 1)]
    print("event pre rows:", len(sub), "event months:", de[de.event == 1].event_id.nunique(), flush=True)


# ---------------------------------------------------------------------------
# 민감도 그리드: (MA20 이격 폭) x (MA60 이격 임계)
# ---------------------------------------------------------------------------
WIDTHS = (0.01, 0.02, 0.03, 0.05)
TH60 = (None, -0.05, -0.10, -0.15, -0.20)


def work_grid(code: str):
    p = _prep_stock(code)
    if p is None:
        return None
    x, n, dates, uni_ok, years, tax, dnum, row = p
    c = x["c"].to_numpy(float); o = x["o"].to_numpy(float)
    h = x["h"].to_numpy(float); lo = x["l"].to_numpy(float)
    d20 = x["disp20"].to_numpy(float); d60 = x["disp60"].to_numpy(float)
    rows = []
    for w in WIDTHS:
        base = uni_ok & (d20 >= -w) & (d20 < w)
        for th in TH60:
            m = base if th is None else (base & (d60 < th))
            for i in np.flatnonzero(m):
                e = i + 1
                if e >= n:
                    continue
                r, mfe, mae = _fwd(c, o, h, lo, tax, e, 20)
                if not np.isfinite(r):
                    continue
                rows.append((f"w{int(w*100)}_t{'none' if th is None else int(abs(th)*100)}",
                             int(years[e]), bool(x["bull"].to_numpy(bool)[i]), r, mfe, mae))
    return rows


def work_chunk_grid(codes):
    out = []
    for code in codes:
        try:
            r = work_grid(code)
        except Exception as e:  # noqa: BLE001
            print(f"[fail] {code}: {e}", flush=True)
            continue
        if r:
            out.extend(r)
    return out


def grid(args):
    uni = universe()
    idx = pd.read_pickle(CACHE / "index.pkl")
    codes = uni.index.tolist()
    if args.limit:
        codes = codes[: args.limit]
    print(f"[universe] {len(uni)} / run {len(codes)}", flush=True)
    t0 = time.time()
    res = _run(codes, uni, idx, work_chunk_grid, args.workers)
    rows = [r for chunk in res for r in chunk]
    df = pd.DataFrame(rows, columns=["cell", "yr", "bull", "r20", "mfe20", "mae20"])
    df["r20"] = df["r20"].astype(np.float32)
    df["mfe20"] = df["mfe20"].astype(np.float32)
    df["mae20"] = df["mae20"].astype(np.float32)
    df.to_pickle(WORK / "ma20_ma60_grid.pkl")
    print(f"[done] {time.time()-t0:.0f}s rows={len(df)}", flush=True)
    print(df.groupby("cell")["r20"].agg(["count", "mean", "median"]).round(4), flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["extract", "events", "grid"])
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    {"extract": extract, "events": events, "grid": grid}[a.mode](a)
