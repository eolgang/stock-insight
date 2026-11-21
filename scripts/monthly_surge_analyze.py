#!/usr/bin/env python3
"""monthly_surge collect 결과 분석 → 표(markdown) + JSON 산출."""
from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from monthly_surge import (OUT_ACC, OUT_MONTHLY, WORK, CACHE, YEARS, acc_stats, SURGE)

PARTS = WORK / "report_parts"
OUT_JSON = Path(__file__).resolve().parent.parent / "data" / "monthly-20-surge.json"


def pct(x, d=1):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    return f"{x*100:+.{d}f}%"


def pc(x, d=1):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    return f"{x*100:.{d}f}%"


def num(x, d=2):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    return f"{x:.{d}f}"


def zfmt(z):
    if z is None or not np.isfinite(z):
        return "—"
    return (f"{z:+.1f}σ" if abs(z) >= 2 else f"{z:+.1f}σ (우연 범위)")


def prop_stat(ev: pd.Series, ct: pd.Series) -> dict:
    ev = ev.dropna().astype(bool)
    ct = ct.dropna().astype(bool)
    n1, n0 = len(ev), len(ct)
    p1 = float(ev.mean()) if n1 else np.nan
    p0 = float(ct.mean()) if n0 else np.nan
    se = np.sqrt(p1 * (1 - p1) / max(n1, 1) + p0 * (1 - p0) / max(n0, 1))
    return {"n1": n1, "n0": n0, "p1": p1, "p0": p0, "diff": p1 - p0, "se": se,
            "z": (p1 - p0) / se if se > 0 else np.nan}


def mean_stat(ev: pd.Series, ct: pd.Series) -> dict:
    ev = ev.replace([np.inf, -np.inf], np.nan).dropna()
    ct = ct.replace([np.inf, -np.inf], np.nan).dropna()
    n1, n0 = len(ev), len(ct)
    m1 = float(ev.mean()) if n1 else np.nan
    m0 = float(ct.mean()) if n0 else np.nan
    s1 = float(ev.std(ddof=1)) if n1 > 1 else np.nan
    s0 = float(ct.std(ddof=1)) if n0 > 1 else np.nan
    se = np.sqrt((s1 ** 2) / max(n1, 1) + (s0 ** 2) / max(n0, 1))
    return {"n1": n1, "n0": n0, "m1": m1, "m0": m0, "diff": m1 - m0, "se": se,
            "z": (m1 - m0) / se if se and se > 0 else np.nan}


def md_table(headers, rows, align=None):
    align = align or ["---"] * len(headers)
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(align) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out) + "\n"


def write_part(name, text):
    PARTS.mkdir(parents=True, exist_ok=True)
    (PARTS / f"{name}.md").write_text(text, encoding="utf-8")


