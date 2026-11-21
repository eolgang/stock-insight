# 얼미리서치 · Eolmi Research

시장 브리핑 · 국내 스윙 룰 · 13F 수급을 한 화면에 모은 개인 리서치 페이지.
GitHub Pages로 배포되는 정적 사이트입니다. 빌드 도구 없이 HTML/CSS/JS + JSON만 씁니다.

- 사이트: https://eolgang.github.io/stock-insight/
- 최근 업데이트: 2026-11-21

## 구성

```
index.html          화면 틀 (티커·헤더·히어로·탭·푸터)
styles.css          다크 핀테크 테마, 모바일 우선
app.js              JSON을 읽어 탭·표·차트·계산기 렌더링
data/insights.json  ★ 모든 콘텐츠(수치·표·뉴스·주석)는 이 파일 하나에서 관리
favicon.svg         아이콘
.nojekyll           Jekyll 처리 없이 정적 파일 그대로 서빙
```

## 탭

| 탭 | 내용 |
|---|---|
| 시장 브리핑 | 11/20 마감 지수·지지선 표, 거장들의 매크로 신호등(공포·탐욕/VIX/10년물 원형 계기판 + 지표별 카드·기준선 표), 10년물 월별 차트, 고점 대비 낙폭 차트, 5% 금리 이력, 일정 |
| 관심종목 | 주간 스윙 후보(매주 일요일 20:00 갱신 · 최근 4주 롤링 추적) + 테마별 추적 종목 카드/요약 표 |
| 실시간 차트 | TradingView 위젯 + 종목 전환(NVDA·TSLA·TSM·AVGO·APH·SPY·QQQ·삼성전자·SK하이닉스·금), 20·200일선 |
| 뉴스 | 한국어 핵심 뉴스 카드 + TradingView 실시간 속보 |
| 스윙 전략 | 4대 선정 기준, 20일선 이격도 게이지/실측/기준표, 진입·청산 룰 |
| 손익 계산기 | 평단가·목표가·손절가·손익비 실시간 계산(원/달러) |
| 폭등 징표 | 테슬라 vs 엔비디아 비교, 공통 징표, 가짜 신호 반례, 13F 시차 |
| 13F 수급 | 2026 Q3 5사 수급, AI 병목주·암페놀 액면분할 주의 |

## 업데이트 방법 (브리핑 때마다)

1. `data/insights.json`만 수정합니다.
   - `site.updated`: 화면 상단·하단의 "최근 업데이트" 날짜
   - `site.ticker`: 티커 위젯이 막힐 때 보여줄 종가 뱃지
   - `sections[].blocks`: 각 탭의 표(`table`), 카드(`cards`), 뉴스(`news`), 차트(`chart`) 등
2. 바꾼 뒤 `main`에 푸시하면 1~2분 안에 GitHub Pages에 반영됩니다.

```bash
git add data/insights.json
git commit -m "chore: 11/22 아침 브리핑 반영"
git push
```

## 블록 타입

| type | 설명 |
|---|---|
| `callout` | 강조 박스 (tone: `alert` / `warn` / `good`) |
| `table` | 표. `pin: true`면 첫 열 고정, `minWidth`로 가로 스크롤 폭 지정 |
| `cards` | 카드 그리드 (`cols: 3`). 항목에 `sub`(부제)·`status`+`statusTone`(상태 뱃지)를 주면 헤더형 카드로 렌더 |
| `list` | 체크리스트 |
| `timeline` | 날짜 일정 |
| `chips` | 종목 태그 |
| `news` | 뉴스 카드 (tag/tone/title/body/meta) |
| `chart` | `line`(라인), `bars`(낙폭 바), `range`(이격도 게이지), `gauge`(반원형 계기판) |
| `tradingview` | TradingView 실시간 차트 + 종목 전환 버튼 |
| `tvTimeline` | TradingView 실시간 속보 위젯 |
| `simulator` | 평단가·목표가·손절가 계산기 |

## 데이터 시각화

- 라이브 차트·티커·뉴스: TradingView 무료 임베드 (인터넷 필요)
- 10년물 추이 / 고점 대비 낙폭 / 20일선 이격도: 외부 라이브러리 없이 SVG·CSS로 직접 그림

### `chart: gauge` (원형 계기판) 데이터 형식

```json
{ "type": "chart", "chart": "gauge", "title": "...", "sub": "...", "note": "...",
  "items": [
    { "name": "CNN 공포·탐욕", "value": 10, "display": "10", "unit": "/100",
      "min": 0, "max": 100, "minLabel": "0", "maxLabel": "100",
      "status": "극단적 공포", "statusTone": "alert",
      "zones": [ { "to": 25, "tone": "alert" }, { "to": 45, "tone": "hot" },
                  { "to": 55, "tone": "muted" }, { "to": 100, "tone": "good" } ],
      "foot": "1주 전 37 · 1개월 전 28", "note": "..." }
  ] }
```

- `value`는 바늘 위치(숫자), `display`는 화면에 보일 글자(없으면 value).
- `zones`는 min→max를 따라 누적되는 구간(`to`까지). tone은 `good`/`warn`/`hot`/`alert`/`muted`.
- 지표를 추가하려면 `items`에 하나만 더 넣으면 3열 그리드에 자동으로 붙습니다.

### TradingView 무료 임베드 제한

- 한국 거래소(KRX) 심볼과 코스피·나스닥100·미 10년물 같은 지수/채권 심볼은 무료 위젯에서
  "이 심볼은 트레이딩뷰에서만 볼 수 있습니다"로 막힙니다. 그래서
  - 티커에는 실제로 열리는 심볼(S&P500·금·원/달러·비트코인·EWY·QQQ·NVDA·TSM·AVGO)만 넣고,
    코스피·나스닥 종합·미 10년물은 `site.snapshot`의 종가 뱃지로 보여줍니다.
  - 삼성전자·SK하이닉스 버튼은 차트 대신 TradingView·네이버 금융 링크를 띄웁니다.
- CSS/JS를 고쳤을 때는 `index.html`의 `?v=` 날짜를 올려야 브라우저 캐시(10분)를 피합니다.

## 주의

- 공개 저장소입니다. 실명·전화번호·주소·계좌번호 같은 개인정보는 넣지 않습니다.
- 모든 수치는 표에 적힌 기준일의 값이며, 투자 권유가 아닙니다.
