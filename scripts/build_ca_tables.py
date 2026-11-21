#!/usr/bin/env python3
"""candidate_a_verify.py가 만든 ca_acc.pkl → 표(markdown) + 요약 JSON."""
from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from monthly_surge import acc_stats  # noqa: E402

YEARS = list(range(2015, 2027))
CACHE = Path("/workspace/work/backtest/cache")

EXIT_KO = {
    "hold20": "20거래일 단순보유",
    "hold10": "10거래일 단순보유",
    "hold40": "40거래일 단순보유",
    "sl8only": "손절 −8%만 / 20일",
    "tp15only": "익절 +15%만 / 20일",
    "sl8": "손절 −8% / 익절 +15% / 20일",
    "sl7": "손절 −7% / 익절 +15% / 20일",
    "trail8": "손절 −8% + 트레일링(최고 −7%·MA20) / 20일",
}


def pct(v, d=2):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "—"
    return f"{v * 100:+.{d}f}%"


def zs(z):
    if z is None:
        return "—"
    return f"{z:+.1f}σ" if abs(z) >= 2 else f"{z:+.1f}σ (우연)"


def num(v, d=2):
    if v is None:
        return "—"
    return f"{v:.{d}f}"


def tbl(headers, rows, align=None):
    align = align or ["---"] + ["---:"] * (len(headers) - 1)
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(align) + " |"]
    for r in rows:
        out.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(out)


class A:
    def __init__(self, acc):
        self.acc = acc

    def st(self, key, randkey=None):
        a = self.acc.get(key)
        if a is None:
            return None
        s = acc_stats(a)
        o = {"n": s["n"], "mean": s["mean"], "median": s["median"], "win": s["win"],
             "payoff": s["payoff"], "avg_win": s["avg_win"], "avg_loss": s["avg_loss"],
             "se": s["se"], "sd": s["sd"], "yearly": {y["year"]: (y["n"], y["mean"]) for y in s["yearly"]}}
        if randkey and randkey in self.acc:
            rs = acc_stats(self.acc[randkey])
            o["rand_mean"] = rs["mean"]
            o["diff"] = s["mean"] - rs["mean"]
            o["z"] = o["diff"] / s["se"] if s["se"] else None
        if key + "|kos" in self.acc:
            o["kos"] = acc_stats(self.acc[key + "|kos"])["mean"]
        return o

    def cnt(self, key):
        a = self.acc.get(key)
        return int(a["n"]) if a else 0


