#!/usr/bin/env python3
"""월봉 +20% 급등 종목만 — 직전 8주(40거래일) 안에서 거래량이 최고로 터진 날(V_max)은
언제, 어떤 모양으로 나오고, 그 뒤 T(급등 달 직전 마지막 거래일)까지 어떻게 흘러 급등으로
이어지는지를 분석한다.

산출물
  work/backtest/surge_8w_cases.pkl        사례별 V_max 특징 + 흐름 유형
  stock-insight/data/surge-8w-pattern.json 요약표(보고서·차트용)
  work/backtest/surge_8w_rules.json       전 종목 규칙 검증 결과
  downloads/surge-8w-vmax-flow.png        V_max 시점 분포 + 전후 경로/거래량
  downloads/surge-8w-case-{1..4}.png      유형별 대표 사례 차트

사용법
  python3 scripts/surge_8w_peak_volume.py build --workers 4
  python3 scripts/surge_8w_peak_volume.py rules --workers 6
  python3 scripts/surge_8w_peak_volume.py charts
  python3 scripts/surge_8w_peak_volume.py verify
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import warnings
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
try:
    import surge_only_pattern as sp
except Exception:  # 스크립트 단독 실행 대비
    sp = None

REPO = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("BACKTEST_CACHE", REPO.parent / "work" / "backtest" / "cache"))
OHLCV = CACHE / "ohlcv"
WORK = REPO.parent / "work" / "backtest"
DL = REPO.parent / "downloads"
CASES_IN = WORK / "surge_only_cases.pkl"
OUT_CASES = WORK / "surge_8w_cases.pkl"
OUT_JSON = REPO / "data" / "surge-8w-pattern.json"
RULES_JSON = WORK / "surge_8w_rules.json"

W8 = 40                      # 8주 = 40거래일
HOLDS = (20, 30)             # 검증 보유기간
SPLITS = {"learn": (2016, 2020), "valid": (2021, 2026)}
MIN_MKTCAP = 50_000_000_000
MIN_VALUE = 10_000_000_000
SLIP = 0.001
COMM = 0.00015
TAX_BY_YEAR = {2015: 0.0030, 2016: 0.0030, 2017: 0.0030, 2018: 0.0030, 2019: 0.0030,
               2020: 0.0025, 2021: 0.0023, 2022: 0.0023, 2023: 0.0020, 2024: 0.0018,
               2025: 0.0015, 2026: 0.0020}

TYPES = [
    "T1_막판 폭발(1주 전)",
    "T2_투매성 음봉(MA60 아래)",
    "T3_신고가 돌파형",
    "T4_초기 폭발→눌림·마름",
    "T5_중기 V_max→재돌파",
    "T6_중기 V_max→박스 획보",
]
TYPES[5] = "T6_중기 V_max→박스 횡보"


# ------------------------------------------------------------------ 공통
def prep(d: pd.DataFrame) -> dict:
    c = d["aclose"].to_numpy(float)
    o = d["aopen"].to_numpy(float)
    h = d["ahigh"].to_numpy(float)
    l = d["alow"].to_numpy(float)
    v = d["volume"].to_numpy(float)
    tf = d["turnover"].to_numpy(float)
    n = c.size
    ma = {k: pd.Series(c).rolling(k).mean().to_numpy(float) for k in (5, 20, 60, 120, 250)}
    return dict(c=c, o=o, h=h, l=l, v=v, tf=tf, n=n, ma=ma,
                v20=pd.Series(v).rolling(20).mean().to_numpy(float),
                v10=pd.Series(v).rolling(10).mean().to_numpy(float),
                v5=pd.Series(v).rolling(5).mean().to_numpy(float),
                t20=pd.Series(tf).rolling(20).mean().to_numpy(float),
                ret1=np.r_[np.nan, c[1:] / c[:-1] - 1])


def cost_arr(dates) -> np.ndarray:
    return np.array([2 * SLIP + 2 * COMM + TAX_BY_YEAR.get(int(y), 0.0020) for y in dates.year])


def week_no(off: int) -> int:
    """off = V_max - T (음수). 8 = 1주 전 … 1 = 8주 전."""
    return 8 - ((-off - 1) // 5)


# ------------------------------------------------------------------ 1) 사례 특징
def case_features(p: dict, T: int) -> dict:
    c, o, h, l, v, tf = p["c"], p["o"], p["h"], p["l"], p["v"], p["tf"]
    W0 = T - W8
    vw = v[W0:T]
    imax = int(np.argmax(vw)) + W0
    tmax = int(np.argmax(tf[W0:T])) + W0
    f: dict = {}
    f["imax"] = imax
    f["vmax_off"] = imax - T
    f["vmax_week"] = week_no(imax - T)
    f["tmax_week"] = week_no(tmax - T)
    f["vmax_same_tmax"] = bool(imax == tmax)
    f["vmax"] = float(v[imax])
    f["vmax_x20"] = float(v[imax] / p["v20"][imax]) if p["v20"][imax] > 0 else np.nan
    f["tmax_x20"] = float(tf[imax] / p["t20"][imax]) if p["t20"][imax] > 0 else np.nan
    f["vmax_turnover_eok"] = float(tf[imax] / 1e8)
    f["vmax_ret1"] = float(c[imax] / c[imax - 1] - 1) if imax > 0 else np.nan
    f["vmax_gap"] = float(o[imax] / h[imax - 1] - 1) if imax > 0 else np.nan
    rng = h[imax] - l[imax]
    body = (c[imax] - o[imax]) / rng if rng > 0 else 0.0
    upsh = (h[imax] - max(c[imax], o[imax])) / rng if rng > 0 else 0.0
    f["body"] = float(body)
    f["upsh"] = float(upsh)
    f["close_pos"] = float((c[imax] - l[imax]) / rng) if rng > 0 else 0.5
    if f["vmax_ret1"] >= 0.05 and body >= 0.5:
        cnd = "장대양봉"
    elif f["vmax_ret1"] <= -0.05 and body <= -0.3:
        cnd = "장대음봉"
    elif upsh >= 0.45 and c[imax] >= o[imax]:
        cnd = "윗꼬리 양봉"
    elif c[imax] < o[imax] and body <= -0.4:
        cnd = "음봉"
    elif c[imax] > o[imax]:
        cnd = "양봉"
    else:
        cnd = "보합"
    f["candle"] = cnd
    for k in (20, 60, 120):
        m = p["ma"][k][imax]
        mp = p["ma"][k][imax - 1]
        f[f"vm_disp{k}"] = float(c[imax] / m - 1) if np.isfinite(m) and m > 0 else np.nan
        f[f"vm_above{k}"] = bool(np.isfinite(m) and c[imax] > m)
        f[f"vm_br{k}"] = bool(np.isfinite(m) and np.isfinite(mp) and c[imax] > m and c[imax - 1] <= mp)
    hi250 = np.nanmax(h[max(0, imax - 249):imax + 1])
    hi60 = np.nanmax(h[max(0, imax - 59):imax + 1])
    hi20 = np.nanmax(h[max(0, imax - 19):imax + 1])
    f["vm_d52"] = float(c[imax] / hi250 - 1)
    f["vm_new250"] = bool(h[imax] >= hi250 - 1e-12)
    f["vm_new60"] = bool(h[imax] >= hi60 - 1e-12)
    f["vm_new20"] = bool(h[imax] >= hi20 - 1e-12)
    m60 = p["ma"][60]
    f["vm_sl60"] = float(m60[imax] / m60[imax - 20] - 1) if imax >= 20 and np.isfinite(m60[imax - 20]) else np.nan
    f["align3"] = bool(np.isfinite(m60[imax]) and p["ma"][5][imax] > p["ma"][20][imax] > m60[imax])

    # ---- V_max 이후 ~ T 까지
    days = T - 1 - imax
    f["days_after"] = days
    vseg = v[imax + 1:T]
    f["vm_vol_shrink"] = float(np.nanmean(vseg) / v[imax]) if vseg.size and v[imax] > 0 else np.nan
    f["vm_vol_shrink5"] = float(p["v5"][T] / v[imax]) if v[imax] > 0 else np.nan
    f["vm_vol_shrink10"] = float(p["v10"][T] / v[imax]) if v[imax] > 0 else np.nan
    f["v_x20_at_T"] = float(v[T] / p["v20"][T]) if p["v20"][T] > 0 else np.nan
    f["T_disp20"] = float(c[T] / p["ma"][20][T] - 1) if np.isfinite(p["ma"][20][T]) else np.nan
    f["T_disp60"] = float(c[T] / p["ma"][60][T] - 1) if np.isfinite(p["ma"][60][T]) else np.nan
    f["T_vs_vmhigh"] = float(c[T] / h[imax] - 1)
    f["T_above20"] = bool(c[T] > p["ma"][20][T])
    f["T_above60"] = bool(c[T] > p["ma"][60][T])
    if days >= 1:
        lows = l[imax + 1:T + 1]
        f["pull_from_high"] = float(np.nanmin(lows) / h[imax] - 1)
        f["pull_from_close"] = float(np.nanmin(lows) / c[imax] - 1)
        f["pull_day"] = int(np.nanargmin(lows)) + 1
        highs = h[imax + 1:T + 1]
        f["rebreak"] = bool(np.nanmax(highs) > h[imax])
        f["rebreak_day"] = int(np.nanargmax(highs)) + 1 if f["rebreak"] else None
        f["after_ret"] = float(c[T] / c[imax] - 1)
        for k in (20, 60):
            m = p["ma"][k][imax + 1:T + 1]
            okm = np.isfinite(m)
            below = (c[imax + 1:T + 1] < m) & okm
            f[f"days_below{k}"] = int(below.sum())
            f[f"frac_below{k}"] = float(below.sum() / max(1, okm.sum()))
            f[f"exited{k}"] = bool(below.any())
    else:
        f.update(pull_from_high=0.0, pull_from_close=0.0, pull_day=0, rebreak=False,
                 rebreak_day=None, after_ret=0.0, days_below20=0, days_below60=0,
                 frac_below20=0.0, frac_below60=0.0, exited20=False, exited60=False)
    # 창 수익(참고)
    f["win_ret8"] = float(c[T] / c[W0] - 1)
    f["vm_is_win_high"] = bool(h[imax] >= np.nanmax(h[W0:T]) - 1e-12)
    f["vm_close_is_win_high"] = bool(c[imax] >= np.nanmax(c[W0:T]) - 1e-12)
    # V_max 기준 경로(가격·거래량, V_max=100) — 오프셋 -20 ~ +40
    pc, pv = [], []
    for off2 in range(-20, 41):
        j = imax + off2
        if 0 <= j < p["n"]:
            pc.append(float(c[j] / c[imax] * 100))
            pv.append(float(v[j] / v[imax] * 100) if v[imax] > 0 else np.nan)
        else:
            pc.append(np.nan); pv.append(np.nan)
    f["path_vm"] = pc
    f["volpath_vm"] = pv
    return f


def flow_type(f: dict) -> str:
    if f["vmax_week"] == 8:
        return "T1_막판 폭발(1주 전)"
    if np.isfinite(f["vmax_ret1"]) and f["vmax_ret1"] <= -0.03 and not f["vm_above60"]:
        return "T2_투매성 음봉(MA60 아래)"
    if np.isfinite(f["vmax_ret1"]) and f["vmax_ret1"] >= 0.03 and f["vm_new250"]:
        return "T3_신고가 돌파형"
    if f["vmax_week"] <= 3:
        return "T4_초기 폭발→눌림·마름"
    if f["rebreak"]:
        return "T5_중기 V_max→재돌파"
    return "T6_중기 V_max→박스 횡보"


def build_rows_for_code(code: str, cases: pd.DataFrame):
    d = pd.read_pickle(OHLCV / f"{code}.pkl")
    p = prep(d)
    out = []
    for r in cases.itertuples():
        T = int(r.T)
        f = case_features(p, T)
        f["row"] = r.Index
        out.append(f)
    return out


def _worker_build(args):
    code, grp = args
    try:
        return build_rows_for_code(code, grp)
    except Exception as e:  # noqa: BLE001
        print("[fail]", code, e)
        return []


def cmd_build(args):
    cases = pd.read_pickle(CASES_IN)
    jobs = [(code, g) for code, g in cases.groupby("code")]
    rows = []
    if args.workers <= 1:
        for j in jobs:
            rows.extend(_worker_build(j))
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            for i, res in enumerate(ex.map(_worker_build, jobs, chunksize=8)):
                rows.extend(res)
                if (i + 1) % 200 == 0:
                    print(f"[build] {i+1}/{len(jobs)}", flush=True)
    vm = pd.DataFrame(rows).set_index("row")
    vm = vm.rename(columns={"align3": "vm_align3"})
    df = cases.join(vm.drop(columns=[c for c in ("T", "code") if c in vm.columns]))
    df["ftype"] = [flow_type(r) for r in df.to_dict("records")]
    df.to_pickle(OUT_CASES)
    print(f"[build] {len(df)}건 → {OUT_CASES}")
    print(df["vmax_week"].value_counts().sort_index().to_string())
    print(df["ftype"].value_counts().to_string())
    return df


# ------------------------------------------------------------------ 2) 전 종목 규칙 검증
def panel_arrays(p: dict) -> dict:
    """신호일 i 마다 '최근 40거래일(i-40~i-1) V_max' 정보를 벡터로 만든다."""
    c, o, h, l, v = p["c"], p["o"], p["h"], p["l"], p["v"]
    n = p["n"]
    if n < W8 + 60:
        return None
    sw = np.lib.stride_tricks.sliding_window_view(v, W8)      # sw[k] = v[k:k+W8]
    starts = np.arange(0, n - W8)
    i = starts + W8                                           # 신호일(종가) — 창은 i-40 ~ i-1 = sw[i-40]
    win = sw[starts]
    imax = (i - W8) + np.argmax(win, axis=1)
    days = (i - 1) - imax
    out = dict(i=i, imax=imax, days=days)
    vmax = v[imax]
    out["vm_x20"] = np.where(p["v20"][imax] > 0, vmax / p["v20"][imax], np.nan)
    out["vm_ret1"] = p["ret1"][imax]
    out["vm_above60"] = c[imax] > p["ma"][60][imax]
    out["vm_above20"] = c[imax] > p["ma"][20][imax]
    out["vm_close"] = c[imax]
    out["vm_high"] = h[imax]
    hi250 = pd.Series(h).rolling(250).max().to_numpy()
    out["vm_new250"] = h[imax] >= hi250[imax]
    # V_max 이후 (imax ~ i-1) 최저가·최고가 — d별 rolling으로 O(1) 조회
    rmins = np.vstack([pd.Series(l).rolling(d0 + 1).min().shift(1).to_numpy() for d0 in range(W8)])
    rmaxs = np.vstack([pd.Series(h).rolling(d0 + 1).max().shift(1).to_numpy() for d0 in range(W8)])
    dd = np.clip(days, 0, W8 - 1)
    out["pull"] = rmins[dd, i] / h[imax] - 1
    out["post_high"] = rmaxs[dd, i]
    out["v5_ratio"] = np.where(vmax > 0, p["v5"][i] / vmax, np.nan)
    out["v10_ratio"] = np.where(vmax > 0, p["v10"][i] / vmax, np.nan)
    out["above20"] = c[i] > p["ma"][20][i]
    out["above60"] = c[i] > p["ma"][60][i]
    out["disp20"] = np.where(np.isfinite(p["ma"][20][i]), c[i] / p["ma"][20][i] - 1, np.nan)
    out["ret1"] = p["ret1"][i]
    # 5일 안에 종가가 MA20을 상향 돌파한 적
    cur = (c > p["ma"][20]) & np.isfinite(p["ma"][20])
    prev = np.r_[False, cur[:-1]]
    new = cur & ~prev
    out["brk20_5"] = (pd.Series(new.astype(float)).rolling(5, min_periods=1).max().to_numpy() > 0)[i]
    # 20일 평균 대비 거래량(오늘)
    out["v_x20"] = np.where(p["v20"][i] > 0, v[i] / p["v20"][i], np.nan)
    return out


def rule_masks(a: dict) -> dict:
    d = a["days"]
    vm3 = a["vm_x20"] >= 3.0
    vm2 = a["vm_x20"] >= 2.0
    up = a["vm_ret1"] >= 0.03
    dry30 = a["v5_ratio"] <= 0.30
    dri40 = a["v5_ratio"] <= 0.40
    near20 = a["disp20"] >= -0.02
    mild20 = a["disp20"] <= 0.06
    pull = (a["pull"] <= -0.05) & (a["pull"] >= -0.30)
    rebreak = a["vm_close"] > a["vm_high"]
    R = {}
    R["R1_마름30+MA20지지"] = vm3 & up & (d >= 5) & (d <= 25) & dry30 & a["above20"] & near20 & mild20
    R["R1b_마름40+MA20지지"] = vm3 & up & (d >= 5) & (d <= 25) & dri40 & a["above20"] & near20 & mild20
    R["R1c_2배+MA20지지"] = vm2 & up & (d >= 5) & (d <= 25) & dry30 & a["above20"] & near20 & mild20
    R["R2_V_max고가재돌파"] = vm3 & up & (d >= 3) & (d <= 30) & rebreak & a["above20"] & (a["disp20"] <= 0.10)
    R["R3_막판폭발_3일내"] = (d <= 3) & vm3 & up & a["above20"]
    R["R4_초기폭발_눌림후지지"] = (d >= 20) & (d <= 40) & vm3 & pull & a["above20"] & a["above60"] & (a["ret1"] > 0)
    R["R6_신고가V_max_눌림지지"] = vm3 & a["vm_new250"] & (a["vm_ret1"] >= 0.03) & (d >= 5) & (d <= 25) \
        & (a["v5_ratio"] <= 0.50) & a["above20"] & (a["disp20"] >= -0.02) & (a["disp20"] <= 0.08)
    R["R5_투매후MA20회복"] = (a["vm_ret1"] <= -0.03) & (~a["vm_above60"]) & (d >= 5) & (d <= 30) \
        & a["above20"] & a["brk20_5"]
    return R


def fwd_ret(c, cost, idx, hold):
    ok = idx + hold - 1 < c.size
    out = np.full(idx.size, np.nan)
    ee = idx[ok]
    out[ok] = c[ee + hold - 1] / c[ee] - 1 - cost[ee]
    return out


KS = None
UNI = None


def init_worker(uni, ks):
    global UNI, KS
    UNI = uni
    KS = ks


def run_one(code: str):
    path = OHLCV / f"{code}.pkl"
    if not path.exists():
        return None
    d = pd.read_pickle(path)
    if d.empty or len(d) < 320:
        return None
    row = UNI.loc[code]
    p = prep(d)
    a = panel_arrays(p)
    if a is None:
        return None
    n = p["n"]
    dates = d.index
    mktcap = d["close"].to_numpy(float) * float(row["shares"])
    base_ok = (mktcap >= MIN_MKTCAP) & (p["t20"] >= MIN_VALUE) & (p["v"] > 0) & np.isfinite(p["ma"][250])
    cost = cost_arr(dates)
    years = dates.year.to_numpy()
    i = a["i"]
    ok0 = base_ok[i]
    if not ok0.any():
        return None
    kk = KS["close_k"].reindex(dates).ffill().to_numpy(float)
    masks = rule_masks(a)
    masks["BASE_전종목"] = np.ones(i.size, bool)
    out: dict[str, list] = {}
    out_idx = np.flatnonzero(ok0 & (i >= 0))
    for name, m in masks.items():
        sel = out_idx if name != "BASE_전종목" else out_idx[::5]
        sel = sel[m[sel]]
        if sel.size == 0:
            continue
        ii = i[sel]
        for hold in HOLDS:
            r = fwd_ret(p["c"], cost, ii, hold)
            ok = np.isfinite(r)
            if not ok.any():
                continue
            jj = ii[ok]
            rr = r[ok]
            kkok = np.isfinite(kk[jj + hold - 1]) & np.isfinite(kk[jj])
            ex = np.full(rr.size, np.nan)
            ex[kkok] = rr[kkok] - (kk[jj[kkok] + hold - 1] / kk[jj[kkok]] - 1)
            for sp_name, (y0, y1) in SPLITS.items():
                sm = (years[jj] >= y0) & (years[jj] <= y1)
                if sm.any():
                    out.setdefault(f"{name}|h{hold}|{sp_name}", []).append(
                        np.c_[rr[sm], ex[sm], years[jj][sm]].astype(np.float32))
    return {k: np.vstack(v) for k, v in out.items()}


def stats(r: np.ndarray) -> dict:
    r = r[np.isfinite(r)]
    if r.size == 0:
        return {"n": 0}
    sd = float(r.std(ddof=1)) if r.size > 1 else None
    return {"n": int(r.size), "mean": float(r.mean()), "median": float(np.median(r)),
            "win": float((r > 0).mean()), "top10": float(np.percentile(r, 90)),
            "bot10": float(np.percentile(r, 10)), "sd": sd,
            "t": float(r.mean() / sd * np.sqrt(r.size)) if sd else None}


def cmd_rules(args):
    global UNI, KS
    if sp is None:
        raise SystemExit("surge_only_pattern.py 필요")
    uni = sp.load_universe()
    ks = pd.read_pickle(CACHE / "index.pkl")
    UNI, KS = uni, ks
    codes = uni.index.tolist()
    agg: dict[str, list] = {}
    with ProcessPoolExecutor(max_workers=args.workers, initializer=init_worker,
                             initargs=(uni, ks)) as ex:
        for k, res in enumerate(ex.map(run_one, codes, chunksize=10)):
            if not res:
                continue
            for key, arr in res.items():
                agg.setdefault(key, []).append(arr)
            if (k + 1) % 400 == 0:
                print(f"[rules] {k+1}/{len(codes)}", flush=True)
    out: dict = {}
    for key, arrs in agg.items():
        name, hold, split = key.split("|")
        A = np.vstack(arrs)
        st = stats(A[:, 0])
        st["excess_mean"] = float(np.nanmean(A[:, 1]))
        st["excess_win"] = float(np.nanmean(A[:, 1] > 0)) if np.isfinite(A[:, 1]).any() else None
        st["excess_median"] = float(np.nanmedian(A[:, 1]))
        st["years"] = {int(y): int(c) for y, c in zip(*np.unique(A[:, 2], return_counts=True))}
        st["year_mean"] = {int(y): round(float(A[A[:, 2] == y, 0].mean()) * 100, 2)
                           for y in np.unique(A[:, 2])}
        out.setdefault(name, {}).setdefault(hold, {})[split] = st
    with open(RULES_JSON, "w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    rows = []
    for name, hv in out.items():
        for hold, spv in hv.items():
            l, v = spv.get("learn", {}), spv.get("valid", {})
            rows.append([name, hold, l.get("n"), l.get("mean"), l.get("median"),
                         v.get("n"), v.get("mean"), v.get("median"), v.get("win"),
                         v.get("excess_mean"), v.get("excess_median"), v.get("t")])
    df = pd.DataFrame(rows, columns=["rule", "hold", "n_l", "mean_l", "med_l", "n_v",
                                     "mean_v", "med_v", "win_v", "ex_v", "exmed_v", "t_v"])
    pd.set_option("display.width", 250)
    print(df.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    print("→", RULES_JSON)


# ------------------------------------------------------------------ 3) 요약표

def pick_rep(g: pd.DataFrame) -> pd.DataFrame:
    """유형 대표 사례: 시총 3,000억↑ · 급등폭 25~40% · 유형 중앙값에 가까운 순."""
    cand = g[g["mktcap"] >= 3e11]
    if cand.empty:
        cand = g
    narrow = cand[(cand["ret"] >= 0.25) & (cand["ret"] <= 0.40)]
    if not narrow.empty:
        cand = narrow
    cand = cand.copy()
    cand["_d"] = ((cand["ret"] - 0.31).abs() * 2
                  + (cand["vmax_x20"] - g["vmax_x20"].median()).abs() / 5
                  + (cand["days_after"] - g["days_after"].median()).abs() / 80
                  + (cand["win_ret8"] - g["win_ret8"].median()).abs() * 1.5)
    return cand.nsmallest(2, "_d")

def pct(x, d=1):
    return None if x is None or not np.isfinite(x) else round(float(x) * 100, d)


def summarize(df: pd.DataFrame) -> dict:
    df = df.copy()
    df["neg_candle"] = df["vmax_ret1"].apply(lambda r: bool(np.isfinite(r) and r < 0))
    df["bigyang"] = df["candle"].eq("장대양봉")
    df["upshadow"] = df["candle"].eq("윗꼬리 양봉")
    df["yang"] = df["vmax_ret1"].apply(lambda r: bool(np.isfinite(r) and r > 0))
    out: dict = {}
    out["cases"] = int(len(df))
    out["codes"] = int(df["code"].nunique())
    out["period"] = [str(df["M"].min()), str(df["M"].max())]
    out["surge"] = {"mean": pct(df["ret"].mean()), "median": pct(df["ret"].median()),
                    "min": pct(df["ret"].min()), "max": pct(df["ret"].max())}
    out["vmax_x20_median"] = round(float(df["vmax_x20"].median()), 2)
    out["same_tmax_share"] = pct(df["vmax_same_tmax"].mean())
    out["vm_is_win_high"] = pct(df["vm_is_win_high"].mean())
    out["vm_close_is_win_high"] = pct(df["vm_close_is_win_high"].mean())
    out["turnover_median_eok"] = round(float(df["vmax_turnover_eok"].median()), 1)

    # (1) 주별 분포
    weeks = []
    for w in range(1, 9):
        g = df[df["vmax_week"] == w]
        weeks.append({
            "week": w, "label": f"{9-w}주 전",
            "n": int(len(g)), "share": pct(len(g) / len(df)),
            "x20": round(float(g["vmax_x20"].median()), 2),
            "ret1": pct(g["vmax_ret1"].median()),
            "bigyang": pct(g["bigyang"].mean()), "upshadow": pct(g["upshadow"].mean()),
            "neg": pct(g["neg_candle"].mean()),
            "above60": pct(g["vm_above60"].mean()), "new250": pct(g["vm_new250"].mean()),
            "shrink5": pct(g["vm_vol_shrink5"].median()),
            "pull": pct(g["pull_from_high"].median()),
            "rebreak": pct(g["rebreak"].mean()),
            "T_disp20": pct(g["T_disp20"].median()),
            "days_after": float(g["days_after"].median()),
        })
    out["weeks"] = weeks

    # (2) V_max 당일 성격
    out["candle"] = [{"name": k, "n": int(v), "share": pct(v / len(df))}
                     for k, v in df["candle"].value_counts().items()]
    out["vmax_day"] = {
        "above20": pct(df["vm_above20"].mean()), "above60": pct(df["vm_above60"].mean()),
        "above120": pct(df["vm_above120"].mean()),
        "br20": pct(df["vm_br20"].mean()), "br60": pct(df["vm_br60"].mean()),
        "br120": pct(df["vm_br120"].mean()),
        "new20": pct(df["vm_new20"].mean()), "new60": pct(df["vm_new60"].mean()),
        "new250": pct(df["vm_new250"].mean()),
        "d52_median": pct(df["vm_d52"].median()),
        "close_pos_median": pct(df["close_pos"].median()),
        "gap_median": pct(df["vmax_gap"].median()),
        "x20": {f"{a}~{b}": pct(((df["vmax_x20"] >= a) & (df["vmax_x20"] < b)).mean())
                for a, b in [(0, 2), (2, 3), (3, 5), (5, 10), (10, 100)]},
    }
    # 배수 x 캔들 교차
    b = pd.cut(df["vmax_x20"], [0, 2, 3, 5, 10, 100], right=False)
    out["x20_by_candle"] = {
        str(k): {c2: pct(v) for c2, v in r.items()}
        for k, r in pd.crosstab(b, df["candle"], normalize="index").iterrows()}
    out["x20_by_candle_n"] = {str(k): int(v) for k, v in b.value_counts().sort_index().items()}

    # (3) V_max 이후 흐름 (경과일 구간별)
    dbin = pd.cut(df["days_after"], [-1, 4, 9, 14, 19, 24, 29, 34, 39],
                  labels=["0~4", "5~9", "10~14", "15~19", "20~24", "25~29", "30~34", "35~39"])
    flow = []
    for k, g in df.groupby(dbin, observed=True):
        flow.append({"bin": str(k), "n": int(len(g)),
                     "shrink": pct(g["vm_vol_shrink"].median()),
                     "shrink5": pct(g["vm_vol_shrink5"].median()),
                     "pull": pct(g["pull_from_high"].median()),
                     "rebreak": pct(g["rebreak"].mean()),
                     "T_vs_vmhigh": pct(g["T_vs_vmhigh"].median()),
                     "T_disp20": pct(g["T_disp20"].median()),
                     "after_ret": pct(g["after_ret"].median()),
                     "days_below20": round(float(g["days_below20"].median()), 1),
                     "days_below60": round(float(g["days_below60"].median()), 1)})
    out["flow_by_days"] = flow
    out["pull_dist"] = {f"{a}~{b}": pct(((df["pull_from_high"] >= a) & (df["pull_from_high"] < b)).mean())
                        for a, b in [(-1, -0.3), (-0.3, -0.2), (-0.2, -0.1), (-0.1, -0.05), (-0.05, 0.01)]}
    out["pull_median"] = pct(df["pull_from_high"].median())
    out["pull_day_median"] = float(df["pull_day"].median())
    out["rebreak_share"] = pct(df["rebreak"].mean())
    out["rebreak_day_median"] = float(df.loc[df["rebreak"], "rebreak_day"].median()) if df["rebreak"].any() else None
    out["shrink5_median"] = pct(df["vm_vol_shrink5"].median())
    out["exited20_share"] = pct(df["exited20"].mean())
    out["exited60_share"] = pct(df["exited60"].mean())
    out["T_vs_vmhigh_median"] = pct(df["T_vs_vmhigh"].median())

    # (4) 흐름 유형
    types = []
    for t, g in df.groupby("ftype"):
        reps = [{"code": r.code, "name": r.name, "M": r.M, "ret": pct(r.ret),
                 "pull": pct(r.pull_from_high), "shrink5": pct(r.vm_vol_shrink5),
                 "r20": pct(r.r20)}
                for r in pick_rep(g).itertuples()]
        types.append({
            "name": t, "n": int(len(g)), "share": pct(len(g) / len(df)),
            "week": float(g["vmax_week"].median()), "days_after": float(g["days_after"].median()),
            "x20": round(float(g["vmax_x20"].median()), 2),
            "ret1": pct(g["vmax_ret1"].median()), "pull": pct(g["pull_from_high"].median()),
            "shrink5": pct(g["vm_vol_shrink5"].median()),
            "rebreak": pct(g["rebreak"].mean()), "T_vs_vmhigh": pct(g["T_vs_vmhigh"].median()),
            "T_disp20": pct(g["T_disp20"].median()),
            "r20_med": pct(g["r20"].median()), "r30_med": pct(g["r30"].median()),
            "r20_mean": pct(g["r20"].mean()), "r30_mean": pct(g["r30"].mean()),
            "reps": reps})
        types[-1]["reps"] = reps
    out["types"] = sorted(types, key=lambda x: -x["n"])
    return out


def cmd_summary(args):
    df = pd.read_pickle(OUT_CASES)
    if RULES_JSON.exists():
        with open(RULES_JSON) as fh:
            rules = json.load(fh)
    else:
        rules = {}
    out = summarize(df)
    out["rules"] = rules
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_JSON, "w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1)[:6000])
    print("→", OUT_JSON)


# ------------------------------------------------------------------ 4) 차트
YL = "#ffe98a"
PULL = "#dbe7f5"
RISE = "#fbdcdc"


def _font():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.font_manager as fm
    import matplotlib.pyplot as plt
    for f in fm.fontManager.ttflist:
        if "CJK KR" in f.name and "Sans" in f.name:
            plt.rcParams["font.family"] = f.name
            break
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.dpi"] = 130
    return plt


def flow_figure(df: pd.DataFrame, out: Path):
    plt = _font()
    from matplotlib.patches import Rectangle  # noqa: F401
    fig = plt.figure(figsize=(13.2, 8.4))
    gs = fig.add_gridspec(2, 2, hspace=0.34, wspace=0.22,
                          left=0.07, right=0.97, top=0.90, bottom=0.08)

    # (a) 주별 분포
    ax = fig.add_subplot(gs[0, 0])
    vc = df["vmax_week"].value_counts().sort_index()
    labels = [f"{9-w}주\n전" for w in vc.index]
    bars = ax.bar(range(len(vc)), vc.values, color="#5b8def", width=0.68)
    for i, (w, v) in enumerate(vc.items()):
        ax.text(i, v + 6, f"{v}\n{v/len(df)*100:.1f}%", ha="center", va="bottom", fontsize=7.6)
    ax.set_xticks(range(len(vc)))
    ax.set_xticklabels(labels, fontsize=7.6)
    ax.set_ylim(0, max(vc.values) * 1.25)
    ax.set_ylabel("건수", fontsize=8)
    ax.set_title("(a) 8주 안에서 '거래량 최고일'은 몇 주 전에 나오나", fontsize=9.5, fontweight="bold")
    ax.grid(axis="y", alpha=0.2, lw=0.4)
    ax.tick_params(labelsize=7)

    # (b) V_max 기준 중앙값 주가 경로
    offs = np.arange(-20, 41)
    P = np.vstack([np.array(x, float) for x in df["path_vm"]])
    V = np.vstack([np.array(x, float) for x in df["volpath_vm"]])
    med = np.nanmedian(P, axis=0)
    q1 = np.nanpercentile(P, 25, axis=0)
    q3 = np.nanpercentile(P, 75, axis=0)
    ax = fig.add_subplot(gs[0, 1])
    ax.fill_between(offs, q1, q3, color="#90caf9", alpha=0.35, lw=0)
    ax.plot(offs, med, color="#0d47a1", lw=1.9, label="주가 중앙값 (V_max=100)")
    ax.axvline(0, color="#c62828", lw=1.0, ls="--")
    ax.axhline(100, color="#888", lw=0.6, ls=":")
    ax.set_xlabel("V_max 기준 거래일", fontsize=8)
    ax.set_ylabel("주가 (V_max 종가=100)", fontsize=8)
    ax.set_title("(b) 거래량 최고일 전후 주가 경로 (중앙값·사분위)", fontsize=9.5, fontweight="bold")
    ax.grid(alpha=0.2, lw=0.4)
    ax.tick_params(labelsize=7)
    ax.legend(fontsize=7, loc="lower right")

    # (c) 거래량 경로
    ax = fig.add_subplot(gs[1, 0])
    vmed = np.nanmedian(V, axis=0)
    vq1 = np.nanpercentile(V, 25, axis=0)
    vq3 = np.nanpercentile(V, 75, axis=0)
    ax.fill_between(offs, vq1, vq3, color="#ffcc80", alpha=0.4, lw=0)
    ax.plot(offs, vmed, color="#e65100", lw=1.9, label="거래량 중앙값 (V_max=100)")
    ax.axvline(0, color="#c62828", lw=1.0, ls="--")
    ax.set_xlabel("V_max 기준 거래일", fontsize=8)
    ax.set_ylabel("거래량 (V_max=100)", fontsize=8)
    ax.set_ylim(0, 160)
    ax.set_title("(c) 그 뒤 거래량은 어떻게 마르나", fontsize=9.5, fontweight="bold")
    ax.grid(alpha=0.2, lw=0.4)
    ax.tick_params(labelsize=7)
    ax.legend(fontsize=7, loc="upper right")

    # (d) 경과일별 재돌파율·거래량
    ax = fig.add_subplot(gs[1, 1])
    b = pd.cut(df["days_after"], [-1, 4, 9, 14, 19, 24, 29, 34, 39],
               labels=["0~4", "5~9", "10~14", "15~19", "20~24", "25~29", "30~34", "35~39"])
    g = df.groupby(b, observed=True)
    x = np.arange(8)
    reb = g["rebreak"].mean().to_numpy() * 100
    shr = g["vm_vol_shrink5"].median().to_numpy() * 100
    ax.bar(x - 0.19, reb, width=0.36, color="#5b8def", label="V_max 고가 재돌파율(%)")
    ax.bar(x + 0.19, shr, width=0.36, color="#ffb74d", label="5일 거래량 / V_max(%)")
    for i in range(8):
        ax.text(x[i] - 0.19, reb[i] + 1.5, f"{reb[i]:.0f}", ha="center", fontsize=6.6)
        ax.text(x[i] + 0.19, shr[i] + 1.5, f"{shr[i]:.0f}", ha="center", fontsize=6.6)
    ax.set_xticks(x)
    ax.set_xticklabels([str(c) for c in g.groups], fontsize=7)
    ax.set_xlabel("V_max 이후 경과 거래일", fontsize=8)
    ax.set_ylabel("%", fontsize=8)
    ax.set_ylim(0, 88)
    ax.set_title("(d) 경과일이 길수록 재돌파는 늘고, 거래량은 20%대로 마른다", fontsize=9.5, fontweight="bold")
    ax.legend(fontsize=7, loc="upper left")
    ax.grid(axis="y", alpha=0.2, lw=0.4)
    ax.tick_params(labelsize=7)

    fig.suptitle("급등 전 8주 — 거래량 최고일(V_max)은 언제 터지고, 그 뒤 어떻게 흐르나  "
                 "(월봉 +20% 급등 3,184건·999종목, 2016-01~2026-10)",
                 fontsize=11, fontweight="bold", y=0.965)
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print("saved", out)


def case_figure(row: dict, out: Path, idx: int):
    plt = _font()
    from matplotlib.patches import Rectangle
    d = pd.read_pickle(OHLCV / f"{row['code']}.pkl")
    c = d["aclose"].to_numpy(float); o = d["aopen"].to_numpy(float)
    h = d["ahigh"].to_numpy(float); l = d["alow"].to_numpy(float)
    v = d["volume"].to_numpy(float)
    dates = d.index
    T = int(row["T"]); im = int(row["imax"])
    m20 = pd.Series(c).rolling(20).mean().to_numpy()
    m60 = pd.Series(c).rolling(60).mean().to_numpy()
    m120 = pd.Series(c).rolling(120).mean().to_numpy()

    i0, i1 = T - 42, min(len(c) - 1, T + 22)
    xs = np.arange(i0, i1 + 1)
    fig, (ax, axv) = plt.subplots(2, 1, figsize=(13.0, 6.4), sharex=True,
                                  gridspec_kw=dict(height_ratios=[3.4, 1.0], hspace=0.06))
    ax.axvspan(im - 0.5, T + 0.5, color=PULL, alpha=0.85, lw=0, zorder=0)
    ax.axvspan(T + 0.5, min(i1, T + 21) + 0.5, color=RISE, alpha=0.85, lw=0, zorder=0)
    for i in xs:
        up = c[i] >= o[i]
        col = "#d32f2f" if up else "#1565c0"
        ax.vlines(i, l[i], h[i], color=col, lw=0.7, zorder=3)
        lo_, hi_ = min(o[i], c[i]), max(o[i], c[i])
        ax.add_patch(Rectangle((i - 0.32, lo_), 0.64, max(hi_ - lo_, 1e-9), facecolor=col,
                               edgecolor=col, lw=0.4, zorder=4))
    ax.plot(xs, m20[xs], color="#f9a825", lw=1.2, label="20일선", zorder=5)
    ax.plot(xs, m60[xs], color="#6a1b9a", lw=1.2, label="60일선", zorder=5)
    ax.plot(xs, m120[xs], color="#2e7d32", lw=1.2, label="120일선", zorder=5)
    lo = np.nanmin(l[xs]); hi = np.nanmax(h[xs]); pad = (hi - lo) * 0.10
    ax.set_ylim(lo - pad, hi + pad * 1.75)
    ax.set_xlim(i0 - 1, i1 + 1)
    # V_max 표시: 세로 점선 + 상단 ▼ + 라벨
    ax.axvline(im, color="#b71c1c", lw=1.0, ls=":", alpha=0.85, zorder=6)
    ax.plot([im], [hi + pad * 0.80], marker="v", ms=8, color="#b71c1c", zorder=7, clip_on=False)
    ha_ = "center" if im >= i0 + 6 else "left"
    ax.annotate(f"V_max {dates[im].strftime('%m/%d')} · 거래량 {row['vmax_x20']:.1f}배",
                xy=(im, hi + pad * 0.80), xytext=(0 if ha_ == "center" else 3, 9),
                textcoords="offset points", fontsize=8.5, fontweight="bold", color="#b71c1c",
                ha=ha_, va="bottom", zorder=8)
    # T 표시
    ax.axvline(T, color="#37474f", lw=1.0, ls="--", alpha=0.8)
    ax.text(T, lo - pad * 0.52, "T", fontsize=8.4, fontweight="bold", color="#37474f",
            ha="center", va="bottom", zorder=8,
            bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.75))
    ax.text(im + (T - im) / 2, lo - pad * 0.15, f"눌림·마름 {row['pull_from_high']*100:.0f}%",
            fontsize=8.2, ha="center", color="#0d47a1")
    ax.text(T + 10, lo - pad * 0.15, f"급등 {row['ret']*100:+.0f}%", fontsize=8.2, ha="center", color="#b71c1c")
    ax.legend(fontsize=7.4, loc="lower left", bbox_to_anchor=(0.0, 1.005), ncol=3,
              frameon=False, borderpad=0.2, handlelength=1.4, columnspacing=1.6)
    ax.grid(alpha=0.15, lw=0.4)
    ax.tick_params(labelsize=7.5)
    ax.set_ylabel("주가(원)", fontsize=8)
    ax.set_title(f"[{idx}] {row['name']} ({row['code']}) — {row['M']} 월봉 {row['ret']*100:+.0f}% 급등 · "
                 f"유형 {row['ftype']}",
                 fontsize=11.5, fontweight="bold", pad=22)
    xsl = []
    step = max(1, len(xs) // 10)
    for i in xs[::step]:
        xsl.append((i, dates[i].strftime("%m/%d")))
    ax.set_xticks([a for a, _ in xsl]); ax.set_xticklabels([b for _, b in xsl], fontsize=7)
    # 거래량
    cols = ["#d32f2f" if c[i] >= o[i] else "#1565c0" for i in xs]
    axv.bar(xs, v[xs], color=cols, width=0.7, alpha=0.85)
    axv.axvspan(im - 0.5, T + 0.5, color=PULL, alpha=0.85, lw=0, zorder=0)
    axv.axvspan(T + 0.5, min(i1, T + 21) + 0.5, color=RISE, alpha=0.85, lw=0, zorder=0)
    axv.axvline(im, color="#b71c1c", lw=1.0, ls=":", alpha=0.8)
    axv.set_xlim(i0 - 1, i1 + 1)
    axv.set_ylabel("거래량", fontsize=7.5)
    axv.tick_params(labelsize=6.5)
    axv.set_yticks([])
    axv.grid(alpha=0.15, lw=0.4)
    fig.text(0.5, 0.005,
             f"V_max {dates[im].date()} → T {row['days_after']}거래일 · 5일 거래량 V_max의 {row['vm_vol_shrink5']*100:.0f}% · "
             f"고가 재돌파 {'O' if row['rebreak'] else 'X'} · T 시점 V_max 고가 대비 {row['T_vs_vmhigh']*100:+.0f}% · "
             f"급등달 첫날 시가 매수 후 20일 {row['r20']*100:+.0f}% / 30일 {row['r30']*100:+.0f}% (비용 차감)",
             ha="center", fontsize=8, color="#333")
    fig.subplots_adjust(left=0.05, right=0.99, top=0.90, bottom=0.10)
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print("saved", out)


REPS4 = [("T3_신고가 돌파형", 0), ("T4_초기 폭발→눌림·마름", 1),
         ("T1_막판 폭발(1주 전)", 2), ("T2_투매성 음봉(MA60 아래)", 3)]


def cmd_charts(args):
    df = pd.read_pickle(OUT_CASES)
    flow_figure(df, DL / "surge-8w-vmax-flow.png")
    for t, k in REPS4:
        g = df[df["ftype"] == t]
        row = pick_rep(g).iloc[0].to_dict()
        row["T"] = int(row["T"]); row["imax"] = int(row["imax"])
        case_figure(row, DL / f"surge-8w-case-{k+1}.png", k + 1)


# ------------------------------------------------------------------ 5) 수동 검증(순수 pandas 루프)
def cmd_verify(args):
    df = pd.read_pickle(OUT_CASES)
    codes = args.codes.split(",") if args.codes else list(df["code"].value_counts().head(3).index)
    checked = 0
    worst = 0.0
    fails = []
    for code in codes:
        d = pd.read_pickle(OHLCV / f"{code}.pkl")
        sub = df[df["code"] == code].head(5)
        for r in sub.itertuples():
            T = int(r.T)
            win = d.iloc[T - W8:T]                     # 인덱스 T-40 ~ T-1
            imax_date = None
            best = -1.0
            for dt, rowv in win.iterrows():            # 순수 for 루프
                if rowv["volume"] > best:
                    best = rowv["volume"]
                    imax_date = dt
            i = d.index.get_loc(imax_date)
            vol20 = d["volume"].iloc[i - 19:i + 1].mean()
            volx = d["volume"].iloc[i] / vol20
            days = T - 1 - i
            lo = min(d["alow"].iloc[i + 1:T + 1]) if days >= 1 else d["ahigh"].iloc[i]
            pull = lo / d["ahigh"].iloc[i] - 1
            v5 = d["volume"].iloc[T - 4:T + 1].mean()
            shrink = v5 / d["volume"].iloc[i]
            rebreak = max(d["ahigh"].iloc[i + 1:T + 1]) > d["ahigh"].iloc[i] if days >= 1 else False
            tvh = d["aclose"].iloc[T] / d["ahigh"].iloc[i] - 1
            e = T + 1
            c = d["aclose"]
            cost = 2 * SLIP + 2 * COMM + TAX_BY_YEAR.get(int(d.index[e].year), 0.0020)
            r20 = c.iloc[e + 19] / d["aopen"].iloc[e] - 1 - cost
            r30 = c.iloc[e + 29] / d["aopen"].iloc[e] - 1 - cost
            pairs = [
                ("week", week_no(i - T), r.vmax_week),
                ("x20", volx, r.vmax_x20),
                ("days", days, r.days_after),
                ("pull", pull, r.pull_from_high),
                ("shrink5", shrink, r.vm_vol_shrink5),
                ("rebreak", rebreak, r.rebreak),
                ("Tvs", tvh, r.T_vs_vmhigh),
                ("r20", r20, r.r20),
                ("r30", r30, r.r30),
            ]
            for name, got, exp in pairs:
                diff = abs(float(got) - float(exp))
                worst = max(worst, diff)
                if diff > 1e-9:
                    fails.append((code, r.M, name, got, exp, diff))
            checked += 1
            print(f"{code} {r['M'] if False else r.M} T={r.T_date} imax={imax_date.date()} "
                  f"week={week_no(i-T)} x20={volx:.4f} days={days} pull={pull*100:.2f}% "
                  f"shrink={shrink*100:.2f}% rebreak={rebreak} r20={r20*100:+.2f}%")
    print(f"\n[verify] {len(codes)}종목 · {checked}사례 · 최대오차 {worst:.2e} · 불일치 {len(fails)}건")
    for f in fails[:10]:
        print("  MISMATCH", f)
    if not fails:
        print("  → 전 항목 오차 0 (1e-9 이내)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "rules", "summary", "charts", "verify"])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--codes", type=str, default="")
    a = ap.parse_args()
    if a.cmd == "build":
        cmd_build(a)
    elif a.cmd == "rules":
        cmd_rules(a)
    elif a.cmd == "summary":
        cmd_summary(a)
    elif a.cmd == "charts":
        cmd_charts(a)
    else:
        cmd_verify(a)
