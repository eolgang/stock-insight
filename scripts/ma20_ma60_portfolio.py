#!/usr/bin/env python3
"""MA20 근접 + MA60 이격 큰 자리 — 회전 포트폴리오 검증.

20거래일마다 갈아타기: 직전 20거래일 안에 신호가 나온 종목 중 N개를 골라(동일가중) 다음 거래일
시가에 사고 20거래일 뒤 종가에 판다. 항상 100% 투입(후보가 N보다 적으면 후보 전부에 균등 배분).
비교: 코스피 buy&hold, 삼성전자 buy&hold, 전 유니버스 동일가중 무작위 교체(=기대값), 국면 필터형.

산출: work/backtest/ma20_ma60_portfolio.json
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from ma_support_rebound import CACHE, OHLCV, WORK, MIN_MKTCAP, MIN_VALUE, SLIP, COMM, TAX_BY_YEAR

START = "2016-01-04"
HOLD = 20
ROT = 20


def pstats(periods: pd.Series, label: str) -> dict:
    p = periods.dropna()
    eq = (1 + p).cumprod()
    yrs = len(p) * ROT / 244.0
    cagr = eq.iloc[-1] ** (1 / yrs) - 1 if yrs > 0 else np.nan
    dd = (eq / eq.cummax() - 1).min()
    sharpe = p.mean() / p.std(ddof=1) * np.sqrt(244 / ROT) if p.std() > 0 else np.nan
    return {"label": label, "periods": int(len(p)), "cum": float(p.add(1).prod() - 1), "cagr": float(cagr),
            "mdd": float(dd), "sharpe": float(sharpe), "win_period": float((p > 0).mean()),
            "mean_period": float(p.mean()), "median_period": float(p.median())}


def main():
    uni = pd.read_pickle(CACHE / "universe.pkl")
    idx = pd.read_pickle(CACHE / "index.pkl")
    S = pd.read_pickle(WORK / "ma20_ma60_signals.pkl")
    S["date"] = pd.to_datetime(S["date"]); S["entry_date"] = pd.to_datetime(S["entry_date"])

    days = idx.index[idx.index >= START]
    reb = days[::ROT]
    schedule = []
    for i, d in enumerate(reb):
        pos = idx.index.get_loc(d)
        if pos + 1 + HOLD - 1 >= len(idx):
            continue
        e = idx.index[pos + 1]
        x = idx.index[pos + HOLD]
        prev = reb[i - 1] if i > 0 else idx.index[0]
        schedule.append({"reb": d, "entry": e, "exit": x, "prev": prev})
    print("periods", len(schedule), schedule[0]["reb"].date(), schedule[-1]["reb"].date())

    # --- 종목별 가격/유니버스 준비
    keep = []
    for r in uni.itertuples():
        name = r.name or ""
        if r.end_type != "stock" or (r.code in ("", None)):
            continue
        if not (OHLCV / f"{r.code}.pkl").exists():
            continue
        keep.append((r.code, r.last_close, r.mktcap_eok))
    codes = [k[0] for k in keep]
    meta = {k[0]: (k[1], k[2]) for k in keep}

    ent = pd.DatetimeIndex([s["entry"] for s in schedule])
    ext = pd.DatetimeIndex([s["exit"] for s in schedule])
    px = {}       # code -> (entry_open[], exit_close[])
    uniok = {}    # code -> bool[] (entry date 기준 유니버스)
    for c in codes:
        d = pd.read_pickle(OHLCV / f"{c}.pkl")
        if len(d) < 300:
            continue
        a = d.reindex(ent)
        b = d.reindex(ext)
        if a["aopen"].notna().sum() == 0:
            continue
        px[c] = (a["aopen"].to_numpy(float), b["aclose"].to_numpy(float))
        lc, mce = meta[c]
        mk = mce * 1e8
        t20 = d["turnover"].rolling(20).mean().reindex(ent).to_numpy(float)
        v22 = d["volume"].reindex(ent).to_numpy(float)
        uniok[c] = (mk >= MIN_MKTCAP) & (t20 >= MIN_VALUE) & (v22 > 0)

    tax_by_year = {y: TAX_BY_YEAR.get(y, 0.0020) for y in range(2015, 2028)}
    cost = np.array([2 * SLIP + 2 * COMM + tax_by_year[s["exit"].year] for s in schedule])

    # --- 전 유니버스 동일가중(무작위 교체 기대값)
    R = np.zeros((len(codes), len(schedule)))
    OK = np.zeros((len(codes), len(schedule)), bool)
    for i, c in enumerate(codes):
        if c not in px:
            continue
        o, cl = px[c]
        with np.errstate(divide="ignore", invalid="ignore"):
            R[i] = cl / o - 1 - cost
        OK[i] = uniok[c] & np.isfinite(R[i])
    R = np.where(OK, R, np.nan)
    uni_mean = np.nanmean(R, axis=0)
    uni_count = np.isfinite(R).sum(axis=0)

    # --- 코스피·삼성전자 buy&hold (같은 기간)
    k0 = idx["close_k"].loc[idx.index >= START].iloc[0]
    k1 = idx["close_k"].loc[idx.index <= schedule[-1]["exit"]].iloc[-1]
    kospi_bh = k1 / k0 - 1
    kospi_periods = pd.Series(
        (idx["close_k"].reindex(ext).to_numpy() / idx["open_k"].reindex(ent).to_numpy() - 1), index=range(len(schedule)))
    sam = pd.read_pickle(OHLCV / "005930.pkl")
    s0 = sam["aclose"].loc[sam.index >= START].iloc[0]
    s1 = sam["aclose"].loc[sam.index <= schedule[-1]["exit"]].iloc[-1]
    sam_bh = s1 / s0 - 1

    # --- 규칙 포트폴리오
    def run_picks(mode: str, N: int, regime: str = "none", seed: int = 0, lookback: int = ROT):
        rng = np.random.default_rng(seed)
        rets, names, lists = [], [], []
        for s in schedule:
            lo = s["reb"] - pd.Timedelta(days=0)
            pos = idx.index.get_loc(s["reb"])
            prev_d = idx.index[max(pos - lookback, 0)]
            pool = S[(S.entry_date <= s["reb"]) & (S.entry_date > prev_d)]
            list_ = []
            if len(pool):
                pool = pool.sort_values("disp20", key=lambda x: x.abs())
                if mode == "random":
                    pool = pool.sample(frac=1, random_state=int(rng.integers(1e9)))
                elif mode == "bigcap":
                    pool = pool.sort_values("log_mktcap", ascending=False)
                list_ = pool.head(N)["code"].tolist()
            if regime in ("dd8", "dd8cash"):
                dd = idx["close_k"].loc[:s["reb"]]
                ddv = dd.iloc[-1] / dd.iloc[-60:].max() - 1
                if ddv > -0.08:
                    list_ = []
                    names.append(0)
                    rets.append(kospi_periods.iloc[len(rets)] if regime == "dd8" else 0.0)
                    lists.append([])
                    continue
            names.append(len(list_))
            if not list_:
                rets.append(kospi_periods.iloc[len(rets)])   # 신호 없으면 코스피 보유
                lists.append([])
                continue
            vals = []
            for c in list_:
                if c in px:
                    o, cl = px[c]
                    i = len(rets)
                    if np.isfinite(o[i]) and np.isfinite(cl[i]):
                        vals.append(cl[i] / o[i] - 1 - cost[i])
            rets.append(float(np.mean(vals)) if vals else np.nan)
            lists.append(list_)
        return pd.Series(rets), names, lists

    out = {}
    for N in (5, 10):
        for mode in ("absdisp", "bigcap", "random"):
            p, nm, _ = run_picks(mode, N)
            out[f"rule_{mode}_N{N}"] = {**pstats(p, f"규칙 {mode} N={N}"), "avg_names": float(np.mean(nm)),
                                        "min_names": int(np.min(nm))}
        pr, nmr, _ = run_picks("random", N, seed=42)
        out[f"rule_random_seed42_N{N}"] = {**pstats(pr, f"규칙 후보중 무작위 N={N}")}
        p5, nm5, _ = run_picks("absdisp", N, regime="dd8")
        out[f"rule_dd8_N{N}"] = {**pstats(p5, f"규칙+국면필터(코스피 60일고점 -8%↓, 아니면 코스피) N={N}"),
                                 "avg_names": float(np.mean(nm5))}
        # 규칙 구간만(신호 없으면 현금)
        p6, _, _ = run_picks("absdisp", N, regime="dd8cash")
        out[f"rule_dd8_cash_N{N}"] = {**pstats(p6, f"규칙+국면필터(현금) N={N}")}
        p7, nm7, _ = run_picks("absdisp", N, lookback=5)
        out[f"rule_absdisp_fresh5_N{N}"] = {**pstats(p7, f"규칙(최근 5일 신호만) N={N}"), "avg_names": float(np.mean(nm7))}
        if N == 5:
            seeds = [run_picks("random", N, seed=sd)[0] for sd in range(50)]
            cums = [float((1 + x.fillna(0)).prod() - 1) for x in seeds]
            out["rule_random_50seeds_N5"] = {"mean_cum": float(np.mean(cums)), "p5": float(np.percentile(cums, 5)),
                                             "p95": float(np.percentile(cums, 95)), "best": float(np.max(cums)),
                                             "worst": float(np.min(cums))}



    # --- 대조: 전 유니버스 시총 상위 N (규칙 없이)
    sh = {c: (meta[c][1] * 1e8 / meta[c][0] if meta[c][0] else np.nan) for c in codes}
    for N in (5, 10):
        vals, comp = [], []
        for j, sc in enumerate(schedule):
            mcj = {}
            for c in codes:
                if c not in px:
                    continue
                _, cl = px[c]
                if np.isfinite(cl[j]) and np.isfinite(sh[c]):
                    mcj[c] = cl[j] * sh[c]
            if not mcj:
                vals.append(np.nan); continue
            top = sorted(mcj, key=mcj.get, reverse=True)[:N]
            vs = [R[codes.index(c), j] for c in top if np.isfinite(R[codes.index(c), j])]
            vals.append(float(np.mean(vs)) if vs else float(uni_mean[j]))
            if j % 20 == 0:
                comp.append([c for c in top])
        out[f"universe_bigcap_N{N}"] = pstats(pd.Series(vals), f"그 시점 시총 상위 {N} 교체(규칙 없음)")
        out[f"universe_bigcap_N{N}_comp"] = comp
    out["universe_top5"] = comp[0] if comp else []

    # --- 이벤트 드리븐(지연 없음): 신호 다음날 시가 진입, 20거래일 보유, N슬롯
    cal = idx.index[idx.index >= START]
    pos_of = {d: i for i, d in enumerate(cal)}
    need = sorted(set(S.code))
    PX = {}
    for c in need:
        d = pd.read_pickle(OHLCV / f"{c}.pkl")
        o = d["aopen"].reindex(cal).to_numpy(float)
        cl = d["aclose"].reindex(cal).to_numpy(float)
        PX[c] = (o, cl)
    sig_by_day = {}
    for r in S.itertuples():
        di = pos_of.get(r.entry_date)
        if di is None or r.code not in PX:
            continue
        ex_i = di + HOLD - 1
        if ex_i >= len(cal):
            continue
        sig_by_day.setdefault(di, []).append((r.code, ex_i, abs(r.disp20), r.log_mktcap))

    kret = idx["close_k"].reindex(cal).ffill().pct_change().fillna(0).to_numpy(float)
    dd60_cal = (idx["close_k"].reindex(cal).ffill()
                / idx["close_k"].reindex(cal).ffill().rolling(60).max() - 1).to_numpy(float)

    def slots(N, order="absdisp", regime=False):
        cash, pos, hist = 1.0, [], []
        for i in range(len(cal)):
            if i > 0:
                eq_prev = cash + sum(v * (PX[c][1][i - 1] / ep) for c, ex, ep, v in pos
                                     if np.isfinite(PX[c][1][i - 1]))
            else:
                eq_prev = cash
            sigs = sig_by_day.get(i, [])
            if regime and (not np.isfinite(dd60_cal[i]) or dd60_cal[i] > -0.08):
                sigs = []
            if sigs:
                sigs = sorted(sigs, key=(lambda x: x[2]) if order == "absdisp" else (lambda x: -x[3]))
                for c, ex_i, _, _ in sigs:
                    if len(pos) >= N:
                        break
                    ep = PX[c][0][i]
                    if not np.isfinite(ep) or ep <= 0:
                        continue
                    v = min(eq_prev / N, cash)
                    if v <= 0:
                        break
                    cash -= v
                    pos.append((c, ex_i, ep, v))
            if regime:
                cash *= (1 + kret[i])
            keep = []
            for c, ex_i, ep, v in pos:
                cl = PX[c][1][i]
                if i >= ex_i and np.isfinite(cl):
                    cash += v * cl / ep
                else:
                    keep.append((c, ex_i, ep, v))
            pos = keep
            hist.append(cash + sum(v * (PX[c][1][i] / ep) for c, ex, ep, v in pos
                                   if np.isfinite(PX[c][1][i])))
        return pd.Series(hist, index=cal)

    for N in (5, 10):
        for order, reg in (("absdisp", False), ("bigcap", False), ("absdisp", True), ("bigcap", True)):
            eqs = slots(N, order, reg)
            cum = float(eqs.iloc[-1] - 1)
            yrs = len(cal) / 244.0
            dd = float((eqs / eqs.cummax() - 1).min())
            rets = eqs.pct_change().dropna()
            out[f"event_slots_{order}{'_regime' if reg else ''}_N{N}"] = {
                "label": f"이벤트 드리븐(지연 없음, {order}{'+국면필터(코스피60일고점-8%↓, 잔여현금 코스피)' if reg else ''}, {N}슬롯)", "cum": cum,
                "cagr": float((1 + cum) ** (1 / yrs) - 1), "mdd": dd,
                "sharpe": float(rets.mean() / rets.std() * np.sqrt(244)) if rets.std() > 0 else np.nan,
                "win_period": float((rets > 0).mean())}

    out["span"] = {"start": str(schedule[0]["reb"].date()), "end_last_exit": str(schedule[-1]["exit"].date())}
    kf0 = idx["close_k"].loc[idx.index >= START].iloc[0]
    kf1 = idx["close_k"].iloc[-1]
    out["kospi_bh_full"] = {"label": "코스피 buy&hold(전 구간)", "cum": float(kf1 / kf0 - 1)}
    out["samsung_bh_full"] = {"label": "삼성전자 buy&hold(전 구간)",
                              "cum": float(sam["aclose"].loc[sam.index >= START].iloc[-1] / s0 - 1)}
    out["kospi_bh"] = {"label": "코스피 buy&hold", "cum": float(kospi_bh),
                       "cagr": float((1 + kospi_bh) ** (244 / (len(schedule) * ROT)) - 1)}
    out["samsung_bh"] = {"label": "삼성전자 buy&hold", "cum": float(sam_bh),
                         "cagr": float((1 + sam_bh) ** (244 / (len(schedule) * ROT)) - 1)}
    out["universe_random"] = pstats(pd.Series(uni_mean), "전 유니버스 동일가중 교체(무작위 기대값)")
    out["universe_period_count"] = [int(uni_count.min()), int(np.mean(uni_count))]
    out["rule_kospi_period"] = pstats(kospi_periods, "코스피 20일 보유(같은 회전)")
    out["schedule"] = [{"reb": str(s["reb"].date()), "entry": str(s["entry"].date()), "exit": str(s["exit"].date())}
                       for s in schedule]
    # 마지막 포트폴리오 상세
    p, nm, lst = run_picks("absdisp", 10)
    out["detail_N10"] = pd.DataFrame({"ret": p, "names": nm}).describe().to_dict()
    out["equity_tail"] = [float(x) for x in (1 + p.fillna(0)).cumprod().iloc[::12]]

    with open(WORK / "ma20_ma60_portfolio.json", "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    print("[write]", WORK / "ma20_ma60_portfolio.json")
    for k, v in out.items():
        if isinstance(v, dict) and "cum" in v:
            print(f"{k:28s} cum {v['cum']*100:9.1f}%  cagr {v.get('cagr', float('nan'))*100:6.2f}%  "
                  f"mdd {v.get('mdd', float('nan'))*100:7.1f}%  sharpe {v.get('sharpe', float('nan')):.2f}  "
                  f"win {v.get('win_period', float('nan'))*100:4.1f}%  names {v.get('avg_names', float('nan')):.1f}")


if __name__ == "__main__":
    main()