def run():
    m = pd.read_pickle(OUT_MONTHLY)
    acc = pickle.load(open(OUT_ACC, "rb"))
    m["ev"] = m["ret"] >= SURGE
    m["base"] = m["ev"].mean()
    base = float(m["ev"].mean())
    m["size"] = m.groupby("M")["mktcap"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 3, labels=["소형", "중형", "대형"]))
    # 10일 창 플래그
    for n, d in [("20", "ds20"), ("60", "ds60"), ("120", "ds120"), ("240", "ds240")]:
        m[f"w{n}"] = (m[d] <= 10).astype(float)
    for n, d in [("60", "dsh60"), ("120", "dsh120"), ("250", "dsh250")]:
        m[f"wh{n}"] = (m[d] <= 10).astype(float)
    # 과거 12개월 내 20% 달 이력
    m = m.sort_values(["code", "M"]).reset_index(drop=True)
    hist = []
    for code, g in m.groupby("code", sort=False):
        evh = g["ev"].to_numpy()
        out = np.zeros(len(g))
        for i in range(len(g)):
            out[i] = evh[max(0, i - 12):i].any()
        hist.append(pd.Series(out, index=g.index))
    m["past20"] = pd.concat(hist).sort_index()
    J = {}

    # === A1 ===
    n_all, n_ev = len(m), int(m["ev"].sum())
    by_year = m.groupby("y").agg(n=("ev", "size"), ev=("ev", "sum"), ret=("ret", "mean"))
    by_year["rate"] = by_year["ev"] / by_year["n"]
    rows = [[int(y), int(r.n), int(r.ev), pc(r.rate), pct(r.ret)] for y, r in by_year.iterrows()]
    t_dist = md_table(["연도", "관측(종목-월)", "20% 급등", "발생률", "평균 월수익률"], rows,
                      ["---:", "---:", "---:", "---:", "---:"])
    top_m = m.groupby("M").agg(n=("ev", "size"), ev=("ev", "sum"))
    top_m["rate"] = top_m["ev"] / top_m["n"]
    tm = top_m.sort_values("rate", ascending=False).head(12)
    t_dist += ("\n**발생률 상위 12개월**\n\n" + md_table(
        ["월", "관측", "20% 급등", "발생률"],
        [[i, int(r.n), int(r.ev), pc(r.rate)] for i, r in tm.iterrows()],
        ["---:", "---:", "---:", "---:"]))
    write_part("a1_dist", t_dist)
    J["dist"] = {"n_all": n_all, "n_ev": n_ev, "rate": base, "n_stocks": int(m.code.nunique()),
                 "obs_per_month": float(m.groupby("M").size().mean()),
                 "by_year": by_year.reset_index().to_dict("records")}

    # === A2 ===
    BIN = [("align3", "정배열 MA5>20>60"), ("align5", "완전정배열 5>20>60>120>240"),
           ("w20", "10일내 MA20 상향돌파"), ("w60", "10일내 MA60 상향돌파"),
           ("w120", "10일내 MA120 상향돌파"), ("w240", "10일내 MA240 상향돌파"),
           ("wh60", "10일내 60일 고점 돌파"), ("wh120", "10일내 120일 고점 돌파"),
           ("wh250", "10일내 52주 신고가"), ("big10", "10일내 장대양봉"),
           ("gap10", "10일내 갭상승"), ("bull", "코스피 > 코스피 MA20"),
           ("past20", "직전 12개월 내 20% 달 이력")]
    rows, zs = [], {}
    for col, lab in BIN:
        s = prop_stat(m.loc[m.ev, col], m[col])
        ncond = int(m[col].sum())
        pcond = float(m.loc[m[col] == 1, "ev"].mean()) if ncond else np.nan
        rows.append([lab, pc(s["p1"]), pc(s["p0"]), f"{s['diff']*100:+.1f}%p", zfmt(s["z"]),
                     ncond, pc(pcond), f"{pcond/base:.2f}배"])
        zs[col] = s
    for cond, lab in [("sl20_20", "MA20 20일 기울기 > 0"), ("sl60_20", "MA60 20일 기울기 > 0")]:
        f = (m[cond] > 0)
        s = prop_stat(f[m.ev], f)
        ncond = int(f.sum())
        pcond = float(m.loc[f, "ev"].mean())
        rows.append([lab, pc(s["p1"]), pc(s["p0"]), f"{s['diff']*100:+.1f}%p", zfmt(s["z"]),
                     ncond, pc(pcond), f"{pcond/base:.2f}배"])
        zs[cond] = s
    for sz in ["대형", "중형", "소형"]:
        f = (m["size"] == sz)
        s = prop_stat(f[m.ev], f)
        ncond = int(f.sum())
        pcond = float(m.loc[f, "ev"].mean())
        rows.append([f"시총 {sz}", pc(s["p1"]), pc(s["p0"]), f"{s['diff']*100:+.1f}%p", zfmt(s["z"]),
                     ncond, pc(pcond), f"{pcond/base:.2f}배"])
        zs["size_" + sz] = s
    for mk in ["KOSPI", "KOSDAQ"]:
        f = (m["market"] == mk)
        s = prop_stat(f[m.ev], f)
        ncond = int(f.sum())
        pcond = float(m.loc[f, "ev"].mean())
        rows.append([mk, pc(s["p1"]), pc(s["p0"]), f"{s['diff']*100:+.1f}%p", zfmt(s["z"]),
                     ncond, pc(pcond), f"{pcond/base:.2f}배"])
        zs["mk_" + mk] = s
    t_bin = md_table(["특징(비율)", "사건 집단", "통제집단", "차이", "z", "n(조건 충족)",
                      "P(급등) 조건부", "기준선 대비"], rows,
                     ["---", "---:", "---:", "---:", "---:", "---:", "---:", "---:"])

    CONT = [("disp5", "MA5 이격도(%)", 100), ("disp20", "MA20 이격도(%)", 100),
            ("disp60", "MA60 이격도(%)", 100), ("disp120", "MA120 이격도(%)", 100),
            ("disp240", "MA240 이격도(%)", 100), ("d52w", "52주 신고가 대비(%)", 100),
            ("v520", "거래량 5일/20일", 1), ("v10_20", "거래량 10일/20일", 1),
            ("vmax10", "10일 최대 거래량 배수", 1), ("t520", "거래대금 5일/20일", 1),
            ("tmax10", "10일 최대 거래대금 배수", 1), ("bbwpct", "볼린저폭 1년 백분위", 1),
            ("atr14", "ATR14/종가(%)", 100), ("tr20", "20일 TR/종가(%)", 100),
            ("box20", "20일 박스폭(%)", 100), ("box60", "60일 박스폭(%)", 100),
            ("box120", "120일 박스폭(%)", 100), ("dh60", "60일 최고가 대비(%)", 100),
            ("dd120", "120일 최고가 대비(%)", 100), ("rb60", "60일 최저 대비(%)", 100),
            ("yang10", "10일 양봉 비율", 1), ("ret10", "최근 10일 수익률(%)", 100),
            ("vol_ud", "상승일/하락일 거래량", 1),
            ("f5", "외국인 소지율 5일 변화(%p)", 1), ("f20", "외국인 소지율 20일 변화(%p)", 1)]
    rows2 = []
    for col, lab, mul in CONT:
        s = mean_stat(m.loc[m.ev, col] * mul, m[col] * mul)
        rows2.append([lab, num(s["m1"]), num(s["m0"]), num(s["diff"]), zfmt(s["z"]),
                      s["n1"], s["n0"]])
        zs[col] = s
    t_cont = md_table(["특징(평균)", "사건 집단", "통제집단", "차이", "z", "n(사건)", "n(통제)"], rows2,
                      ["---", "---:", "---:", "---:", "---:", "---:", "---:"])
    write_part("a2_features", t_bin + "\n" + t_cont)

    sig_list = [(k, v) for k, v in zs.items() if np.isfinite(v["z"]) and abs(v["z"]) >= 2]
    sig_list.sort(key=lambda t: -abs(t[1]["z"]))
    J["features"] = {k: {kk: vv for kk, vv in v.items()} for k, v in zs.items()}
    J["n_features_tested"] = len(zs)
    J["n_features_sig"] = len(sig_list)
    J["base_rate"] = base

    # A2b 구간표
    def bucket(col, edges, labels, mul=1, title=None):
        rows = []
        for i, lab in enumerate(labels):
            lo, hi = edges[i], edges[i + 1]
            sub = m[(m[col] >= lo) & (m[col] < hi)]
            if len(sub) == 0:
                continue
            rows.append([lab, len(sub), pc(sub["ev"].mean()), f"{sub['ev'].mean()/base:.2f}배",
                         pct(sub["ret"].mean())])
        return md_table([title or col, "n", "P(급등)", "기준선 대비", "평균 월수익"], rows,
                        ["---", "---:", "---:", "---:", "---:"])

    buckets = []
    buckets.append(("**MA20 이격도**\n\n" + bucket(
        "disp20", [-9, -0.05, 0, 0.03, 0.07, 9], ["−5% 이하", "−5~0%", "0~+3%", "+3~+7%", "+7% 이상"])))
    buckets.append(("**ATR14/종가(변동성)**\n\n" + bucket(
        "atr14", [0, 0.03, 0.045, 0.06, 0.09, 9],
        ["3% 미만", "3~4.5%", "4.5~6%", "6~9%", "9% 이상"])))
    buckets.append(("**볼린저밴드 폭 1년 백분위**\n\n" + bucket(
        "bbwpct", [0, 0.2, 0.4, 0.6, 0.8, 1.01],
        ["하위 20%", "20~40%", "40~60%", "60~80%", "상위 20%"])))
    buckets.append(("**52주 신고가 대비**\n\n" + bucket(
        "d52w", [-1, -0.4, -0.25, -0.12, -0.04, 0.001],
        ["−40% 이하", "−40~−25%", "−25~−12%", "−12~−4%", "−4% 이내(신고가 부근)"])))
    buckets.append(("**최근 10일 수익률**\n\n" + bucket(
        "ret10", [-9, -0.05, 0, 0.05, 0.12, 9],
        ["−5% 이하", "−5~0%", "+0~+5%", "+5~+12%", "+12% 이상"])))
    write_part("a2b_buckets", "\n".join(buckets))

    # === A3 5구간 ===
    bins = [(-9, -0.20, "−20% 이하"), (-0.20, 0.0, "−20~0%"), (0.0, 0.10, "0~+10%"),
            (0.10, 0.20, "+10~+20%"), (0.20, 9, "+20% 이상")]
    cols = [("disp20", 100, "MA20이격%"), ("d52w", 100, "52주고점대비%"), ("align3", None, "정배열%"),
            ("w60", None, "10일내 MA60돌파%"), ("wh250", None, "10일내 52주신고가%"),
            ("wh60", None, "10일내 60일고점%"), ("vmax10", 1, "최대거래량배수"),
            ("bbwpct", 1, "BB폭백분위"), ("atr14", 100, "ATR%"), ("dd120", 100, "120일고점대비%"),
            ("ret10", 100, "10일수익%"), ("vol_ud", 1, "상승/하락거래량")]
    rows3 = []
    for lo, hi, lab in bins:
        sub = m[(m.ret > lo) & (m.ret <= hi)]
        r = [lab, len(sub)]
        for c, mul, _ in cols:
            v = sub[c].mean()
            r.append(pc(v) if mul is None else num(float(v) * mul))
        rows3.append(r)
    t_b = md_table(["월 수익률 구간", "n"] + [c[2] for c in cols], rows3,
                   ["---", "---:"] + ["---:"] * len(cols))
    write_part("a3_buckets", t_b)

    # === A4 최초 사건만 ===
    first = m.sort_values(["code", "M"]).groupby("code", group_keys=False).apply(
        lambda g: g[g.ev].head(1))
    keyfeat = [("align3", "정배열", 0), ("align5", "완전정배열", 0), ("w60", "10일내 MA60 돌파", 0),
               ("wh250", "10일내 52주 신고가", 0), ("wh60", "10일내 60일고점", 0),
               ("dd120", "120일고점대비%", 1), ("vmax10", "최대거래량배수", 2),
               ("bbwpct", "BB폭 백분위", 2), ("disp20", "MA20이격%", 1)]
    rows4 = []
    for c, lab, kind in keyfeat:
        if kind == 0:
            rows4.append([lab, pc(first[c].mean()), pc(m.loc[m.ev, c].mean()), pc(m[c].mean())])
        else:
            rows4.append([lab, num(first[c].mean() * (100 if kind == 1 else 1)),
                          num(m.loc[m.ev, c].mean() * (100 if kind == 1 else 1)),
                          num(m[c].mean() * (100 if kind == 1 else 1))])
    t_f = md_table(["특징", "종목당 최초 사건만", "전체 사건", "통제집단(전체)"], rows4,
                   ["---", "---:", "---:", "---:"])
    t_f = (f"종목 수 {first.code.nunique()}개 · 최초 사건 {len(first)}건 "
           f"(전체 사건 {n_ev}건, 사건의 {len(first)/n_ev:.0%}가 종목별 첫 사건)\n\n") + t_f
    write_part("a4_first", t_f)
    J["first_only"] = {"n_events": int(len(first)), "n_stocks": int(first.code.nunique())}

    # === A5 돌파선 ===
    lines = [("w20", "ds20", "MA20"), ("w60", "ds60", "MA60"), ("w120", "ds120", "MA120"),
             ("w240", "ds240", "MA240"), ("wh60", "dsh60", "직전 60일 고점"),
             ("wh120", "dsh120", "직전 120일 고점"), ("wh250", "dsh250", "52주 신고가")]
    rows5 = []
    for xc, dsc, lab in lines:
        s = prop_stat(m.loc[m.ev, xc], m[xc])
        sub = m.loc[m[xc] == 1, dsc]
        nd = len(sub)
        b13 = ((sub >= 1) & (sub <= 3)).mean() if nd else np.nan
        b47 = ((sub >= 4) & (sub <= 7)).mean() if nd else np.nan
        b810 = ((sub >= 8) & (sub <= 10)).mean() if nd else np.nan
        pcond = float(m.loc[m[xc] == 1, "ev"].mean()) if nd else np.nan
        rows5.append([lab, pc(s["p1"]), pc(s["p0"]), f"{s['diff']*100:+.1f}%p", zfmt(s["z"]),
                      nd, pc(pcond), f"{pcond/base:.2f}배", pc(b13), pc(b47), pc(b810)])
    t_l = md_table(["넘은 선", "사건 집단", "통제집단", "차이", "z", "n(돌파)",
                    "P(급등) 돌파시", "기준선 대비", "1~3일 전", "4~7일 전", "8~10일 전"], rows5,
                   ["---", "---:", "---:", "---:", "---:", "---:", "---:", "---:", "---:", "---:", "---:"])
    cols_all = [c for c, _, _ in lines]
    m["n_break"] = m[cols_all].sum(axis=1)
    sub = m.groupby("n_break").agg(n=("ev", "size"), ev=("ev", "sum"))
    sub["rate"] = sub.ev / sub.n
    t_l += ("\n**10일 안에 넘은 선의 개수(7개 선 기준)**\n\n" + md_table(
        ["넘은 선 개수", "n", "20% 급등", "발생률"],
        [[int(i), int(r.n), int(r.ev), pc(r.rate)] for i, r in sub.iterrows()],
        ["---:", "---:", "---:", "---:"]))
    write_part("a5_lines", t_l)

    # === A6 거래량 ===
    def bucket_table(col, edges, labels, title_col="구간"):
        rows = []
        for i, lab in enumerate(labels):
            lo, hi = edges[i], edges[i + 1]
            sub = m[(m[col] >= lo) & (m[col] < hi)]
            evs = sub[sub.ev]
            rows.append([lab, len(sub), pc(len(sub) / n_all), len(evs),
                         pc(len(evs) / n_ev) if n_ev else "—",
                         pc(len(evs) / len(sub)) if len(sub) else "—",
                         pct(sub.ret.mean())])
        return md_table([title_col, "n(전체)", "통제집단 비중", "20% 급등 n", "사건 집단 비중",
                         "P(급등)", "평균 월수익"], rows,
                        ["---", "---:", "---:", "---:", "---:", "---:", "---:"])

    t_v1 = bucket_table("vmax10", [0, 1.0, 1.5, 2.0, 3.0, 5.0, 99],
                        ["1.0배 미만", "1.0~1.5배", "1.5~2.0배", "2.0~3.0배", "3.0~5.0배", "5배 이상"],
                        "10일 최대 거래량/20일 평균")
    t_v1 += (f"\n- 10일 안에 20일 평균의 **2배 이상** 거래량: 사건 집단 "
             f"{pc((m.loc[m.ev,'vmax10']>=2).mean())} vs 통제 {pc((m['vmax10']>=2).mean())}\n")

    rows = []
    pats = [("vp_gradual", "점증형(3일>5일>10일, 1.2배↑)"), ("vp_burst", "단발 폭발형(3배 1회)"),
            ("vp_quiet", "조용한 매집형(−3~3%·1.5배↑)"),
            ("vp_quiet2", "조용한 매집형 넓게(−5~5%·1.3배↑)"),
            ("vp_volup", "거래량 동반 상승(+5%·2배↑)"), ("vp_novol", "거래량 없는 상승(1배 이하)")]
    for c, lab in pats:
        s = prop_stat(m.loc[m.ev, c], m[c])
        sub = m[m[c] == 1]
        rows.append([lab, pc(s["p1"]), pc(s["p0"]), f"{s['diff']*100:+.1f}%p", zfmt(s["z"]),
                     len(sub), pc(len(sub[sub.ev]) / len(sub)) if len(sub) else "—",
                     pct(sub.ret.mean()) if len(sub) else "—"])
    t_v2 = md_table(["거래량 패턴", "사건 집단", "통제집단", "차이", "z", "n(패턴)",
                     "P(급등)", "평균 월수익"], rows,
                    ["---", "---:", "---:", "---:", "---:", "---:", "---:", "---:"])

    vr_cols = [f"vr_m{k}" for k in range(10, -1, -1)]
    rows = []
    for c in vr_cols:
        s = mean_stat(m.loc[m.ev, c], m[c])
        rows.append([c.replace("vr_m", "T−").replace("T−0", "T"), num(s["m1"]), num(s["m0"]),
                     num(s["diff"]), zfmt(s["z"])])
    t_v3 = md_table(["시점", "사건 집단 평균 배수", "통제집단", "차이", "z"], rows,
                    ["---", "---:", "---:", "---:", "---:"])

    t_v4 = bucket_table("t20", [1e10, 3e10, 1e11, 3e11, 1e13],
                        ["100~300억", "300~1000억", "1000~3000억", "3000억 이상"], "20일 평균 거래대금")
    t_v5 = bucket_table("vol_ud", [0, 0.5, 0.8, 1.2, 2.0, 99],
                        ["0.5 미만", "0.5~0.8", "0.8~1.2", "1.2~2.0", "2.0 이상"],
                        "상승일/하락일 거래량(20일)")
    write_part("a6a_mult", t_v1)
    write_part("a6b_pattern", t_v2)
    write_part("a6c_time", t_v3)
    write_part("a6d_size", t_v4 + "\n" + t_v5)
    J["volume"] = {"patterns": {k: {"p_ev": float(prop_stat(m.loc[m.ev, k], m[k])["p1"]),
                                    "p_ct": float(prop_stat(m.loc[m.ev, k], m[k])["p0"]),
                                    "p_cond": float(m.loc[m[k] == 1, "ev"].mean()),
                                    "n": int((m[k] == 1).sum())} for k, _ in pats}}

    # === A7 유형 ===
    N = m.shape[0]

    def types_of(df):
        t = pd.Series("T7_기타", index=df.index)
        cond = [
            ("T3 낙폭과대 반전", (df.dd120 <= -0.25) & (df.rb60 >= 0.10) & (df.disp20 > 0)),
            ("T1 52주 신고가·고점 근접", (df.d52w > -0.05) | (df.wh250 == 1)),
            ("T2 박스 수축 후 상단 접근", (df.bbwpct < 0.40) & (df.t520 >= 1.5)),
            ("T4 역배열 탈출", (df.w60 == 1) & (df.sl60_20 < 0)),
            ("T5 정배열 순항", (df.align5 == 1) & (df.disp20 < 0.05) & (df.disp20 > -0.02)),
            ("T6 거래대금 폭발", (df.tmax10 >= 3.0) & (df.v520 >= 1.5)),
        ]
        for lab, c in cond:
            t[(t == "T7_기타") & c.fillna(False)] = lab
        return t

    m["type"] = types_of(m)
    g = m.groupby("type").agg(n=("ev", "size"), ev=("ev", "sum"), ret=("ret", "mean"),
                              med=("ret", "median"))
    g["rate"] = g.ev / g.n
    rows = []
    for lab, r in g.sort_values("rate", ascending=False).iterrows():
        rows.append([lab, pc(r.ev / n_ev), pc(r.n / n_all), pc(r.rate), f"{r.rate/base:.2f}배",
                     int(r.ev), int(r.n), pct(r["ret"]), pct(r["med"])])
    t_ty = md_table(["유형(중복 없이 배타 배정)", "사건 집단 비중", "통제집단 비중", "P(급등)",
                     "기준선 대비", "사건 n", "전체 n", "평균 월수익", "중앙값"], rows,
                    ["---", "---:", "---:", "---:", "---:", "---:", "---:", "---:", "---:"])
    t_ty += ("\n배타 배정 순서: 낙폭반전 → 신고가 → 박스수축 → 역배열탈출 → 정배열순항 → "
             "거래대금폭발 → 기타. (앞 유형에 걸리면 뒤 유형에서 제외)\n")

    # 조건별 단독(중복 허용) 표
    singles = [
        ("52주 신고가를 10일 내 돌파", (m.wh250 == 1)),
        ("52주 신고가 −5% 이내", (m.d52w > -0.05)),
        ("52주 신고가 −5% 이내 + 정배열", (m.d52w > -0.05) & (m.align3 == 1)),
        ("**52주 신고가 −5% 이내 + 거래대금 2배**", (m.d52w > -0.05) & (m.t520 >= 2.0)),
        ("60일 고점 −3% 이내", (m.dh60 > -0.03)),
        ("60일 고점 −3% 이내 + 거래대금 2배", (m.dh60 > -0.03) & (m.t520 >= 2.0)),
        ("코스닥 + 신고가 −5% 이내", (m.market == "KOSDAQ") & (m.d52w > -0.05)),
        ("완전정배열 + MA20 이격 5% 미만", (m.align5 == 1) & (m.disp20 < 0.05)),
        ("정배열 + 10일 수익 +5% 이상", (m.align3 == 1) & (m.ret10 > 0.05)),
        ("정배열 + 10일 수익 +5% + 거래대금 2배", (m.align3 == 1) & (m.ret10 > 0.05) & (m.t520 >= 2.0)),
        ("**박스 수축(BB폭 하위 40%) + 거래대금 1.5배**", (m.bbwpct < 0.40) & (m.t520 >= 1.5)),
        ("60일 박스폭 20% 미만 + 거래대금 1.5배", (m.box60 < 0.20) & (m.t520 >= 1.5)),
        ("낙폭 −25% 이상 + 60일 저점 대비 +10%", (m.dd120 <= -0.25) & (m.rb60 >= 0.10)),
        ("낙폭 −40% 이상 + 60일 저점 대비 +20%", (m.dd120 <= -0.40) & (m.rb60 >= 0.20)),
        ("MA60 돌파 + MA60 기울기 하락", (m.w60 == 1) & (m.sl60_20 < 0)),
        ("거래대금 10일 최대 5배 이상", (m.tmax10 >= 5)),
        ("거래대금 5배 이상 + 정배열", (m.tmax10 >= 5) & (m.align3 == 1)),
        ("상승일/하락일 거래량 2배 이상 + 정배열", (m.vol_ud > 2) & (m.align3 == 1)),
        ("ATR 9% 이상(변동성 큰 종목)", (m.atr14 > 0.09)),
        ("조용한 매집형", (m.vp_quiet == 1)),
        ("조용한 매집형(넓게)", (m.vp_quiet2 == 1)),
        ("거래량 동반 상승(+5%·2배)", (m.vp_volup == 1)),
        ("점증형 거래량", (m.vp_gradual == 1)),
    ]
    rows = []
    for lab, c in singles:
        c = c.fillna(False)
        sub = m[c]
        if len(sub) < 20:
            continue
        p = sub["ev"].mean()
        se = np.sqrt(p * (1 - p) / len(sub) + base * (1 - base) / n_all)
        rows.append([lab, len(sub), pc(p), f"{p/base:.2f}배", f"{(p-base)/se:+.1f}σ",
                     pct(sub["ret"].mean()), pct(sub["ret"].median())])
    t_single = md_table(["조건(중복 허용, 단독)", "n", "P(20% 급등)", "기준선 대비", "z",
                         "평균 월수익", "중앙값"], rows,
                        ["---", "---:", "---:", "---:", "---:", "---:", "---:"])
    t_ty += "\n**조건별 단독 성적(중복 허용)**\n\n" + t_single
    t_ty += "\n**유형별 대표 사례(수익률 상위)**\n\n"
    for lab in g.index:
        sub = m[(m.type == lab) & m.ev].nlargest(4, "ret")
        if len(sub) == 0:
            continue
        t_ty += f"- **{lab}**: " + ", ".join(
            f"{r['name']} {r['M']} {pct(r.ret, 0)}" for _, r in sub.iterrows()) + "\n"
    write_part("a7_types", t_ty)
    J["types"] = {str(k): {"n": int(r.n), "ev": int(r.ev), "rate": float(r.rate),
                           "lift": float(r.rate / base), "mean_ret": float(r["ret"]),
                           "median_ret": float(r["med"]),
                           "share_ev": float(r.ev / n_ev), "share_all": float(r.n / n_all)}
                  for k, r in g.iterrows()}
    J["singles"] = {}
    for lab, c in singles:
        c = c.fillna(False)
        sub = m[c]
        if len(sub) < 20:
            continue
        J["singles"][lab.replace("**", "")] = {"n": int(len(sub)),
                                               "p_event": float(sub["ev"].mean()),
                                               "lift": float(sub["ev"].mean() / base),
                                               "mean_ret": float(sub["ret"].mean()),
                                               "median_ret": float(sub["ret"].median())}

    # === A8 부록 ===
    def describe(r):
        bits = []
        if r.align5 == 1:
            bits.append("완전정배열")
        elif r.align3 == 1:
            bits.append("정배열")
        elif r.disp20 > 0:
            bits.append(f"MA20 위(+{r.disp20*100:.1f}%)·배열 아님")
        else:
            bits.append(f"MA20 아래({r.disp20*100:.1f}%)")
        br = []
        for xc, dsc, lab in [("w20", "ds20", "MA20"), ("w60", "ds60", "MA60"),
                             ("w120", "ds120", "MA120"), ("w240", "ds240", "MA240"),
                             ("wh60", "dsh60", "60일고점"), ("wh120", "dsh120", "120일고점"),
                             ("wh250", "dsh250", "52주신고가")]:
            if r[xc] == 1:
                br.append(f"{lab}({int(r[dsc])}일전)")
        bits.append("돌파 " + (", ".join(br) if br else "없음"))
        bits.append(f"거래량 최대 {r.vmax10:.1f}배·거래대금 5일 {r.t520:.1f}배")
        bits.append(f"10일 수익 {pct(r.ret10, 1)}")
        if r.dd120 < -0.2:
            bits.append(f"120일고점 {pct(r.dd120, 0)}")
        return " · ".join(bits)

    ap = m[m.ev].sort_values("ret", ascending=False).head(20)
    rows = [[f"{r['name']}({r.code})", r["M"], pct(r.ret, 1), r.type, describe(r)]
            for _, r in ap.iterrows()]
    t_ap = md_table(["종목", "급등한 달", "월 수익률", "유형", "직전 1~2주 상태"], rows,
                    ["---", "---", "---:", "---", "---"])
    write_part("a8_appendix", t_ap)

    # === B 눌림목 ===
    rand = acc_stats(acc["RANDOM|e1|h20"])
    rows = []
    for line in ("MA20", "MA60", "MA120"):
        for lab, nm in (("전체", "눌림 반등"), ("눌림없음", "눌림 없이 계속 상승")):
            r = acc_stats(acc[f"PB_{line}|{lab}|h20"])
            if r["n"] == 0:
                continue
            d = r["mean"] - rand["mean"]
            rows.append([line, nm, r["n"], pct(r["mean"]), pct(r["median"]), pc(r["win"]),
                         num(r["payoff"]), pct(rand["mean"]), f"{d*100:+.2f}%p",
                         zfmt(d / np.sqrt(r["se"] ** 2 + rand["se"] ** 2))])
    t_p1 = md_table(["선", "구분", "n", "20일 순수익 평균", "중앙값", "승률", "손익비",
                     "무작위 기준선", "차이", "z"], rows,
                    ["---", "---", "---:", "---:", "---:", "---:", "---:", "---:", "---:", "---:"])

    rows = []
    for line in ("MA20", "MA60", "MA120"):
        for dk, lab in [("A_얕음(2%이내)", "얕음 (−2% 이내)"), ("B_보통(2~5%)", "보통 (−2~−5%)"),
                        ("C_깊음(5~10%)", "깊음 (−5~−10%)"), ("D_이탈(10%초과)", "이탈 (−10% 초과)")]:
            r = acc_stats(acc[f"PB_{line}|{dk}|h20"])
            if r["n"] < 30:
                continue
            d = r["mean"] - rand["mean"]
            rows.append([line, lab, r["n"], pct(r["mean"]), pct(r["median"]), pc(r["win"]),
                         num(r["payoff"]), f"{d*100:+.2f}%p",
                         zfmt(d / np.sqrt(r["se"] ** 2 + rand["se"] ** 2))])
    t_p2 = md_table(["선", "눌림 깊이", "n", "20일 순수익 평균", "중앙값", "승률", "손익비",
                     "무작위 대비", "z"], rows,
                    ["---", "---", "---:", "---:", "---:", "---:", "---:", "---:", "---:"])

    rows = []
    for line in ("MA20", "MA60", "MA120"):
        for lab, nm in (("급등직전", "20% 급등 달 직전 2주"), ("일반", "그 외 전체 기간")):
            r = acc_stats(acc[f"PB_{line}|{lab}|h20"])
            if r["n"] == 0:
                continue
            rows.append([line, nm, r["n"], pct(r["mean"]), pct(r["median"]), pc(r["win"]),
                         num(r["payoff"])])
    t_p3 = md_table(["선", "국면", "n", "20일 순수익 평균", "중앙값", "승률", "손익비"], rows,
                    ["---", "---", "---:", "---:", "---:", "---:", "---:"])
    write_part("b_pullback", t_p1 + "\n" + t_p2 + "\n" + t_p3)
    J["pullback"] = {k: {kk: vv for kk, vv in acc_stats(v).items() if kk != "yearly"}
                     for k, v in acc.items() if k.startswith("PB_")}

    # === C 전략 표 ===
    _idxdf = pd.read_pickle(CACHE / "index.pkl")
    _k = _idxdf["close_k"].dropna()
    KOSPI20 = float((_k.shift(-20) / _k - 1).dropna().mean())
    sigs = sorted({k.split("|")[0] for k in acc if k.startswith(("S", "V")) and "|e1|" in k})
    rows, recs = [], []
    for s in sigs:
        for off in (1, 3, 5, 10):
            key = f"{s}|e{off}|h20"
            if key not in acc:
                continue
            r = acc_stats(acc[key])
            b = acc_stats(acc[f"RANDOM|e{off}|h20"])
            d = r["mean"] - b["mean"]
            zz = d / np.sqrt(r["se"] ** 2 + b["se"] ** 2) if r["se"] and b["se"] else np.nan
            ys = [y["mean"] for y in r["yearly"] if y["mean"] is not None and y["n"] >= 20]
            pos = sum(1 for y in ys if y > 0)
            rows.append([s, f"{off}일", r["n"], pct(r["mean"]), pct(r["median"]), pc(r["win"]),
                         num(r["payoff"]), f"{(r['mean']-KOSPI20)*100:+.2f}%p",
                         f"{d*100:+.2f}%p", zfmt(zz), f"{pos}/{len(ys)}",
                         "✅" if np.isfinite(zz) and zz >= 2 else ""])
            recs.append({"signal": s, "offset": off, "hold": 20,
                         **{k: r[k] for k in ("n", "mean", "median", "win", "payoff", "se")},
                         "diff": d, "z": zz, "years_pos": pos, "years": len(ys),
                         "yearly": r["yearly"]})
    t_s1 = md_table(["신호", "진입 시점", "n", "20일 순수익 평균", "중앙값", "승률", "손익비",
                     "코스피 대비", "무작위 대비", "z", "플러스 해", "2σ↑"], rows,
                    ["---", "---", "---:", "---:", "---:", "---:", "---:", "---:", "---:", "---:", "---:", "---:"])
    t_s1 = (f"무작위 기준선(같은 유니버스 전일 진입) 20일 순수익 평균 {pct(rand['mean'])} · "
            f"n={rand['n']:,} · 왕복비용 차감 후\n\n") + t_s1

    rows = []
    for s in sigs:
        for off in (1, 5):
            line = [s, f"{off}일"]
            for h in (5, 10, 20):
                key = f"{s}|e{off}|h{h}"
                if key in acc:
                    r = acc_stats(acc[key])
                    b = acc_stats(acc[f"RANDOM|e{off}|h{h}"])
                    d = r["mean"] - b["mean"]
                    zz = d / np.sqrt(r["se"] ** 2 + b["se"] ** 2)
                    line += [pct(r["mean"]), f"{d*100:+.2f}%p", zfmt(zz)] if h == 20 else [pct(r["mean"])]
                else:
                    line += ["—"] * (3 if h == 20 else 1)
            rows.append(line)
    t_s2 = md_table(["신호", "진입", "5일", "10일", "20일", "20일 무작위 대비", "z"], rows,
                    ["---", "---"] + ["---:"] * 5)

    rows3 = []
    for line in ("MA20", "MA60", "MA120"):
        for lab, key in [("눌림 반등", f"PB_{line}|전체|h20"),
                         ("눌림 없음(5일 후)", f"PB_{line}|눌림없음|h20")]:
            if key not in acc:
                continue
            r = acc_stats(acc[key])
            d = r["mean"] - rand["mean"]
            zz = d / np.sqrt(r["se"] ** 2 + rand["se"] ** 2)
            ys = [y["mean"] for y in r["yearly"] if y["mean"] is not None and y["n"] >= 20]
            rows3.append([f"{line} 눌림목", lab, r["n"], pct(r["mean"]), pct(r["median"]),
                          pc(r["win"]), num(r["payoff"]), f"{d*100:+.2f}%p", zfmt(zz),
                          f"{sum(1 for y in ys if y>0)}/{len(ys)}",
                          "✅" if np.isfinite(zz) and zz >= 2 else ""])
            recs.append({"signal": f"PB_{line}_{'눌림' if '전체' in key else '눌림없음'}",
                         "offset": "눌림", "hold": 20,
                         **{k: r[k] for k in ("n", "mean", "median", "win", "payoff", "se")},
                         "diff": d, "z": zz,
                         "years_pos": sum(1 for y in ys if y > 0), "years": len(ys),
                         "yearly": r["yearly"]})
    t_s3 = md_table(["신호", "진입", "n", "20일 순수익 평균", "중앙값", "승률", "손익비",
                     "무작위 대비", "z", "플러스 해", "2σ↑"], rows3,
                    ["---", "---", "---:", "---:", "---:", "---:", "---:", "---:", "---:", "---:", "---:"])
    write_part("c_strategy", t_s1 + "\n" + t_s2 + "\n" + t_s3)

    n_pass = sum(1 for r in recs if np.isfinite(r["z"]) and r["z"] >= 2)
    idxdf = pd.read_pickle(CACHE / "index.pkl")
    k = idxdf["close_k"].dropna()
    k20 = (k.shift(-20) / k - 1).dropna()
    J["strategy"] = {"random": {kk: rand[kk] for kk in ("n", "mean", "median", "win", "se")},
                     "random_by_offset": {off: acc_stats(acc[f"RANDOM|e{off}|h20"])["mean"]
                                          for off in (1, 3, 5, 10)},
                     "rows": recs, "n_rows": len(recs), "n_pass_2sigma": int(n_pass),
                     "n_sig": len(sigs)}
    J["kospi20"] = {"mean": float(k20.mean()), "n": int(len(k20)),
                    "se": float(k20.std() / np.sqrt(len(k20)))}

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(J, ensure_ascii=False, indent=1, default=float))
    print("wrote", OUT_JSON)
    print("features", len(zs), "sig", len(sig_list), "strategy rows", len(recs), "pass", n_pass)
    print("kospi20", J["kospi20"])
    print("top sig:", [(k2, round(v["z"], 1)) for k2, v in sig_list[:14]])
    print("types:\n", t_ty[:1200])


if __name__ == "__main__":
    run()
