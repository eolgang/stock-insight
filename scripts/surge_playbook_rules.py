#!/usr/bin/env python3
"""급등 전 형태에서 뽑은 매매법 후보를 전 종목·전 기간에서 학습/검증으로 나눠 검증한다.

보유기간은 20·30·40거래일 세 가지. 진입은 (a) 신호일 종가 (b) 다음 거래일 시가 두 방식.
결과: work/backtest/surge_rules.json
"""
from __future__ import annotations

import json
import os
import warnings
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

import surge_only_pattern as sp

WORK = sp.WORK
OUT = WORK / "surge_rules.json"
HOLDS = (20, 30, 40)
SPLITS = {"learn": (2016, 2020), "valid": (2021, 2026)}


def panel_atoms(d: pd.DataFrame):
    p = sp.prep(d)
    c, h, l, v = p["c"], p["h"], p["l"], p["v"]
    n = p["n"]
    T = np.arange(n)
    ma = p["ma"]

    def above(k):
        return (c > ma[k]) & np.isfinite(ma[k])

    def brk(line, w):
        """최근 w일 안에 line을 종가로 상향 돌파한 적이 있는지."""
        cur = (c > line) & np.isfinite(line)
        prev = np.r_[False, cur[:-1]]
        new = cur & ~prev
        return pd.Series(new.astype(float)).rolling(w, min_periods=1).max().to_numpy() > 0

    ret1 = p["ret1"]
    ret10 = pd.Series(c).pct_change(10).to_numpy()
    ret15 = pd.Series(c).pct_change(15).to_numpy()
    disp20 = c / ma[20] - 1
    d52w = c / pd.Series(h).rolling(250).max().to_numpy() - 1
    dh60 = c / pd.Series(h).rolling(60).max().to_numpy() - 1
    j0 = pd.Series(l).rolling(15).min().to_numpy()
    hi15 = pd.Series(h).rolling(15).max().to_numpy()
    win_range = (hi15 - j0) / j0

    a = {}
    a["align3"] = (ma[5] > ma[20]) & (ma[20] > ma[60]) & np.isfinite(ma[60])
    a["align5"] = a["align3"] & (ma[60] > ma[120]) & (ma[120] > ma[240]) & np.isfinite(ma[240])
    a["sl60up"] = (ma[60] / pd.Series(ma[60]).shift(20).to_numpy() - 1) > 0.01
    a["disp20_mid"] = (disp20 >= -0.02) & (disp20 <= 0.08)
    a["br250"] = brk(p["hi"][250], 15)
    a["br120"] = brk(p["hi"][120], 15)
    a["br60"] = brk(p["hi"][60], 15)
    a["brh20"] = brk(p["hi"][20], 15)
    a["brma60"] = brk(ma[60], 15)
    a["brma20"] = brk(ma[20], 15)
    a["v5_15"] = (p["v5"] / p["v20"]) >= 1.5
    a["v5_20"] = (p["v5"] / p["v20"]) >= 2.0
    a["dry"] = (p["v10"] / p["v20"]) < 0.85
    a["near52"] = d52w > -0.05
    a["hot"] = ret15 >= 0.20
    a["boxtight"] = win_range <= 0.30
    a["above60"] = above(60)
    a["above20"] = above(20)
    a["not_align3"] = ~a["align3"]
    a["dh60near"] = dh60 > -0.08
    a["ret10_up"] = ret10 >= 0.05
    a["align3_not_disp"] = a["align3"]
    a["t520_2"] = (p["t5"] / p["t20"]) >= 2.0
    a["quiet"] = (ret10 >= -0.03) & (ret10 <= 0.03) & ((p["v10"] / p["v20"]) >= 1.5) & above(20)
    volup = ((ret1 >= 0.05) & (p["vr"] >= 2.0))
    a["volup10"] = pd.Series(volup.astype(float)).rolling(10, min_periods=1).max().to_numpy() > 0
    a["ma20break10"] = brk(ma[20], 10)

    # 눌림 후 회복: 10일 고점 대비 -5% 이상 밀렸다가 지금 고점 -4% 이내
    hi10 = pd.Series(c).rolling(10).max().to_numpy()
    a["pullback_recover"] = (c / hi10 - 1 > -0.04) & (pd.Series(c).rolling(10).min().to_numpy() / hi10 - 1 <= -0.05)

    return p, a


