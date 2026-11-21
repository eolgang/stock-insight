# 얼미리서치 · Eolmi Research

시장 브리핑 · 국내 스윙 룰 · 13F 수급을 한 화면에 모은 개인 리서치 페이지.
GitHub Pages로 배포되는 정적 사이트입니다. 빌드 도구 없이 HTML/CSS/JS + JSON만 씁니다.

- 사이트: https://eolgang.github.io/stock-insight/
- 최근 업데이트: 2026-11-21

## 구성

```
index.html          화면 틀 (헤더·히어로·탭·푸터)
styles.css          다크 핀테크 테마, 모바일 우선
app.js              JSON을 읽어 탭·표·차트·계산기 렌더링
data/insights.json  ★ 모든 콘텐츠(수치·표·뉴스·주석)는 이 파일 하나에서 관리
favicon.svg         아이콘
.nojekyll           Jekyll 처리 없이 정적 파일 그대로 서빙
```

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
| `cards` | 카드 그리드 (`cols: 3`) |
| `list` | 체크리스트 |
| `timeline` | 날짜 일정 |
| `chips` | 종목 태그 |
| `news` | 뉴스 카드 (tag/tone/title/body/meta) |
| `chart` | `line`(라인), `bars`(낙폭 바), `range`(이격도 게이지) |
| `tradingview` | TradingView 실시간 차트 + 종목 전환 버튼 |
| `tvTimeline` | TradingView 실시간 속보 위젯 |
| `simulator` | 평단가·목표가·손절가 계산기 |

## 데이터 시각화

- 라이브 차트·티커·뉴스: TradingView 무료 임베드 (인터넷 필요)
- 10년물 추이 / 고점 대비 낙폭 / 20일선 이격도: 외부 라이브러리 없이 SVG·CSS로 직접 그림

## 주의

- 공개 저장소입니다. 실명·전화번호·주소·계좌번호 같은 개인정보는 넣지 않습니다.
- 모든 수치는 표에 적힌 기준일의 값이며, 투자 권유가 아닙니다.
