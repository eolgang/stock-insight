#!/usr/bin/env python3
"""후보 A 실전 검증 + 월 중순(T−8~T−2) 거래량 선행 진입 테스트.

후보 A (직전 보고서의 S11 계열 최고 조합)
  신호: 종가가 250일 최고가 대비 −5% 이내(d52w > −0.05) & 5일 평균 거래대금 / 20일 평균 ≥ 2.0
  진입: 신호 5거래일 뒤 시가
  청산: 20거래일 뒤 종가 (변형: 손절·익절·트레일링·시장필터)

검증 1  기간 분할(2016~2020 / 2021~2026) + 청산 규칙 5종 + 시장 필터
검증 2  월 중순 창(T−8~T−2)에서 당일 거래량/거래대금 배수가 2·3배일 때
        (a) 신고가 −5% 이내 (b) MA20 위 (c) 단독 × 진입 1·3·5일 × 보유 10·20일·손절형
        + 같은 규칙을 월말(T−1·T)·그 외 구간에서도 돌려 시점 효과만 분리

데이터: work/backtest/cache/ohlcv/*.pkl (수정주가 OHLC + 원주가 거래량·거래대금), 2015-01-02~2026-11-20
비용  : 슬리피지 0.10%×2 + 수수료 0.015%×2 + 증권거래세(연도별) = 왕복 약 0.46%p 차감
        (직전 보고서와 동일하게 단순 차감 — 비교 가능성 유지)

사용법
  python3 scripts/candidate_a_verify.py --stage collect --workers 2
  python3 scripts/candidate_a_verify.py --stage report
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from monthly_surge import acc_add, acc_stats, merge_acc, prep  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("BACKTEST_CACHE", REPO.parent / "work" / "backtest" / "cache"))
OHLCV = CACHE / "ohlcv"
WORK = REPO.parent / "work" / "backtest"
ACC_PKL = WORK / "ca_acc.pkl"
OUT_JSON = REPO / "data" / "candidate-a-and-midmonth.json"
OUT_TABLES = WORK / "report_parts" / "ca_tables.md"

MIN_MKTCAP = 50_000_000_000
MIN_VALUE = 10_000_000_000
SLIP = 0.001
COMM = 0.00015
TAX_BY_YEAR = {2015: 0.0030, 2016: 0.0030, 2017: 0.0030, 2018: 0.0030, 2019: 0.0030,
               2020: 0.0025, 2021: 0.0023, 2022: 0.0023, 2023: 0.0020, 2024: 0.0018,
               2025: 0.0015, 2026: 0.0020}
YEARS = list(range(2015, 2027))
P1 = set(range(2016, 2021))
P2 = set(range(2021, 2027))

PREFERRED_RE = re.compile(r"(?:\d?우B?C?)$|우\(|우B$")
NON_COMMON_KEYWORDS = ("스팩", "리츠", "인프라", "ETN", "ETF", "선물", "레버리지", "인버스", "합성")

EXITS = {
    "hold20": dict(mode="hold", hold=20),
    "hold10": dict(mode="hold", hold=10),
    "hold40": dict(mode="hold", hold=40),
    "sl8": dict(mode="sl_tp", hold=20, sl=0.08, tp=0.15),
    "sl7": dict(mode="sl_tp", hold=20, sl=0.07, tp=0.15),
    "sl8only": dict(mode="sl_tp", hold=20, sl=0.08, tp=None),
    "tp15only": dict(mode="sl_tp", hold=20, sl=None, tp=0.15),
    "trail8": dict(mode="trail", hold=20, sl=0.08, trail=0.07),
}
SUB_EXITS = {"hold20": EXITS["hold20"], "hold10": EXITS["hold10"], "sl8": EXITS["sl8"]}

UNI = None
IDX = None


def init_worker(uni, idx):
    global UNI, IDX
    UNI, IDX = uni, idx


def build_universe() -> pd.DataFrame:
    u = pd.read_pickle(CACHE / "universe.pkl")
    keep = []
    for r in u.itertuples():
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


def zone_of(dates: pd.DatetimeIndex) -> np.ndarray:
    """월말 기준 거래일 위치. 0=T(월 마지막 거래일), 1=T−1, …, 8=T−8, 그 외 99."""
    pm = dates.year.to_numpy() * 12 + dates.month.to_numpy()
    last = np.flatnonzero(np.r_[pm[:-1] != pm[1:], True])
    dte = np.full(len(dates), 99, dtype=np.int16)
    for T in last:
        dte[T] = 0
        for k in range(1, 9):
            if T - k >= 0 and dte[T - k] == 99:
                dte[T - k] = k
    return dte


def window_sim(o, h, l, c, ma20, E, mode, hold, sl=None, tp=None, trail=None):
    """진입 시가 o[E] → 청산. 반환 (E_used, exit_idx, exit_px, trigger).

    trigger: 0=보유만료, 1=손절, 2=익절, 3=트레일링·MA이탈
    같은 날 손절·익절이 겹치면 손절을 먼저 적용(보수적). 갭은 시가 체결.
    """
    n = len(c)
    E = np.asarray(E, dtype=np.int64)
    E = E[(E >= 0) & (E + hold - 1 < n)]
    if E.size == 0:
        return None
    entry = o[E]
    ok = np.isfinite(entry) & (entry > 0)
    E, entry = E[ok], entry[ok]
    if E.size == 0:
        return None
    days = np.arange(hold)
    idx = E[:, None] + days[None, :]
    H, L, C = h[idx], l[idx], c[idx]
    rows = np.arange(E.size)
    exit_idx = E + hold - 1
    exit_px = C[:, -1].astype(float).copy()
    trigger = np.zeros(E.size, dtype=np.int8)

    if mode == "hold":
        return E, exit_idx, exit_px, trigger

    ds = np.full(E.size, 10 ** 6)
    stop_px = None
    if sl is not None:
        stop_px = entry * (1 - sl)
        hit_s = L <= stop_px[:, None]
        ds = np.where(hit_s.any(1), hit_s.argmax(1), 10 ** 6)
    dt = np.full(E.size, 10 ** 6)
    tp_px = None
    if tp is not None:
        tp_px = entry * (1 + tp)
        hit_t = H >= tp_px[:, None]
        dt = np.where(hit_t.any(1), hit_t.argmax(1), 10 ** 6)
    if trail is not None:
        runmax = np.maximum.accumulate(C.astype(float), axis=1)
        ma = ma20[idx]
        tr = (C <= runmax * (1 - trail)) | (np.isfinite(ma) & (C < ma))
        dt = np.where(tr.any(1), tr.argmax(1), 10 ** 6)

    take_s = (ds < 10 ** 6) & (ds <= dt)
    if take_s.any():
        d = ds[take_s]
        r = rows[take_s]
        opx = o[idx[r, d]]
        exit_px[take_s] = np.minimum(opx, stop_px[take_s])
        exit_idx[take_s] = idx[r, d]
        trigger[take_s] = 1
    take_t = (~take_s) & (dt < 10 ** 6)
    if take_t.any():
        d = dt[take_t]
        r = rows[take_t]
        opx = o[idx[r, d]]
        if tp_px is not None:
            exit_px[take_t] = np.maximum(opx, tp_px[take_t])
            trigger[take_t] = 2
        else:
            exit_px[take_t] = C[r, d]
            trigger[take_t] = 3
        exit_idx[take_t] = idx[r, d]
    return E, exit_idx, exit_px, trigger


def add_variant(acc, key, net, years, kos_exc=None):
    acc_add(acc, key, net, years)
    if kos_exc is not None:
        acc_add(acc, f"{key}|kos", kos_exc, years)


def run_exits(o, h, l, c, ma20, kos_c, kos_o, E, dates, acc, prefix,
              samples=None, kosx=True, exits=None, periods=False, counts=False):
    """E(진입일 인덱스)에 대해 청산 규칙별 순수익을 acc에 누적한다."""
    if len(E) == 0:
        return
    yrs_all = dates.year.to_numpy()
    for exname, kw in (exits or EXITS).items():
        res = window_sim(o, h, l, c, ma20, E, **kw)
        if res is None:
            continue
        Eu, xi, xp, trig = res
        entry_px = o[Eu]
        gross = xp / entry_px - 1
        tax = np.array([TAX_BY_YEAR.get(int(dates[i].year), 0.0020) for i in xi])
        net = (gross - (2 * SLIP + 2 * COMM + tax)).astype(np.float32)
        yrs_abs = yrs_all[Eu].astype(np.int16)
        yrs = (yrs_abs - YEARS[0]).astype(np.int16)   # acc_add는 0-based 연도 인덱스를 받는다
        kex = net - (kos_c[xi] / kos_o[Eu] - 1) if kosx else None
        add_variant(acc, f"{prefix}|{exname}", net, yrs, kex)
        if counts and kw["mode"] != "hold":
            for tv in (1, 2, 3):
                k = int((trig == tv).sum())
                if k:
                    acc_add(acc, f"{prefix}|{exname}|T{tv}", np.ones(k, np.float32), yrs[trig == tv])
        if periods:
            for pname, pset in (("p1", P1), ("p2", P2)):
                pm = np.isin(yrs_abs, list(pset))
                if pm.any():
                    add_variant(acc, f"{prefix}|{exname}|{pname}", net[pm], yrs[pm],
                                None if kex is None else kex[pm])
        if samples is not None and len(samples) < 400 and exname in ("hold20", "sl8", "trail8"):
            fin = np.flatnonzero(np.isfinite(net))
            for j in fin[:2]:
                samples.append({
                    "variant": f"{prefix}|{exname}",
                    "entry": str(dates[Eu[j]].date()), "entry_px": float(entry_px[j]),
                    "exit": str(dates[xi[j]].date()), "exit_px": float(xp[j]),
                    "trigger": int(trig[j]), "gross": float(gross[j]), "net": float(net[j]),
                    "tax": float(tax[j]),
                })


def dedup_first(sig_idx, dates) -> np.ndarray:
    """같은 (종목, 월) 안에서 첫 신호만 남긴다(중복 진입 제거)."""
    if sig_idx.size == 0:
        return sig_idx
    pm = dates.year.to_numpy() * 12 + dates.month.to_numpy()
    keep = [int(sig_idx[0])]
    for i in sig_idx[1:]:
        if pm[i] != pm[keep[-1]]:
            keep.append(int(i))
    return np.array(keep, dtype=np.int64)


def work_one(code: str):
    p = OHLCV / f"{code}.pkl"
    if not p.exists():
        return None
    d = pd.read_pickle(p)
    if d.empty or len(d) < 300:
        return None
    x = prep(d, float(UNI.loc[code, "shares"]), IDX)
    n = len(x)
    dates = x.index
    c = x["c"].to_numpy(float)
    o = x["o"].to_numpy(float)
    h = x["h"].to_numpy(float)
    l = x["l"].to_numpy(float)
    v = d["volume"].to_numpy(float)
    tf = d["turnover"].to_numpy(float)
    ma20 = x["ma20"].to_numpy(float)
    uni_ok = ((x["mktcap"].to_numpy(float) >= MIN_MKTCAP)
              & (x["t20"].to_numpy(float) >= MIN_VALUE)
              & (x["vol0"].to_numpy(bool)))
    kos_c = IDX["close_k"].reindex(dates).ffill().to_numpy(float)
    kos_o = IDX["open_k"].reindex(dates).ffill().to_numpy(float)
    kos_ma = IDX["close_k"].reindex(dates).ffill().rolling(20).mean().to_numpy(float)
    bull = np.isfinite(kos_ma) & (kos_c > kos_ma)

    acc: dict = {}
    samples: list = []
    dte = zone_of(dates)
    zmid = (dte >= 2) & (dte <= 8)
    zend = (dte <= 1)
    zoth = (dte >= 9)

    # ---------- 무작위 기준선 (같은 유니버스 모든 날) ----------
    base = np.flatnonzero(uni_ok)
    run_exits(o, h, l, c, ma20, kos_c, kos_o, base, dates, acc, "RAND", kosx=False, periods=True)

    # ---------- 검증 1: 후보 A ----------
    d52w = x["d52w"].to_numpy(float)
    t520 = x["t520"].to_numpy(float)
    sigA = np.flatnonzero(uni_ok & np.isfinite(d52w) & (d52w > -0.05) & np.isfinite(t520) & (t520 >= 2.0))
    entA = sigA + 5
    entA = entA[entA < n]
    run_exits(o, h, l, c, ma20, kos_c, kos_o, entA, dates, acc, "A", samples=samples,
              periods=True, counts=True)
    for zname, zmask in (("zend", zend), ("zmid", zmid), ("zoth", zoth)):
        run_exits(o, h, l, c, ma20, kos_c, kos_o, entA[zmask[entA]], dates, acc, f"A|{zname}",
                  periods=True, counts=True)
    run_exits(o, h, l, c, ma20, kos_c, kos_o, entA[bull[entA]], dates, acc, "A|bull",
              periods=True, counts=True)
    run_exits(o, h, l, c, ma20, kos_c, kos_o, entA[~bull[entA]], dates, acc, "A|bear", counts=True)

    # ---------- 검증 2: 월 중순 거래량 선행 ----------
    v20 = pd.Series(v, index=dates).rolling(20).mean().to_numpy(float)
    vmult = v / v20
    tmult = tf / x["t20"].to_numpy(float)
    above20 = np.isfinite(ma20) & (c > ma20)
    near52 = np.isfinite(d52w) & (d52w > -0.05)
    conds = {"a": near52, "b": above20, "c": np.ones(n, bool)}
    zones = {"mid": zmid, "end": zend, "oth": zoth}

    for mname, marr in (("vol", vmult), ("amt", tmult)):
        for thr in (2.0, 3.0):
            base_sig = uni_ok & np.isfinite(marr) & (marr >= thr)
            for ckey, cmask in conds.items():
                sig = base_sig & cmask
                for zname, zmask in zones.items():
                    sd = np.flatnonzero(sig & zmask)
                    if sd.size == 0:
                        continue
                    for k in (1, 3, 5):
                        ent = sd + k
                        ent = ent[ent < n]
                        pre = f"M|{mname}{int(thr)}|{ckey}|{zname}|e{k}"
                        run_exits(o, h, l, c, ma20, kos_c, kos_o, ent, dates, acc, pre,
                                  exits=SUB_EXITS, periods=(zname != "oth"))
                    if zname == "mid":
                        dd = dedup_first(sd, dates)
                        for k in (1, 3, 5):
                            ent = dd + k
                            ent = ent[ent < n]
                            pre = f"MD|{mname}{int(thr)}|{ckey}|e{k}"
                            run_exits(o, h, l, c, ma20, kos_c, kos_o, ent, dates, acc, pre,
                                      exits=SUB_EXITS, periods=True)
    return acc, samples


def stage_collect(args):
    uni = build_universe()
    idxdf = pd.read_pickle(CACHE / "index.pkl")
    codes = uni.index.tolist()
    if args.limit:
        codes = codes[: args.limit]
    print(f"[universe] {len(uni)}종목, 실행 {len(codes)}", flush=True)
    acc_all: dict = {}
    samples: list = []
    t0 = time.time()
    done = 0
    with ProcessPoolExecutor(max_workers=args.workers, initializer=init_worker,
                             initargs=(uni, idxdf)) as ex:
        pending = {ex.submit(work_one, c): c for c in codes[: args.workers * 2]}
        i = args.workers * 2
        while pending:
            import concurrent.futures as cf
            fin, _ = cf.wait(list(pending), return_when=cf.FIRST_COMPLETED)
            for f in fin:
                code = pending.pop(f)
                done += 1
                try:
                    res = f.result()
                except Exception as e:  # noqa: BLE001
                    print(f"[fail] {code}: {e}", flush=True)
                    res = None
                if res is not None:
                    a, sm = res
                    merge_acc(acc_all, a)
                    if len(samples) < 400:
                        samples.extend(sm[: max(0, 400 - len(samples))])
                if i < len(codes):
                    pending[ex.submit(work_one, codes[i])] = codes[i]
                    i += 1
                if done % 200 == 0:
                    print(f"[collect] {done}/{len(codes)}  keys={len(acc_all)}  {time.time()-t0:.0f}s",
                          flush=True)
    ACC_PKL.parent.mkdir(parents=True, exist_ok=True)
    with open(ACC_PKL, "wb") as fh:
        pickle.dump({"acc": acc_all, "samples": samples, "uni": len(uni), "tickers": done}, fh)
    print(f"[done] {ACC_PKL}  keys={len(acc_all)}  {time.time()-t0:.0f}s", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="collect", choices=["collect", "report"])
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    if args.stage == "collect":
        stage_collect(args)
    else:
        from build_ca_tables import build
        build(ACC_PKL, OUT_JSON, OUT_TABLES)


if __name__ == "__main__":
    main()
