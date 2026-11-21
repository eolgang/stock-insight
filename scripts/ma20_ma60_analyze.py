#!/usr/bin/env python3
"""MA20 근접 + MA60 이격 큰 자리 — 분석 (단독 성과 / 분위 / 사건 / 사전예측).

입력
  work/backtest/ma20_ma60_signals.pkl   규칙 신호 (7,062행)
  work/backtest/ma20_ma60_random.npz    전 유니버스 임의 날 대조군
  work/backtest/ma20_ma60_grid.pkl      민감도 그리드
  work/backtest/ma20_ma60_events.pkl    월봉 +20% 사건 (pre/monthstart/confirm)
  work/backtest/ma20_ma60_panel.pkl     전 유니버스 5일 간격 패널
  work/backtest/ma_support_raw.json     기존 무작위·코스피 기준선
산출
  work/backtest/ma20_ma60_tables.json
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as st
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")
REPO = Path(__file__).resolve().parent.parent
WORK = REPO.parent / "work" / "backtest"
CACHE = WORK / "cache"

FEATS = ["disp5", "disp20", "disp60", "disp120", "gap20_60", "log_mktcap", "log_t20", "atr14",
         "box60", "vr", "v520", "vs10", "vr_max10", "d52", "depth10", "sl20", "sl60",
         "ret5", "ret20", "ret60", "since_cross60", "rsi14", "up_streak", "body",
         "range_pct", "tail_lo", "tail_hi", "big_yang", "big_eum", "kospi_dd60",
         "kospi_dd250", "kospi_r20", "kospi_d60", "kospi_bull", "offset"]
LAB = {"disp5": "5일선 이격%", "disp20": "20일선 이격%", "disp60": "60일선 이격%",
       "disp120": "120일선 이격%", "gap20_60": "MA20/MA60 간격", "log_mktcap": "시총(log10)",
       "log_t20": "20일 거래대금(log10)", "atr14": "ATR14%", "box60": "60일 박스폭",
       "vr": "당일 거래량/20일평균", "v520": "5일/20일 거래량", "vs10": "거래량 기울기(10일)",
       "vr_max10": "10일 최대 거래량 배수", "d52": "52주 신고가 대비", "depth10": "10일 고점 대비 눌림",
       "sl20": "MA20 기울기(20일)", "sl60": "MA60 기울기(20일)", "ret5": "최근 5일 수익률",
       "ret20": "최근 20일 수익률", "ret60": "최근 60일 수익률",
       "since_cross60": "MA60 상향돌파 후 경과일", "rsi14": "RSI14", "up_streak": "연속 상승일수",
       "body": "당일 몸통(종가−시가)/종가", "range_pct": "당일 변동폭(고−저)/종가",
       "tail_lo": "아래꼬리", "tail_hi": "위꼬리", "big_yang": "장대양봉(≥+5%)", "big_eum": "장대음봉(≤−5%)",
       "kospi_dd60": "코스피 60일 고점 대비", "kospi_dd250": "코스피 250일 고점 대비",
       "kospi_r20": "코스피 20일 수익률", "kospi_d60": "코스피 MA60 이격",
       "kospi_bull": "코스피>MA20", "offset": "급등월 시작까지 남은 거래일"}

HZ = (5, 10, 15, 20, 30, 40)
ALL_F = [f for f in FEATS if f != "offset"] + ["kospi_bull"]


def add_bull(df, idx, col="date"):
    ks = idx["close_k"]
    bull = (ks > ks.rolling(20).mean()).rename("kospi_bull")
    return df.join(bull, on=col)


def desc(x, mfe=None, mae=None) -> dict:
    x = np.asarray(pd.Series(x).dropna(), float)
    if x.size == 0:
        return {"n": 0}
    out = {"n": int(x.size), "mean": float(x.mean()), "median": float(np.median(x)),
           "win": float((x > 0).mean()), "sd": float(x.std(ddof=1)) if x.size > 1 else None,
           "p5": float(np.percentile(x, 5)), "p25": float(np.percentile(x, 25)),
           "p75": float(np.percentile(x, 75)), "p90": float(np.percentile(x, 90)),
           "p95": float(np.percentile(x, 95)), "p99": float(np.percentile(x, 99)),
           "min": float(x.min()), "max": float(x.max()),
           "skew": float(st.skew(x)) if x.size > 2 else None}
    pos, neg = x[x > 0], x[x < 0]
    out["payoff"] = float(pos.mean() / abs(neg.mean())) if pos.size and neg.size else None
    out["sum"] = float(x.sum())
    if mfe is not None:
        out["mfe"] = float(np.nanmean(mfe))
    if mae is not None:
        out["mae"] = float(np.nanmean(mae))
    return out


def tail_flag(d: dict) -> bool:
    """평균이 상단 꼬리에 끌린 정도: 평균/중앙값 비율과 상위 5% 합계 비중으로 판정."""
    if not d.get("n"):
        return False
    return d["mean"] > 0 and (d["median"] <= d["mean"] * 0.5)


def add_kospi_fwd(df: pd.DataFrame, idx: pd.DataFrame) -> pd.DataFrame:
    ks = idx.copy()
    for hz in HZ:
        ks[f"k{hz}"] = ks["close_k"].shift(-(hz - 1)) / ks["open_k"] - 1
    cols = {f"k{hz}": ks[f"k{hz}"] for hz in HZ}
    kdf = pd.DataFrame(cols, index=ks.index)
    out = df.drop(columns=[c for c in kdf.columns if c in df.columns], errors="ignore")
    return out.join(kdf, on="entry_date")


def buckets_by(x: pd.Series, edges=(0.0, 0.1, 0.2, 0.3, 0.4, 0.5)) -> list:
    """결과 수익률 내림차순 정렬 후 상위 10/10-20/20-30/30-40/40-50/하위50 구간 인덱스."""
    v = x.dropna().sort_values(ascending=False)
    n = len(v)
    cuts = [int(round(e * n)) for e in edges] + [n]
    return [(v.index[cuts[i]:cuts[i + 1]], f"{int(edges[i]*100)}~{int(edges[i+1]*100)}%" if i < 5 else "하위 50%")
            for i in range(6)]


def auc(x, y) -> float:
    x = np.asarray(x, float); y = np.asarray(y).astype(bool)
    ok = np.isfinite(x)
    if y[ok].sum() < 10 or (~y[ok]).sum() < 10:
        return np.nan
    return float(roc_auc_score(y[ok], x[ok]))


def block_bootstrap(vals: np.ndarray, months: np.ndarray, reps: int = 2000, seed: int = 7) -> dict:
    """월 블록 부트스트랩(같은 달 신호는 통째로 리샘플) — 중복 표본 상관을 보수적으로 반영."""
    rng = np.random.default_rng(seed)
    v = pd.Series(vals).dropna()
    m = pd.Series(months).loc[v.index]
    grp = v.groupby(m)
    keys = list(grp.groups.keys())
    sums = grp.sum().to_numpy()
    cnts = grp.count().to_numpy()
    out = []
    for _ in range(reps):
        pick = rng.integers(0, len(keys), len(keys))
        out.append(sums[pick].sum() / cnts[pick].sum())
    out = np.array(out)
    return {"lo": float(np.percentile(out, 2.5)), "hi": float(np.percentile(out, 97.5)),
            "se": float(out.std(ddof=1)), "blocks": len(keys)}


def main():
    S = pd.read_pickle(WORK / "ma20_ma60_signals.pkl")
    G = pd.read_pickle(WORK / "ma20_ma60_grid.pkl")
    E = pd.read_pickle(WORK / "ma20_ma60_events.pkl")
    P = pd.read_pickle(WORK / "ma20_ma60_panel.pkl")
    R = np.load(WORK / "ma20_ma60_random.npz")
    raw = json.load(open(WORK / "ma_support_raw.json"))
    idx = pd.read_pickle(CACHE / "index.pkl")

    S = add_bull(add_kospi_fwd(S, idx), idx, "date")
    E = add_bull(E, idx, "feat_date")
    P = add_bull(P, idx, "date")
    for hz in HZ:
        S[f"ex{hz}"] = S[f"r{hz}"] - S[f"k{hz}"]
    S["entry_month"] = pd.to_datetime(S["entry_date"]).dt.to_period("M").astype(str)

    out = {"meta": {"signals": int(len(S)), "stocks": int(S.code.nunique()),
                    "first": str(S.entry_date.min().date()), "last": str(S.entry_date.max().date()),
                    "rule": "disp20∈[-2%,+2%) & disp60<-10% & 시총500억↑ & 20일 거래대금 100억↑"}}

    # =====================================================================
    # 1. 규칙 재현·부호 확인
    # =====================================================================
    hm = raw["variants"]
    cells = {}
    for k in ("HM|2_0", "HM|3_0"):
        for hz in ("5", "20", "40"):
            d = hm[k].get(hz, {})
            cells[f"{k}|h{hz}"] = {"n": d.get("n"), "mean": d.get("mean"), "median": d.get("median"), "win": d.get("win")}
    out["reproduce"] = {"old_heatmap_cells": cells,
                        "old_combined_20": (cells["HM|2_0|h20"]["mean"] * cells["HM|2_0|h20"]["n"]
                                            + cells["HM|3_0|h20"]["mean"] * cells["HM|3_0|h20"]["n"])
                        / (cells["HM|2_0|h20"]["n"] + cells["HM|3_0|h20"]["n"]),
                        "new_rule": desc(S["r20"], S["mfe20"], S["mae20"]),
                        "grid_w2_t10": {}

                        }
    g = G[G.cell == "w2_t10"]
    out["reproduce"]["grid_w2_t10"] = desc(g["r20"], g["mfe20"], g["mae20"])
    ex = S.dropna(subset=["r20"]).sort_values("entry_date").head(6)[
        ["name", "date", "entry_date", "close", "ma20", "ma60", "disp20", "disp60", "r20"]]
    ex["close_ma20_pct"] = ex["close"] / ex["ma20"] - 1
    ex["close_ma60_pct"] = ex["close"] / ex["ma60"] - 1
    out["reproduce"]["sign_check"] = ex.assign(date=ex.date.astype(str), entry_date=ex.entry_date.astype(str)).round(4).to_dict("records")

    # 민감도 그리드
    grid = {}
    for cell, gd in G.groupby("cell"):
        d = desc(gd["r20"], gd["mfe20"], gd["mae20"])
        d["min_med"] = None
        grid[cell] = d
    out["grid"] = grid

    # 연도별 신호 수
    out["signal_freq"] = {str(k): {"n": int(v)} for k, v in S.groupby(S.entry_date.astype("datetime64[ns]").dt.year).size().items()}
    out["signal_freq_stocks"] = int(S.code.nunique())

    # =====================================================================
    # 2. 단독 성과 (보유기간별)
    # =====================================================================
    perf = {}
    for hz in HZ:
        d = desc(S[f"r{hz}"], S[f"mfe{hz}"], S[f"mae{hz}"])
        d["kospi"] = float(S[f"k{hz}"].mean()) if S[f"k{hz}"].notna().any() else None
        d["kospi_median"] = float(S[f"k{hz}"].median())
        d["ex_mean"] = float(S[f"ex{hz}"].mean())
        d["ex_median"] = float(S[f"ex{hz}"].median())
        rnd = raw["random"].get(str(hz), {})
        d["random_mean"] = rnd.get("mean")
        d["random_median"] = rnd.get("median")
        d["random_win"] = rnd.get("win")
        d["random_mfe"] = rnd.get("mfe")
        d["random_mae"] = rnd.get("mae")
        perf[str(hz)] = d
    out["perf"] = perf

    # =====================================================================
    # 3. 연도별·국면별
    # =====================================================================
    def grp_table(keys):
        res = {}
        for k, gd in S.groupby(keys, observed=True):
            d = desc(gd["r20"], gd["mfe20"], gd["mae20"])
            d["kospi"] = float(gd["k20"].mean())
            d["ex_mean"] = float(gd["ex20"].mean())
            d["ex_median"] = float(gd["ex20"].median())
            d40 = desc(gd["r40"])
            d["mean40"] = d40.get("mean"); d["median40"] = d40.get("median")
            res[str(k)] = d
        return res

    out["by_year"] = grp_table(S["entry_date"].astype("datetime64[ns]").dt.year)
    out["by_market"] = grp_table(S["market"])
    out["by_bull"] = grp_table(pd.Series(np.where(S["kospi_bull"] > 0, "코스피>MA20", "코스피≤MA20"), index=S.index))
    dd = pd.cut(S["kospi_dd60"], [-1, -0.15, -0.08, 0, 1],
                labels=["①-15%↓", "②-15~-8%", "③-8~0%", "④0%↑"])
    out["by_dd60"] = grp_table(pd.Series(dd.to_numpy(), index=S.index))
    bear = np.where(S["kospi_dd250"] <= -0.10, "약세장(코스피 250일 고점 -10%↓)", "그 외")
    out["by_bear"] = grp_table(pd.Series(bear, index=S.index))

    # =====================================================================
    # 4. 익절·손절 스윕
    # =====================================================================
    sweep = {}
    for hz in (20, 40):
        for tp in ("-", "5", "10", "15", "20"):
            for slv in ("-", "5", "8"):
                col = f"s{hz}_tp{tp}_sl{slv}"
                if col not in S:
                    continue
                d = desc(S[col])
                d["train"] = desc(S.loc[S.yr <= 2020, col]).get("mean")
                d["valid"] = desc(S.loc[S.yr >= 2021, col]).get("mean")
                sweep[col] = d
    out["sweep"] = sweep

    # =====================================================================
    # 5. 학습/검증 + 부트스트랩 + 비중첩
    # =====================================================================
    tr = S[S.yr <= 2020]; va = S[S.yr >= 2021]
    out["split"] = {"train": {"r20": desc(tr["r20"], tr["mfe20"], tr["mae20"]),
                              "r40": desc(tr["r40"]), "ex20": desc(tr["ex20"]),
                              "kospi20": float(tr["k20"].mean())},
                    "valid": {"r20": desc(va["r20"], va["mfe20"], va["mae20"]),
                              "r40": desc(va["r40"]), "ex20": desc(va["ex20"]),
                              "kospi20": float(va["k20"].mean())}}
    boot = {"all20": block_bootstrap(S["r20"].to_numpy(), S["entry_month"].to_numpy()),
            "valid20": block_bootstrap(va["r20"].to_numpy(), va["entry_month"].to_numpy()),
            "all40": block_bootstrap(S["r40"].to_numpy(), S["entry_month"].to_numpy()),
            "valid40": block_bootstrap(va["r40"].to_numpy(), va["entry_month"].to_numpy()),
            "ex20_all": block_bootstrap(S["ex20"].to_numpy(), S["entry_month"].to_numpy()),
            "ex20_valid": block_bootstrap(va["ex20"].to_numpy(), va["entry_month"].to_numpy())}
    # 연도 클러스터(11개) 부트스트랩
    rng = np.random.default_rng(11)
    yr_g = S.groupby(S.entry_date.astype("datetime64[ns]").dt.year)["r20"]
    ys, yn = yr_g.sum().to_numpy(), yr_g.count().to_numpy()
    draws = [ys[rng.integers(0, len(ys), len(ys))].sum() / yn[rng.integers(0, len(ys), len(ys))].sum() for _ in range(2000)]
    boot["year_cluster20"] = {"lo": float(np.percentile(draws, 2.5)), "hi": float(np.percentile(draws, 97.5)),
                              "blocks": int(len(ys))}
    # 비중첩 표본(종목별 28일 간격)
    keep = []
    last = {}
    for i, r in S.sort_values(["code", "entry_date"]).iterrows():
        d = r["entry_date"]
        if r["code"] in last and (d - last[r["code"]]).days < 28:
            continue
        last[r["code"]] = d
        keep.append(i)
    Sx = S.loc[keep]
    boot["nonoverlap"] = {"n": int(len(Sx)), "r20": desc(Sx["r20"]), "r40": desc(Sx["r40"]),
                          "valid_r20": desc(Sx[Sx.yr >= 2021]["r20"]),
                          "boot20": block_bootstrap(Sx["r20"].to_numpy(),
                                                    Sx["entry_month"].to_numpy())}
    out["bootstrap"] = boot

    # =====================================================================
    # 6-A. 분위(사후 정렬) — 사용자 지시 방식 (상위10/…/하위50)
    # =====================================================================
    def quant_tables(df, col):
        v = df[col]
        res = []
        for idxs, lab in buckets_by(v):
            sub = df.loc[idxs]
            hz = col[1:]
            d = desc(sub[col], sub[f"mfe{hz}"], sub[f"mae{hz}"])
            d["bucket"] = lab
            d["share_n"] = len(sub) / len(df)
            d["contrib"] = float(sub[col].sum() / len(df.dropna(subset=[col])))
            res.append(d)
        return res

    qt = {"h20": quant_tables(S, "r20"), "h40": quant_tables(S, "r40")}
    # 10분위
    dec = {}
    for col in ("r20", "r40"):
        v = S[col].dropna().sort_values(ascending=False)
        n = len(v)
        rows = []
        for k in range(10):
            seg = v.iloc[int(k * n / 10):int((k + 1) * n / 10)]
            d = desc(seg)
            d["decile"] = f"D{k+1}" if k < 9 else "D10"
            rows.append(d)
        dec[col] = rows
    qt["deciles"] = dec
    # 기여도 분해
    v20 = S["r20"].dropna()
    tot = v20.sum()
    srt = np.sort(v20)[::-1]
    n = len(srt)
    contrib = {}
    for cut in (0.05, 0.1, 0.2, 0.3, 0.5):
        top = srt[:int(round(n * cut))].sum()
        bot = srt[-int(round(n * cut)):].sum()
        contrib[f"top_{int(cut*100)}"] = {"sum": float(top), "share_of_total": float(top / tot),
                                           "mean_contrib": float(top / n)}
        contrib[f"bottom_{int(cut*100)}"] = {"sum": float(bot), "share_of_total": float(bot / tot),
                                             "mean_contrib": float(bot / n)}
    contrib["total_sum"] = float(tot)
    contrib["mean"] = float(v20.mean())
    contrib["pos_sum"] = float(v20[v20 > 0].sum())
    contrib["top20_share_of_pos"] = float(srt[:int(round(n * 0.2))].sum() / v20[v20 > 0].sum())
    contrib["top10_min"] = float(srt[int(round(n * 0.1)) - 1])
    contrib["top20_min"] = float(srt[int(round(n * 0.2)) - 1])
    contrib["top30_min"] = float(srt[int(round(n * 0.3)) - 1])
    qt["contrib"] = contrib
    # 작은 부분표본 예시
    subs = {"2022년(하락장)": S[S.entry_date.astype("datetime64[ns]").dt.year == 2022],
            "코스피≤MA20일 때": S[S.kospi_bull == 0],
            "학습 2016–2020": S[S.yr <= 2020],
            "시총 상위 절반": S[S.log_mktcap >= S.log_mktcap.median()]}
    qt["examples"] = {k: quant_tables(v, "r20") for k, v in subs.items() if len(v) > 30}
    out["quantile"] = qt

    # =====================================================================
    # 6-B. 분포 비교 (규칙 vs 무작위 vs 같은 날짜 무작위)
    # =====================================================================
    dist = {}
    for hz in (20, 40):
        rnd = R[f"r{hz}"]
        sig = S[f"r{hz}"].dropna()
        # 같은 날짜 대조: 신호와 같은 dnum 의 임의 진입 표본
        di = R[f"di{hz}"]
        order = np.argsort(di)
        di_s, r_s = di[order], rnd[order]
        uq, starts = np.unique(di_s, return_index=True)
        ends = np.append(starts[1:], len(di_s))
        mp = {}
        for u, a, b in zip(uq, starts, ends):
            mp[u] = (float(np.nanmean(r_s[a:b])), int(b - a))
        same = S[["dnum", f"r{hz}"]].copy()
        same["same_day_rand"] = same["dnum"].map(lambda d: mp.get(np.int32(d), (np.nan, 0))[0])
        diff = (same[f"r{hz}"] - same["same_day_rand"]).dropna()
        dist[str(hz)] = {
            "signal": desc(sig), "random": desc(rnd),
            "same_day_rand_mean": float(same["same_day_rand"].mean()),
            "signal_minus_same_day": desc(diff),
            "share_ge20_sig": float((sig >= 0.20).mean()), "share_ge20_rand": float((rnd >= 0.20).mean()),
            "share_ge30_sig": float((sig >= 0.30).mean()), "share_ge30_rand": float((rnd >= 0.30).mean()),
            "share_le_m20_sig": float((sig <= -0.20).mean()), "share_le_m20_rand": float((rnd <= -0.20).mean()),
            "decile_sig": [float(np.percentile(sig, q)) for q in (5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 95)],
            "decile_rand": [float(np.percentile(rnd, q)) for q in (5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 95)],
        }
    out["distribution"] = dist

    # =====================================================================
    # 7. 사전 예측 — 규칙 신호 안에서 상위 분위를 미리 고를 수 있는가
    # =====================================================================
    tr_m = S.yr <= 2020
    thr20 = S.loc[tr_m, "r20"].quantile(0.9)
    thr20_30 = S.loc[tr_m, "r20"].quantile(0.7)
    out["exante_rule"] = {"thr_top10_train": float(thr20), "thr_top30_train": float(thr20_30)}
    uni_rows = []
    for f in ALL_F:
        a_all = auc(S[f], S["r20"] >= thr20)
        a_tr = auc(S.loc[tr_m, f], (S.loc[tr_m, "r20"] >= thr20))
        a_va = auc(S.loc[~tr_m, f], (S.loc[~tr_m, "r20"] >= thr20))
        # 특징 5분위별 평균 r20 (검증 구간)
        q = pd.qcut(S[f], 5, duplicates="drop", labels=False)
        quint = [desc(S.loc[(q == k) & (~tr_m), "r20"]).get("mean") for k in range(5)]
        uni_rows.append({"feat": f, "label": LAB[f], "auc_all": a_all, "auc_train": a_tr,
                         "auc_valid": a_va, "quint_valid": quint})
    uni_rows.sort(key=lambda r: -abs((r["auc_all"] or 0.5) - 0.5))
    out["exante_rule"]["univariate"] = uni_rows

    def fit_logit(feats, df_tr, y_tr, df_va=None):
        sc = StandardScaler().fit(df_tr[feats].fillna(0))
        Xtr = sc.transform(df_tr[feats].fillna(0))
        m = LogisticRegression(max_iter=2000).fit(Xtr, y_tr)
        res = {"coef": dict(zip(feats, m.coef_[0].round(3).tolist()))}
        for nm, dd in (("train", df_tr), ("valid", df_va)):
            if dd is None or len(dd) == 0:
                continue
            p = m.predict_proba(sc.transform(dd[feats].fillna(0)))[:, 1]
            res[nm + "_auc"] = auc(p, dd["y_target"]) if "y_target" in dd else None
            res[nm + "_p"] = p
        return res, sc, m

    # 규칙 신호 위 필터: 학습 상위 특징 3개로 상위 10% 예측
    cand = [r["feat"] for r in uni_rows[:8]]
    S2 = S.copy()
    S2["y_target"] = (S2["r20"] >= thr20).astype(int)
    S2["y_target30"] = (S2["r20"] >= thr20_30).astype(int)
    res, sc, mdl = fit_logit(cand[:4], S2[tr_m].dropna(subset=["y_target"]),
                             S2.loc[tr_m].dropna(subset=["y_target"])["y_target"],
                             S2[~tr_m].dropna(subset=["y_target"]))
    p_tr = mdl.predict_proba(sc.transform(S2.loc[tr_m, cand[:4]].fillna(0)))[:, 1]
    cutoff = np.quantile(p_tr, 0.9)
    p_va = mdl.predict_proba(sc.transform(S2.loc[~tr_m, cand[:4]].fillna(0)))[:, 1]
    sel = S2.loc[~tr_m].assign(p=p_va)
    hit = sel[sel.p >= cutoff]
    out["exante_rule"]["model"] = {
        "feats": cand[:4], "coef": res["coef"], "valid_auc": res["valid_auc"],
        "cutoff": float(cutoff), "n_selected": int(len(hit)), "n_valid": int((~tr_m).sum()),
        "precision": float(hit["y_target"].mean()) if len(hit) else None,
        "recall": float(hit["y_target"].sum() / S2.loc[~tr_m, "y_target"].sum()) if len(hit) else None,
        "sel_r20": desc(hit["r20"]) if len(hit) else None,
        "rest_r20": desc(sel[sel.p < cutoff]["r20"]) if len(hit) else None,
        "sel_r40": desc(hit["r40"]) if len(hit) else None,
        "sel_by_year": {str(k): {"n": int(len(g2)), "mean": float(g2["r20"].mean())}
                        for k, g2 in hit.groupby(hit.entry_date.astype("datetime64[ns]").dt.year)},
    }

    # =====================================================================
    # 8. 사용자 지적 검증 — 월봉 +20% 사건의 세 가지 진입 시점
    # =====================================================================
    E2 = E[E.uni == 1].copy()
    ms = E2[E2.kind == "monthstart"]
    pre = E2[(E2.kind == "pre") & (E2.event == 1)]
    cf_all = E2[E2.kind == "confirm"]
    cf = cf_all[cf_all.event == 1]
    ev_ms = ms[ms.event == 1]
    timing = {
        "months_total": int(len(ms)),
        "event_months": int(len(ev_ms)),
        "event_share": float(len(ev_ms) / len(ms)),
        "monthstart_all": desc(ms["r20"], ms["mfe20"], ms["mae20"]),
        "monthstart_event": desc(ev_ms["r20"], ev_ms["mfe20"], ev_ms["mae20"]),
        "monthstart_event_monthend": desc(ev_ms["r_monthend"]),
        "pre_pool": desc(pre["r20"], pre["mfe20"], pre["mae20"]),
        "pre_by_offset": {str(o): desc(pre[pre.offset == o]["r20"]) for o in range(1, 11)},
        "confirm": desc(cf["r20"], cf["mfe20"], cf["mae20"]),
        "confirm_realtime": desc(cf_all["r20"], cf_all["mfe20"], cf_all["mae20"]),
        "confirm_realtime_monthend": desc(cf_all["r_monthend"]),
        "confirm_realtime_share_event": float(cf_all["event"].mean()),
        "confirm_monthend": desc(cf["r_monthend"]),
        "confirm_days": desc(cf["days_to_hit"]),
        "panel_all": desc(P["r20"]),
        "rule": desc(S["r20"], S["mfe20"], S["mae20"]),
    }
    # 사건 시작 전 진입(사후 선택) vs 확인 후 진입(실시간) 의 전진 20일
    # 무작위 대조: 같은 날짜의 전 종목 평균
    timing["interpretation"] = {
        "monthstart_event_minus_all": timing["monthstart_event"]["mean"] - timing["monthstart_all"]["mean"],
        "confirm_minus_monthstart_all": timing["confirm"]["mean"] - timing["monthstart_all"]["mean"],
        "confirm_realtime_minus_all": timing["confirm_realtime"]["mean"] - timing["monthstart_all"]["mean"],
        "pre_minus_monthstart_all": timing["pre_pool"]["mean"] - timing["monthstart_all"]["mean"],
    }
    # 월봉 +20% 중 확인 후에도 남는 상승분 (확인 진입 → 월말)
    timing["after_confirm_month_return"] = desc(cf["r_monthend"])
    out["surge_timing"] = timing

    # =====================================================================
    # 9. C — 급등 사건 진입표본에서 상위 20% vs 하위 20%
    # =====================================================================
    def top_bottom(df, col="r20", frac=0.2, feats=None):
        v = df[col].dropna()
        thr_hi, thr_lo = v.quantile(1 - frac), v.quantile(frac)
        top = df.loc[v[v >= thr_hi].index]
        bot = df.loc[v[v <= thr_lo].index]
        rows = []
        for f in (feats or ALL_F):
            if f not in df:
                continue
            a, b = top[f].dropna(), bot[f].dropna()
            if len(a) < 10 or len(b) < 10:
                continue
            sd = np.sqrt((a.var(ddof=1) + b.var(ddof=1)) / 2)
            d_std = (a.mean() - b.mean()) / sd if sd > 0 else np.nan
            y = pd.Series(0, index=df.index)
            y.loc[top.index] = 1
            y.loc[bot.index] = 0
            a_auc = auc(df.loc[y.index, f], y)
            rows.append({"feat": f, "label": LAB.get(f, f), "top": float(a.mean()), "bottom": float(b.mean()),
                         "diff": float(a.mean() - b.mean()), "d": float(d_std), "auc": a_auc})
        rows.sort(key=lambda r: -abs((r["auc"] or 0.5) - 0.5))
        return {"n_top": int(len(top)), "n_bottom": int(len(bot)), "thr_hi": float(thr_hi),
                "thr_lo": float(thr_lo), "rows": rows,
                "top": desc(top[col], top["mfe20"], top["mae20"]), "bottom": desc(bot[col], bot["mfe20"], bot["mae20"])}

    out["C_surge_topbottom"] = {
        "pooled": top_bottom(pre),
        "fixed_offset5": top_bottom(pre[pre.offset == 5]),
    }
    # 규칙 신호에서도 같은 비교
    out["C_rule_topbottom"] = top_bottom(S.dropna(subset=["r20"]))

    # =====================================================================
    # 10. D — 예측 규칙 (학습 2016–2020 사건표본 → 검증 2021–2026 + 패널 배포)
    # =====================================================================
    pre2 = pre.dropna(subset=["r20"]).copy()
    pre_tr = pre2[pre2.yr <= 2020]
    pre_va = pre2[pre2.yr >= 2021]
    c_rows = [r for r in out["C_surge_topbottom"]["pooled"]["rows"] if r["feat"] != "offset"]
    picked = [r["feat"] for r in c_rows[:6]]
    # 학습 구간에서만 다시 AUC 순위를 매긴다(검증 정보 누출 방지)
    trc = []
    for f in picked:
        y = (pre_tr["r20"] >= pre_tr["r20"].quantile(0.8)).astype(int)
        trc.append((f, abs((auc(pre_tr[f], y) or 0.5) - 0.5)))
    trc.sort(key=lambda x: -x[1])
    feats_d = [f for f, _ in trc[:4]]
    thrD = float(pre_tr["r20"].quantile(0.8))
    ptr = pre_tr.assign(y=(pre_tr["r20"] >= thrD).astype(int))
    pva = pre_va.assign(y=(pre_va["r20"] >= thrD).astype(int))
    sc2 = StandardScaler().fit(ptr[feats_d].fillna(0))
    mdl2 = LogisticRegression(max_iter=2000).fit(sc2.transform(ptr[feats_d].fillna(0)), ptr["y"])
    pva_p = mdl2.predict_proba(sc2.transform(pva[feats_d].fillna(0)))[:, 1]
    cut2 = float(np.quantile(mdl2.predict_proba(sc2.transform(ptr[feats_d].fillna(0)))[:, 1], 0.8))
    pva2 = pva.assign(p=pva_p)
    sel2 = pva2[pva2.p >= cut2]
    # 검증 사건표본 정밀도/리콜/AUC
    D = {"feats": feats_d, "coef": dict(zip(feats_d, mdl2.coef_[0].round(3).tolist())),
         "train_auc": auc(mdl2.predict_proba(sc2.transform(ptr[feats_d].fillna(0)))[:, 1], ptr["y"]),
         "valid_auc": auc(pva_p, pva["y"]),
         "cutoff": cut2,
         "event_precision": float(sel2["y"].mean()) if len(sel2) else None,
         "event_recall": float(sel2["y"].sum() / pva["y"].sum()) if len(sel2) else None,
         "event_sel_n": int(len(sel2)), "event_valid_n": int(len(pva)),
         "event_sel_r20": desc(sel2["r20"]) if len(sel2) else None,
         "event_rest_r20": desc(pva2[pva2.p < cut2]["r20"])}
    # 배포: 전 유니버스 패널(검증 구간)에 적용
    Pva = P[P.yr >= 2021].dropna(subset=["r20"]).copy()
    pv_p = mdl2.predict_proba(sc2.transform(Pva[feats_d].fillna(0)))[:, 1]
    Pva = Pva.assign(p=pv_p)
    buy = Pva[Pva.p >= cut2]
    D["panel_valid_n"] = int(len(Pva))
    D["panel_buy_n"] = int(len(buy))
    D["panel_buy_rate"] = float(len(buy) / len(Pva))
    D["panel_buy_r20"] = desc(buy["r20"])
    D["panel_rest_r20"] = desc(Pva[Pva.p < cut2]["r20"])
    D["panel_buy_r40"] = desc(buy["r40"])
    D["panel_all_r20"] = desc(Pva["r20"])
    D["panel_by_year"] = {str(k): {"n": int(len(g2)), "mean": float(g2["r20"].mean()),
                                   "median": float(g2["r20"].median()),
                                   "win": float((g2["r20"] > 0).mean())}
                          for k, g2 in buy.groupby(buy.date.astype("datetime64[ns]").dt.year)}
    D["panel_boot"] = block_bootstrap(buy["r20"].to_numpy(),
                                      pd.to_datetime(buy["date"]).dt.to_period("M").astype(str).to_numpy())
    # 같은 특징으로 20일 상위 20% 확률 = 패널 전체 중 상위 20% 컷
    thr_panel = float(Pva["r20"].quantile(0.8))
    D["panel_top20_thr"] = thr_panel
    D["panel_buy_hit_top20"] = float((buy["r20"] >= thr_panel).mean())
    D["panel_base_hit_top20"] = float((Pva["r20"] >= thr_panel).mean())
    out["D_predict"] = D


    # =====================================================================
    # 12. 국면 필터 (사전에 알 수 있는 시장 상태) — 검증 분할·부트스트랩
    # =====================================================================
    def regime_block(name, mask):
        sub = S[mask]
        d = {"name": name, "n": int(len(sub)), "share": float(mask.mean()),
             "r20": desc(sub["r20"], sub["mfe20"], sub["mae20"]),
             "r40": desc(sub["r40"]),
             "ex20": desc(sub["ex20"]),
             "kospi20": float(sub["k20"].mean()),
             "train": desc(sub.loc[sub.yr <= 2020, "r20"]),
             "valid": desc(sub.loc[sub.yr >= 2021, "r20"]),
             "valid_r40": desc(sub.loc[sub.yr >= 2021, "r40"]),
             "valid_ex20": desc(sub.loc[sub.yr >= 2021, "ex20"]),
             "boot20": block_bootstrap(sub["r20"].to_numpy(), sub["entry_month"].to_numpy()),
             "boot_valid20": block_bootstrap(sub.loc[sub.yr >= 2021, "r20"].to_numpy(),
                                             sub.loc[sub.yr >= 2021, "entry_month"].to_numpy()),
             "by_year": {str(k): {"n": int(len(g2)), "mean": float(g2["r20"].mean()),
                                  "median": float(g2["r20"].median()),
                                  "win": float((g2["r20"] > 0).mean())}
                         for k, g2 in sub.groupby(sub.entry_date.astype("datetime64[ns]").dt.year)},
             "months": int(sub["entry_month"].nunique()),
             "sweep_best": None}
        # 이 국면에서 최고 TP/SL
        best = None
        for col in [c for c in sub.columns if c.startswith("s20_")]:
            dd = desc(sub[col])
            if dd.get("n", 0) < 200:
                continue
            if best is None or dd["mean"] > best[1]["mean"]:
                best = (col, dd)
        if best:
            d["sweep_best"] = {"col": best[0], **best[1]}
        return d

    bear = S["kospi_dd250"] <= -0.10
    dd8 = S["kospi_dd60"] <= -0.08
    out["regime"] = {
        "all": regime_block("전체", pd.Series(True, index=S.index)),
        "bear": regime_block("약세장(코스피 250일 고점 -10%↓)", bear),
        "notbear": regime_block("그 외(강세·횡보)", ~bear),
        "dd60_8": regime_block("코스피 60일 고점 -8%↓", dd8),
        "bear_bull": regime_block("약세장 & 코스피>MA20", bear & (S.kospi_bull > 0)),
        "bear_notbull": regime_block("약세장 & 코스피≤MA20", bear & (S.kospi_bull == 0)),
    }
    # 국면 에피소드 수(연속 월 묶음)
    for k in ("bear", "dd60_8"):
        ms = sorted(S.loc[out["regime"][k]["n"] and (bear if k == "bear" else dd8), "entry_month"].unique())
        eps, prev = 0, None
        for m_ in ms:
            p_ = pd.Period(m_, freq="M")
            if prev is None or (p_ - prev).n != 1:
                eps += 1
            prev = p_
        out["regime"][k]["episodes"] = eps

    # 연도 x 국면 교차
    out["regime_by_year"] = {}
    for y, g2 in S[S.yr >= 2016].groupby("yr"):
        out["regime_by_year"][str(y)] = {
            "n": int(len(g2)), "mean": float(g2["r20"].mean()), "median": float(g2["r20"].median()),
            "bear_n": int((g2["kospi_dd250"] <= -0.10).sum()),
            "bear_mean": float(g2.loc[g2["kospi_dd250"] <= -0.10, "r20"].mean()) if (g2["kospi_dd250"] <= -0.10).any() else None,
            "notbear_mean": float(g2.loc[g2["kospi_dd250"] > -0.10, "r20"].mean()) if (g2["kospi_dd250"] > -0.10).any() else None,
        }

    # =====================================================================
    # 11. 수동 검증용 표본 목록 (3종목)
    # =====================================================================
    picks = S.groupby("code").size().sort_values(ascending=False).head(3).index.tolist()
    out["manual_check_codes"] = picks

    with open(WORK / "ma20_ma60_tables.json", "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=lambda o: None)
    print("[write]", WORK / "ma20_ma60_tables.json")
    print("signals", len(S), "mean20", round(S.r20.mean() * 100, 2), "median20", round(S.r20.median() * 100, 2))
    print("valid mean20", round(va.r20.mean() * 100, 2), "median", round(va.r20.median() * 100, 2),
          "n", len(va))
    print("train mean20", round(tr.r20.mean() * 100, 2), "n", len(tr))
    print("dist: sig med", round(dist["20"]["signal"]["median"] * 100, 2),
          "rand med", round(dist["20"]["random"]["median"] * 100, 2))
    print("D panel buy", len(buy), "mean20", round(buy.r20.mean() * 100, 2) if len(buy) else None,
          "vs all", round(Pva.r20.mean() * 100, 2))
    print("surge: monthstart_all", round(timing["monthstart_all"]["mean"] * 100, 2),
          "event", round(timing["monthstart_event"]["mean"] * 100, 2),
          "pre", round(timing["pre_pool"]["mean"] * 100, 2),
          "confirm", round(timing["confirm"]["mean"] * 100, 2))


if __name__ == "__main__":
    main()