def fwd(c, o, cost, e_idx, hold, mode):
    """mode='close'면 신호일 종가 진입, 'open'이면 다음날 시가 진입."""
    if mode == "open":
        e = e_idx + 1
        ok = e + hold - 1 < c.size
        out = np.full(e_idx.size, np.nan)
        ee = e[ok]
        out[ok] = c[ee + hold - 1] / o[ee] - 1 - cost[ee]
        return out
    ok = e_idx + hold - 1 < c.size
    out = np.full(e_idx.size, np.nan)
    ee = e_idx[ok]
    out[ok] = c[ee + hold - 1] / c[ee] - 1 - cost[ee]
    return out


def run_one(code: str):
    if not (sp.OHLCV / f"{code}.pkl").exists():
        return None
    d = pd.read_pickle(sp.OHLCV / f"{code}.pkl")
    if d.empty or len(d) < 300:
        return None
    row = UNI.loc[code]
    p, a = panel_atoms(d)
    n = p["n"]
    c, o = p["c"], p["o"]
    dates = d.index
    mktcap = d["close"].to_numpy(float) * float(row["shares"])
    uni_ok = (mktcap >= sp.MIN_MKTCAP) & (p["t20"] >= sp.MIN_VALUE) & (p["v"] > 0)
    if not np.isfinite(p["ma"][240]).any():
        return None
    cost = np.array([2 * sp.SLIP + 2 * sp.COMM + sp.TAX_BY_YEAR.get(int(y), 0.0020) for y in dates.year])
    years = dates.year.to_numpy()
    k = IDX["close_k"].to_numpy(float)
    k20 = pd.Series(k).rolling(20).mean().to_numpy()
    kospi_up = np.zeros(n, bool)
    kk = IDX["close_k"].reindex(dates).ffill().to_numpy(float)
    kss = IDX["close_k"].reindex(dates).ffill().rolling(20).mean().to_numpy(float)
    kospi_up = np.nan_to_num(kk) > np.nan_to_num(kss)

    base_ok = uni_ok & np.isfinite(p["ma"][240])
    out = {}
    bidx = np.flatnonzero(base_ok)[::5]
    for mode in ("open", "close"):
        for hold in HOLDS:
            r = fwd(c, o, cost, bidx, hold, mode)
            ok = np.isfinite(r)
            for sp_name, (y0, y1) in SPLITS.items():
                sel = ok & (years[bidx] >= y0) & (years[bidx] <= y1)
                if sel.any():
                    out.setdefault("BASE_전종목평균", {}).setdefault(mode, {}).setdefault(
                        f"h{hold}", {})[sp_name] = stats(r[sel]) if False else None
                    out["BASE_전종목평균"][mode][f"h{hold}"].setdefault(sp_name, [])
    # 실제 누적은 아래에서 배열로 처리하기 위해 리스트로 보관
    for mode in ("open", "close"):
        for hold in HOLDS:
            r = fwd(c, o, cost, bidx, hold, mode)
            ok = np.isfinite(r)
            for sp_name, (y0, y1) in SPLITS.items():
                sel = ok & (years[bidx] >= y0) & (years[bidx] <= y1)
                if sel.any():
                    key = f"BASE_전종목평균|{mode}|h{hold}|{sp_name}"
                    out.setdefault(key, []).extend(r[sel].astype(np.float32).tolist())
    out = {k: v for k, v in out.items() if isinstance(v, list)}
    for mode in ("open", "close"):
        for hold in HOLDS:
            for sp_name in SPLITS:
                out.pop(f"BASE_전종목평균|{mode}|h{hold}|{sp_name}_tmp", None)
    for name, mask in RULES_MASKS(a, kospi_up).items():
        m = mask & base_ok
        idxs = np.flatnonzero(m)
        if idxs.size == 0:
            continue
        for mode in ("open", "close"):
            for hold in HOLDS:
                r = fwd(c, o, cost, idxs, hold, mode)
                ok = np.isfinite(r)
                if not ok.any():
                    continue
                for sp_name, (y0, y1) in SPLITS.items():
                    sel = ok & (years[idxs] >= y0) & (years[idxs] <= y1)
                    if sel.any():
                        key = f"{name}|{mode}|h{hold}|{sp_name}"
                        out.setdefault(key, []).extend(r[sel].astype(np.float32).tolist())
    return out