def build(acc_pkl: Path, out_json: Path, out_tables: Path):
    d = pickle.load(open(acc_pkl, "rb"))
    acc = d["acc"]
    a = A(acc)
    parts = []
    J = {"meta": {
        "generated": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
        "period": ["2015-01-02", "2026-11-20"],
        "tickers": d.get("tickers"), "universe": d.get("uni"),
        "cost": "슬리피지 0.10%×2 + 수수료 0.015%×2 + 증권거래세 연도별 = 왕복 약 0.46%p 차감",
        "note": "수정주가 일봉, 신호일 유니버스(시총 500억·20일 거래대금 100억), 중복 진입 제거 없음(직전 보고서와 동일)",
    }}

    # ---------------- 무작위 기준선 ----------------
    rand = {}
    for ex in ["hold20", "hold10", "hold40", "sl8", "sl7", "sl8only", "tp15only", "trail8"]:
        for p in ["", "|p1", "|p2"]:
            s = a.st(f"RAND|{ex}{p}")
            if s:
                rand[f"{ex}{p}"] = {"n": s["n"], "mean": s["mean"], "median": s["median"],
                                    "win": s["win"], "sd": s["sd"]}
    J["random"] = rand
    rows = []
    for ex in ["hold20", "hold10", "hold40", "sl8", "sl7", "sl8only", "tp15only", "trail8"]:
        for p, lab in (("", "전체"), ("|p1", "2016–2020"), ("|p2", "2021–2026")):
            s = a.st(f"RAND|{ex}{p}")
            rows.append([EXIT_KO[ex], lab, f"{s['n']:,}", pct(s["mean"]), pct(s["median"]),
                         f"{s['win']*100:.1f}%", num(s["payoff"])])
    parts.append("### R0. 무작위 기준선 (같은 유니버스 모든 날 진입)\n\n" +
                 tbl(["청산 규칙", "기간", "n", "평균", "중앙값", "승률", "손익비"], rows))

    # ---------------- T1 기간 분할 ----------------
    rows, periods = [], []
    for ex in ["hold20", "hold10", "hold40", "sl8", "sl7", "sl8only", "tp15only", "trail8"]:
        for p, lab in (("", "전체"), ("|p1", "2016–2020"), ("|p2", "2021–2026")):
            s = a.st(f"A|{ex}{p}", f"RAND|{ex}{p}")
            rows.append([EXIT_KO[ex], lab, f"{s['n']:,}", pct(s["mean"]), pct(s["median"]),
                         f"{s['win']*100:.1f}%", num(s["payoff"]), pct(s.get("rand_mean")),
                         pct(s.get("diff")), zs(s.get("z")), pct(s.get("kos"))])
            periods.append({"exit": ex, "period": lab, **{k: s.get(k) for k in
                            ("n", "mean", "median", "win", "payoff", "rand_mean", "diff", "z", "kos")}})
    parts.append("### T1. 후보 A — 기간 분할 (진입=신호 5거래일 뒤 시가)\n\n" +
                 tbl(["청산 규칙", "기간", "n", "순수익 평균", "중앙값", "승률", "손익비",
                      "무작위 평균", "무작위 대비", "z", "코스피 대비"], rows))
    J["task1"] = {"periods": periods}

    # ---------------- T2 청산 규칙 ----------------
    rows, exits = [], []
    for ex in ["hold20", "hold10", "hold40", "sl8", "sl7", "sl8only", "tp15only", "trail8"]:
        s = a.st(f"A|{ex}", f"RAND|{ex}")
        n = s["n"]
        sn = a.cnt(f"A|{ex}|T1") / n if n else 0
        tp = a.cnt(f"A|{ex}|T2") / n if n else 0
        tr = a.cnt(f"A|{ex}|T3") / n if n else 0
        rows.append([EXIT_KO[ex], f"{n:,}", pct(s["mean"]), pct(s["median"]), f"{s['win']*100:.1f}%",
                     pct(s["avg_win"]), pct(s["avg_loss"]), num(s["payoff"]),
                     f"{sn*100:.0f}%" if sn else "—", f"{tp*100:.0f}%" if tp else "—",
                     f"{tr*100:.0f}%" if tr else "—", pct(s.get("diff")), zs(s.get("z")), pct(s.get("kos"))])
        exits.append({"exit": ex, "n": n, "mean": s["mean"], "median": s["median"], "win": s["win"],
                      "payoff": s["payoff"], "avg_win": s["avg_win"], "avg_loss": s["avg_loss"],
                      "stop_share": sn, "tp_share": tp, "trail_share": tr,
                      "rand_mean": s.get("rand_mean"), "diff": s.get("diff"), "z": s.get("z"),
                      "kos": s.get("kos")})
    parts.append("### T2. 후보 A — 청산 규칙 비교 (전 기간, n=5,431)\n\n" +
                 tbl(["청산 규칙", "n", "순수익 평균", "중앙값", "승률", "평균이익", "평균손실", "손익비",
                      "손절 발동", "익절", "트레일", "무작위 대비", "z", "코스피 대비"], rows))
    J["task1"]["exits"] = exits

    # ---------------- T3 연도별 ----------------
    idx = pd.read_pickle(CACHE / "index.pkl")["close_k"]
    ky = idx.groupby(idx.index.year).last()
    ky = (ky / ky.shift(1) - 1)
    kby = [a.st("A|bull|hold20"), a.st("A|sl8"), a.st("RAND|hold20")]
    rows, yearly = [], {}
    for y in range(2016, 2027):
        line = [y]
        for s in (a.st("A|hold20"), a.st("A|sl8"), a.st("A|trail8"), a.st("A|bull|hold20"),
                  a.st("A|bear|hold20"), a.st("RAND|hold20")):
            n, m = s["yearly"].get(y, (0, None))
            line += [f"{n}" if n else "—", pct(m, 1) if n else "—"]
        line.append(pct(float(ky.get(y)) if y in ky.index and np.isfinite(ky.get(y)) else None, 1))
        rows.append(line)
        yearly[y] = {k: a.st(k)["yearly"].get(y, (0, None))[1] for k in
                     ("A|hold20", "A|sl8", "A|trail8", "A|bull|hold20", "A|bear|hold20", "RAND|hold20")}
    parts.append("### T3. 연도별 순수익 평균 (후보 A, 청산별)\n\n" +
                 tbl(["연도", "20일 n", "20일", "손절 n", "손절−8", "트레일 n", "트레일",
                      "시장필터 n", "시장필터", "비필터 n", "비필터", "무작위 n", "무작위", "코스피 연간"], rows))
    J["task1"]["yearly"] = {str(k): v for k, v in yearly.items()}

    # ---------------- T4 시장 필터 ----------------
    rows, regime = [], []
    for ex in ["hold20", "sl8"]:
        for k, lab in (("A", "필터 없음"), ("A|bull", "코스피>MA20"), ("A|bear", "코스피≤MA20")):
            s = a.st(f"{k}|{ex}", f"RAND|{ex}")
            rows.append([EXIT_KO[ex], lab, f"{s['n']:,}", pct(s["mean"]), pct(s["median"]),
                         f"{s['win']*100:.1f}%", num(s["payoff"]), pct(s.get("diff")), zs(s.get("z")),
                         pct(s.get("kos"))])
            regime.append({"exit": ex, "filter": lab, "n": s["n"], "mean": s["mean"],
                           "median": s["median"], "win": s["win"], "payoff": s["payoff"],
                           "z": s.get("z"), "kos": s.get("kos")})
    parts.append("### T4. 시장 필터(진입일 코스피 > 코스피 MA20) 효과\n\n" +
                 tbl(["청산", "시장 필터", "n", "평균", "중앙값", "승률", "손익비", "무작위 대비", "z",
                      "코스피 대비"], rows))
    J["task1"]["regime"] = regime

    # ---------------- T5 구간(월말/중순) ----------------
    rows, zones = [], []
    for ex in ["hold20", "sl8"]:
        for k, lab in (("A", "전체"), ("A|zmid", "월 중순 진입(T−8~T−2)"), ("A|zend", "월말 진입(T−1·T)"),
                       ("A|zoth", "그 외")):
            s = a.st(f"{k}|{ex}", f"RAND|{ex}")
            rows.append([EXIT_KO[ex], lab, f"{s['n']:,}", pct(s["mean"]), pct(s["median"]),
                         f"{s['win']*100:.1f}%", num(s["payoff"]), pct(s.get("diff")), zs(s.get("z"))])
            zones.append({"exit": ex, "zone": lab, "n": s["n"], "mean": s["mean"],
                          "median": s["median"], "win": s["win"], "z": s.get("z")})
    parts.append("### T5. 후보 A — 진입일이 월말/월 중순일 때\n\n" +
                 tbl(["청산", "진입 구간", "n", "평균", "중앙값", "승률", "손익비", "무작위 대비", "z"], rows))
    J["task1"]["zones"] = zones

    # ---------------- T6 월 중순 그리드 ----------------
    CONDS = {"a": "신고가 −5% 이내", "b": "MA20 위", "c": "단독"}
    METS = {"vol": "거래량", "amt": "거래대금"}
    task2 = {}
    for ex in ["hold20", "hold10", "sl8"]:
        rows, g = [], []
        for m in ["vol", "amt"]:
            for t in [2, 3]:
                for c in ["a", "b", "c"]:
                    for e in [1, 3, 5]:
                        s = a.st(f"M|{m}{t}|{c}|mid|e{e}|{ex}", f"RAND|{ex}")
                        if not s:
                            continue
                        rows.append([f"{METS[m]} {t}배", CONDS[c], f"{e}일", f"{s['n']:,}",
                                     pct(s["mean"]), pct(s["median"]), f"{s['win']*100:.1f}%",
                                     num(s["payoff"]), pct(s.get("diff")), zs(s.get("z")), pct(s.get("kos"))])
                        g.append({"metric": m, "thr": t, "cond": c, "entry": e, "exit": ex,
                                  "n": s["n"], "mean": s["mean"], "median": s["median"], "win": s["win"],
                                  "payoff": s["payoff"], "rand_mean": s.get("rand_mean"),
                                  "diff": s.get("diff"), "z": s.get("z"), "kos": s.get("kos")})
        parts.append(f"### T6-{ex}. 월 중순(T−8~T−2) 신호 — 청산 {EXIT_KO[ex]}\n\n" +
                     tbl(["거래량 신호", "추가 조건", "진입", "n", "평균", "중앙값", "승률", "손익비",
                          "무작위 대비", "z", "코스피 대비"], rows))
        task2[ex] = g

    # ---------------- T7 구간 비교 ----------------
    rows, zc = [], []
    for m in ["vol", "amt"]:
        for t in [2, 3]:
            for c in ["a", "b", "c"]:
                line = [f"{METS[m]} {t}배", CONDS[c]]
                for z in ["mid", "end", "oth"]:
                    s = a.st(f"M|{m}{t}|{c}|{z}|e5|hold20", "RAND|hold20")
                    line.append(f"{pct(s['mean'])} ({s['n']:,})" if s else "—")
                    if s:
                        zc.append({"metric": m, "thr": t, "cond": c, "zone": z, "n": s["n"],
                                   "mean": s["mean"], "median": s["median"], "z": s.get("z")})
                rows.append(line)
    parts.append("### T7. 같은 신호를 시점만 바꿔 진입 (5일 뒤 진입·20일 보유)\n\n" +
                 tbl(["거래량 신호", "추가 조건", "월 중순", "월말", "그 외"], rows))
    task2["zones"] = zc

    # ---------------- T8 중복 제거 ----------------
    rows, dd = [], []
    for m in ["vol", "amt"]:
        for t in [2, 3]:
            for c in ["a", "b", "c"]:
                line = [f"{METS[m]} {t}배", CONDS[c]]
                for ex in ["hold20", "hold10", "sl8"]:
                    s = a.st(f"MD|{m}{t}|{c}|e5|{ex}", f"RAND|{ex}")
                    line.append(f"{pct(s['mean'])} ({s['n']:,})" if s else "—")
                    if s:
                        dd.append({"metric": m, "thr": t, "cond": c, "exit": ex, "n": s["n"],
                                   "mean": s["mean"], "median": s["median"], "z": s.get("z")})
                rows.append(line)
    parts.append("### T8. 월 중순 신호를 '종목·월 1회(첫 신호)'만 잡았을 때 (5일 뒤 진입)\n\n" +
                 tbl(["거래량 신호", "추가 조건", "20일", "10일", "손절−8"], rows))
    task2["dedup"] = dd

    # ---------------- T9 월 중순 후보 기간 분할 ----------------
    rows, p2 = [], []
    for base, lab in (("M|amt2|a|mid|e5|hold20", "거래대금 2배+신고가−5% · 5일 · 20일"),
                      ("M|amt2|a|mid|e5|hold10", "거래대금 2배+신고가−5% · 5일 · 10일"),
                      ("M|vol3|a|mid|e5|hold10", "거래량 3배+신고가−5% · 5일 · 10일"),
                      ("M|vol2|c|mid|e5|hold20", "거래량 2배 단독 · 5일 · 20일"),
                      ("M|vol2|b|mid|e5|hold20", "거래량 2배+MA20 위 · 5일 · 20일")):
        for p, plab in (("", "전체"), ("|p1", "2016–2020"), ("|p2", "2021–2026")):
            s = a.st(base + p, f"RAND|hold20" if "hold20" in base else f"RAND|hold10")
            if not s:
                continue
            rows.append([lab, plab, f"{s['n']:,}", pct(s["mean"]), pct(s["median"]),
                         f"{s['win']*100:.1f}%", pct(s.get("diff")), zs(s.get("z"))])
            p2.append({"rule": lab, "period": plab, "n": s["n"], "mean": s["mean"],
                       "median": s["median"], "win": s["win"], "z": s.get("z")})
    parts.append("### T9. 월 중순 대표 규칙 — 기간 분할\n\n" +
                 tbl(["규칙", "기간", "n", "평균", "중앙값", "승률", "무작위 대비", "z"], rows))
    task2["periods"] = p2

    # ---------------- T10 월 중순 대표 규칙 연도별 ----------------
    rows, ys = [], {}
    keys = [("M|amt2|a|mid|e5|hold20", "거래대금2배+신고가·20일"),
            ("M|vol2|c|mid|e5|hold20", "거래량2배 단독·20일"),
            ("RAND|hold20", "무작위")]
    for y in range(2016, 2027):
        line = [y]
        for k, lab in keys:
            n, m = a.st(k)["yearly"].get(y, (0, None))
            line += [f"{n}" if n else "—", pct(m, 1) if n else "—"]
            ys.setdefault(lab, {})[y] = m
        rows.append(line)
    parts.append("### T10. 월 중순 대표 규칙 — 연도별 평균\n\n" +
                 tbl(["연도", "거래대금2배+신고가 n", "평균", "거래량2배 단독 n", "평균", "무작위 n", "평균"], rows))
    task2["yearly"] = {k: {str(y): v for y, v in d_.items()} for k, d_ in ys.items()}

    # 보조 검증(달력 10~20일 정의) — 별도 스크립트 산출물이 있으면 병합
    cal = Path("/workspace/work/backtest/report_parts/ca_calendar.json")
    if cal.exists():
        task2["calendar_check"] = json.loads(cal.read_text(encoding="utf-8"))
    J["task2"] = task2

    out_tables.parent.mkdir(parents=True, exist_ok=True)
    out_tables.write_text("\n\n".join(parts) + "\n", encoding="utf-8")
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(J, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[out] {out_json}\n[out] {out_tables}")
