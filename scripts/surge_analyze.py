#!/usr/bin/env python3
"""급등 사례 특징 → 유형별 성과·상위/하위 비교·시점별 분포 표를 JSON으로 만든다."""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

REPO = Path(__file__).resolve().parent.parent
WORK = REPO.parent / "work" / "backtest"
CASES = WORK / "surge_only_cases.pkl"
OUT = WORK / "surge_pattern_tables.json"
HOLDS = (20, 30, 40)


def pct(x):
    return None if x is None or not np.isfinite(x) else round(float(x) * 100, 1)


def stats(r):
    r = np.asarray(r, float)
    r = r[np.isfinite(r)]
    if r.size == 0:
        return {"n": 0}
    return {"n": int(r.size), "mean": float(r.mean()), "median": float(np.median(r)),
            "win": float((r > 0).mean()), "top10": float(np.percentile(r, 90)),
            "maxloss": float(r.min()), "sd": float(r.std(ddof=1)) if r.size > 1 else None}


def auc(x, y):
    """y=1(상위)인 x 값이 더 클 확률."""
    x = np.asarray(x, float)
    y = np.asarray(y, bool)
    ok = np.isfinite(x)
    x, y = x[ok], y[ok]
    if y.sum() == 0 or (~y).sum() == 0:
        return None
    from scipy.stats import rankdata
    r = rankdata(x)
    n1 = y.sum()
    n0 = (~y).sum()
    return float((r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def cohend(a, b):
    a = np.asarray(a, float); a = a[np.isfinite(a)]
    b = np.asarray(b, float); b = b[np.isfinite(b)]
    if a.size < 2 or b.size < 2:
        return None
    s = np.sqrt(((a.size - 1) * a.var(ddof=1) + (b.size - 1) * b.var(ddof=1)) / (a.size + b.size - 2))
    return float((a.mean() - b.mean()) / s) if s > 0 else None


def share(mask):
    m = pd.Series(mask)
    return {"n": int(m.sum()), "pct": round(float(m.mean()) * 100, 1)}


def main():
    df = pd.read_pickle(CASES)
    out = {"meta": {}}
    out["meta"] = {
        "cases": int(len(df)), "stocks": int(df["code"].nunique()),
        "months": f"{df['M'].min()} ~ {df['M'].max()}",
        "ret_mean": float(df["ret"].mean()), "ret_median": float(df["ret"].median()),
        "ret_min": float(df["ret"].min()), "ret_max": float(df["ret"].max()),
        "by_year": {str(k): int(v) for k, v in df["y"].value_counts().sort_index().items()} if "y" in df else {},
    }
    out["meta"]["by_year"] = {str(k): int(v) for k, v in df["M"].str[:4].value_counts().sort_index().items()}

    # --- 유형별 성과 (20/30/40) ---
    types = {}
    for t, g in df.groupby("type"):
        rec = {"n": int(len(g)), "share": round(len(g) / len(df) * 100, 1),
               "ret_mean": float(g["ret"].mean()), "ret_median": float(g["ret"].median()),
               "mktcap_median": float(g["mktcap"].median()),
               "holds": {}}
        for h in HOLDS:
            rec["holds"][str(h)] = stats(g[f"r{h}"])
        rec["after"] = {str(h): stats(g[f"r{h}_after"]) for h in HOLDS}
        types[t] = rec
    out["types"] = dict(sorted(types.items()))

    # --- 유형별 특징 프로파일 ---
    prof_cols = ["disp20", "disp60", "disp120", "d52w", "dh60", "win_ret", "win_range", "atr",
                 "v10_20", "v5_20", "vmax15", "pull_depth", "sl60_20", "ret10", "up_streak_max",
                 "n_bigyang", "n_gapup", "n_break", "mktcap"]
    prof = {}
    for t, g in df.groupby("type"):
        prof[t] = {c: float(g[c].median()) for c in prof_cols if c in g}
        prof[t].update({
            "align3": round(float(g["align3"].mean()) * 100, 1),
            "align5": round(float(g["align5"].mean()) * 100, 1),
            "above20": round(float(g["above20"].mean()) * 100, 1),
            "above60": round(float(g["above60"].mean()) * 100, 1),
            "above120": round(float(g["above120"].mean()) * 100, 1),
            "br_h250": round(float(g["br_h250"].notna().mean()) * 100, 1),
            "br_h120": round(float(g["br_h120"].notna().mean()) * 100, 1),
            "br_h60": round(float(g["br_h60"].notna().mean()) * 100, 1),
            "br_ma60": round(float(g["br_ma60"].notna().mean()) * 100, 1),
            "br_dtl": round(float(g["br_dtl"].notna().mean()) * 100, 1),
            "vshape": {k: round(v * 100, 1) for k, v in g["vshape"].value_counts(normalize=True).items()},
            "sl60_cls": {k: round(v * 100, 1) for k, v in g["sl60_cls"].value_counts(normalize=True).items()},
        })
    out["profiles"] = prof

    # --- 항목별 빈도/분포 (급등 표본 안에서만) ---
    freq = {}
    freq["align3"] = share(df["align3"])
    freq["align5"] = share(df["align5"])
    for k in (20, 60, 120):
        freq[f"above{k}"] = share(df[f"above{k}"])
    for k in ("br_ma20", "br_ma60", "br_ma120", "br_h60", "br_h120", "br_h250", "br_dtl"):
        freq[k] = share(df[k].notna())
    freq["vshape"] = {k: round(v * 100, 1) for k, v in df["vshape"].value_counts(normalize=True).items()}
    freq["sl60_cls"] = {k: round(v * 100, 1) for k, v in df["sl60_cls"].value_counts(normalize=True).items()}
    freq["pull_any"] = share((df["pull_depth"] <= -0.03) & (df["pull_days"] >= 2))
    freq["gap_up"] = share(df["n_gapup"] > 0)
    freq["bigyang"] = share(df["n_bigyang"] > 0)
    freq["lower_tail"] = share(df["n_lower_tail"] > 0)
    freq["nobreak"] = share(df["n_break"] == 0)
    quant = {}
    for c in ("disp20", "disp60", "disp120", "d52w", "win_ret", "win_range", "atr", "v10_20",
              "vmax15", "pull_depth", "ret10", "up_streak_max", "n_bigyang", "n_gapup", "sl60_20"):
        q = df[c].quantile([0.1, 0.25, 0.5, 0.75, 0.9])
        quant[c] = {f"q{int(k*100)}": float(v) for k, v in q.items()}
    out["freq"] = freq
    out["quantiles"] = quant

    # --- 시점별 (T-15 / T-10 / T-5 / T) 분포 ---
    snaps = {}
    labels = ["T-15", "T-10", "T-5", "T"]
    for i, lab in enumerate(labels):
        s = {}
        for key in ("disp20", "disp60", "disp120", "d52w", "v5_20", "v10_20", "ret10",
                    "win_range20", "pull"):
            v = [r[i].get(key) for r in df["snap"] if r and r[i]]
            v = [x for x in v if x is not None]
            s[key] = {"median": float(np.median(v)), "mean": float(np.mean(v))} if v else {}
        for key in ("above20", "above60", "above120", "align3"):
            v = [bool(r[i].get(key)) for r in df["snap"] if r and r[i]]
            s[key] = round(float(np.mean(v)) * 100, 1) if v else None
        for key in ("brs",):
            brk = {}
            for line in ("MA20", "MA60", "MA120", "60일고점", "52주신고가"):
                c = [line in (r[i].get("brs") or []) for r in df["snap"] if r and r[i]]
                brk[line] = round(float(np.mean(c)) * 100, 1) if c else None
            s["brk10"] = brk
        snaps[lab] = s
    out["snapshots"] = snaps

    # --- 상위 20% vs 하위 20% (급등 폭) ---
    q80, q20 = df["ret"].quantile(0.80), df["ret"].quantile(0.20)
    up = df[df["ret"] >= q80].copy()
    lo = df[df["ret"] <= q20].copy()
    cmp_rows = []
    feats = ["disp20", "disp60", "disp120", "d52w", "dh60", "win_ret", "win_range", "atr",
             "v10_20", "v5_20", "vmax15", "pull_depth", "sl60_20", "ret10", "up_streak_max",
             "n_bigyang", "n_gapup", "n_break", "mktcap"]
    for f in feats:
        a, b = up[f], lo[f]
        cmp_rows.append({"feature": f, "up_mean": float(a.mean()), "lo_mean": float(b.mean()),
                         "up_median": float(a.median()), "lo_median": float(b.median()),
                         "d": cohend(a, b), "auc": auc(df[f], df["ret"] >= q80)})
    for f in ("align3", "align5", "above20", "above60", "above120"):
        cmp_rows.append({"feature": f, "up_mean": float(up[f].mean()), "lo_mean": float(lo[f].mean()),
                         "up_median": None, "lo_median": None,
                         "d": cohend(up[f].astype(float), lo[f].astype(float)),
                         "auc": auc(df[f].astype(float), df["ret"] >= q80)})
    for f in ("br_h250", "br_h120", "br_h60", "br_ma60", "br_dtl"):
        u = up[f].notna().astype(float)
        l = lo[f].notna().astype(float)
        cmp_rows.append({"feature": f, "up_mean": float(u.mean()), "lo_mean": float(l.mean()),
                         "up_median": None, "lo_median": None, "d": cohend(u, l),
                         "auc": auc(df[f].notna().astype(float), df["ret"] >= q80)})
    for k in ("조용·마름", "점증", "단발폭발", "확산", "중립"):
        u = (up["vshape"] == k).astype(float); l = (lo["vshape"] == k).astype(float)
        cmp_rows.append({"feature": f"vshape={k}", "up_mean": float(u.mean()), "lo_mean": float(l.mean()),
                         "up_median": None, "lo_median": None, "d": cohend(u, l),
                         "auc": auc((df["vshape"] == k).astype(float), df["ret"] >= q80)})
    cmp_rows = sorted([c for c in cmp_rows if c["auc"] is not None], key=lambda c: -abs(c["auc"] - 0.5))
    out["upper_lower"] = {"q80": float(q80), "q20": float(q20), "n_up": int(len(up)), "n_lo": int(len(lo)),
                          "up_ret_median": float(up["ret"].median()), "lo_ret_median": float(lo["ret"].median()),
                          "rows": cmp_rows}
    # 상하위별 유형 구성
    out["upper_lower"]["type_mix"] = {
        "up": {k: round(v * 100, 1) for k, v in up["type"].value_counts(normalize=True).items()},
        "lo": {k: round(v * 100, 1) for k, v in lo["type"].value_counts(normalize=True).items()},
    }
    # 상하위 그룹 자체의 20/30/40 성과
    out["upper_lower"]["holds"] = {k: {str(h): stats(g[f"r{h}"]) for h in HOLDS}
                                   for k, g in (("up", up), ("lo", lo))}

    # 급등 크기 구간별 성과(참고)
    bins = [(0.20, 0.25), (0.25, 0.30), (0.30, 0.40), (0.40, 0.60), (0.60, 10)]
    seg = []
    for a, b in bins:
        g = df[(df["ret"] >= a) & (df["ret"] < b)]
        seg.append({"range": f"{a*100:.0f}~{b*100:.0f}%" if b < 10 else "60%+",
                    "n": int(len(g)), **{f"h{h}": stats(g[f"r{h}"]) for h in HOLDS}})
    out["ret_segments"] = seg

    # 시장/시총 구성
    out["mix"] = {
        "market": {k: round(v * 100, 1) for k, v in df["market"].value_counts(normalize=True).items()},
        "mktcap": {k: round(v * 100, 1) for k, v in df["mktcap_class"].value_counts(normalize=True).items()},
    }
    # 대표 사례 후보: 각 유형별 ret 상위 + 유형별 중앙값에 가까운 사례
    reps = {}
    for t, g in df.groupby("type"):
        g2 = g.sort_values("ret", ascending=False)
        med = g["ret"].median()
        near = g.iloc[(g["ret"] - med).abs().argsort()[:3]]
        reps[t] = {
            "big": [row for _, row in g2.head(4).iterrows()],
            "near_median": [row for _, row in near.iterrows()],
        }
    out["_df"] = None
    with open(OUT, "w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1, default=float)
    print(f"[tables] → {OUT}")
    print(json.dumps({k: out["types"][k]["n"] for k in out["types"]}, ensure_ascii=False))
    print("상위20%", len(up), "하위20%", len(lo), f"ret>={q80:.3f} / <={q20:.3f}")
    print(json.dumps(out["snapshots"]["T-10"], ensure_ascii=False, indent=1)[:1200])


if __name__ == "__main__":
    main()