def RULES_MASKS(a, kospi_up):
    A = a
    return {
        # 단일 조건 (조건 사다리용)
        "C1_신고가돌파": A["br250"],
        "C2_정배열": A["align3"],
        "C3_거래량2배": A["v5_20"],
        "C4_박스상단": A["brh20"],
        "C5_60일선회복": A["brma60"] & A["above60"],
        # 후보 규칙
        "R1_신고가+거래량": A["br250"] & A["v5_15"],
        "R2_신고가+거래량+시장": A["br250"] & A["v5_15"] & kospi_up,
        "R3_정배열가속": A["align3"] & A["sl60up"] & A["disp20_mid"],
        "R4_박스상단돌파": A["brh20"] & A["boxtight"] & A["above60"] & A["dh60near"],
        "R5_역배열탈출": A["brma60"] & A["above60"] & A["not_align3"],
        "R6_과열+거래량마름": A["hot"] & A["dry"],
        "R7_정배열가속+시장": A["align3"] & A["sl60up"] & A["disp20_mid"] & kospi_up,
        "R8_MA60아래단기반등": (~A["above60"]) & A["above20"] & A["brma20"],
        "R9_신고가돌파_거래량없이": A["br250"],
        "R10_정배열+모멘텀": A["align3"] & A["ret10_up"],
        "R11_눌림후회복": A["pullback_recover"] & A["align3"] & A["above60"],
        # 기존 보고서의 대표 규칙 재측정(정의 그대로, 진입만 다음날 시가로 통일)
        "P_A_신고가근접+거래대금2배": A["near52"] & A["t520_2"],
        "P_V1_조용한매집": A["quiet"],
        "P_V3_거래량동반상승": A["volup10"],
        "P_S1_MA20돌파": A["ma20break10"],
        # 조건 사다리 (R3 계열: 조건을 하나씩 더한다)
        "L1_정배열": A["align3"],
        "L2_정배열+60일선상승": A["align3"] & A["sl60up"],
        "L3_정배열+상승+이격제한": A["align3"] & A["sl60up"] & A["disp20_mid"],
        "L4_정배열+상승+이격+시장": A["align3"] & A["sl60up"] & A["disp20_mid"] & kospi_up,
        # 사다리 2 (신고가 계열)
        "M1_신고가": A["br250"],
        "M2_신고가+거래량": A["br250"] & A["v5_15"],
        "M3_신고가+거래량+시장": A["br250"] & A["v5_15"] & kospi_up,
    }


def stats(r: np.ndarray) -> dict:
    r = r[np.isfinite(r)]
    if r.size == 0:
        return {"n": 0}
    return {
        "n": int(r.size),
        "mean": float(r.mean()),
        "median": float(np.median(r)),
        "win": float((r > 0).mean()),
        "top10": float(np.percentile(r, 90)),
        "maxloss": float(r.min()),
        "sd": float(r.std(ddof=1)) if r.size > 1 else None,
    }


UNI = None
IDX = None


def init_worker(uni, idx):
    global UNI, IDX
    import surge_only_pattern as spx
    spx.UNI = uni
    UNI, IDX = uni, idx


def main():
    global UNI, IDX
    uni = sp.load_universe()
    idx = pd.read_pickle(sp.CACHE / "index.pkl")
    UNI, IDX = uni, idx
    sp.UNI = uni
    codes = uni.index.tolist()
    agg: dict[str, list] = {}
    with ProcessPoolExecutor(max_workers=6, initializer=init_worker, initargs=(uni, idx)) as ex:
        for res in ex.map(run_one, codes, chunksize=40):
            if not res:
                continue
            for k, v in res.items():
                agg.setdefault(k, []).extend(v)
    out = {}
    for key, vals in agg.items():
        name, mode, hold, split = key.split("|")
        out.setdefault(name, {}).setdefault(mode, {}).setdefault(hold, {})[split] = stats(np.array(vals))
    with open(OUT, "w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(f"[rules] {len(out)} rules → {OUT}")
    rows = []
    for name, mv in out.items():
        for mode in mv:
            for hold in HOLDS:
                st = mv[mode].get(f"h{hold}", {})
                a, b = st.get("learn", {}), st.get("valid", {})
                rows.append([name, mode, hold, a.get("n"), a.get("mean"), a.get("median"),
                             b.get("n"), b.get("mean"), b.get("median"), b.get("win")])
    df = pd.DataFrame(rows, columns=["rule", "mode", "hold", "n_l", "mean_l", "med_l",
                                     "n_v", "mean_v", "med_v", "win_v"])
    pd.set_option("display.width", 240)
    print(df.sort_values(["hold", "mean_v"]).to_string(index=False, float_format=lambda x: f"{x:.4f}"))


if __name__ == "__main__":
    main()
