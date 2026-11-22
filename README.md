# 얼미리서치 · Eolmi Research

시장 브리핑 · 국내 스윙 룰 · 13F 수급을 한 화면에 모은 개인 리서치 페이지.
GitHub Pages로 배포되는 정적 사이트입니다. 빌드 도구 없이 HTML/CSS/JS + JSON만 씁니다.

- 사이트: https://eolgang.github.io/stock-insight/
- 최근 업데이트: 2026-11-22

## 구성

```
index.html          화면 틀 (티커·헤더·히어로·탭·푸터)
styles.css          다크 핀테크 테마, 모바일 우선
app.js              JSON을 읽어 탭·표·차트·계산기 렌더링
data/insights.json  ★ 모든 콘텐츠(수치·표·뉴스·주석)는 이 파일 하나에서 관리
data/screener*.json 조건검색 스캔 결과·성과 히스토리 (scripts/scan_krx.py가 생성)
data/ma-charts.json 종목 차트용 일봉·이동평균 (scripts/scan_krx.py가 생성)
scripts/scan_krx.py 장마감 조건검색 스캐너
hologram.html/js/css  “3D 홀로그램 브리핑” 독립 페이지 (Three.js CDN, data/briefing-hologram.json + mp3)
scripts/build_hologram_audio.py  홀로그램 브리핑 음성(edge-tts) 생성 + 타임라인 자동 정렬
money-flow.js/css   “경제 인사이트” 탭(자산별 자금 흐름·한국 부동산, data/money-flow.json을 읽음)
data/money-flow.json 자금 흐름 스냅숏 누적(주 1회 갱신, 스키마는 docs/money-flow-schema.md)
data/money-flow.sample.json 실제 스냅숏이 없을 때만 쓰는 예시(코인·korea_property 포함)
chart-inbox.js/css  “차트 분석 맡기기” 업로드 버튼(플로팅 + · GitHub Contents API 커밋)
inbox/              업로드된 차트 캡처·메모(페이지에서 자동 커밋)
favicon.svg         아이콘
.nojekyll           Jekyll 처리 없이 정적 파일 그대로 서빙
```

## 차트 분석 맡기기 (업로드)

사이트 어디서든 보이는 오른쪽 아래 **+ 차트 분석 맡기기** 버튼으로 HTS 차트 캡처를 GitHub에 바로 커밋한다.

- 저장 위치: `inbox/YYYYMMDD-HHMMSS-파일명.확장자` · 메모를 적으면 같은 시각의 `…-memo.txt`도 함께 커밋
- 인증: 모달 **설정** 탭에 fine-grained PAT를 붙여넣으면 `localStorage`에만 저장된다(서버 전송 없음).
  토큰 범위는 `stock-insight` 저장소 + **Contents: Read and write**.
- ⚠ 저장소가 공개라 업로드한 이미지는 공개 URL(`https://eolgang.github.io/stock-insight/inbox/…`)로도 열린다.
- 읽는 방법: `git pull` 후 `ls inbox/`, 또는 `gh api /repos/eolgang/stock-insight/contents/inbox`

## 탭

| 탭 | 내용 |
|---|---|
| 시장 브리핑 | 11/20 마감 지수·지지선 표, 거장들의 매크로 신호등(공포·탐욕/VIX/10년물 원형 계기판 + 지표별 카드·기준선 표), 10년물 월별 차트, 고점 대비 낙폭 차트, 5% 금리 이력, 일정 |
| 경제 인사이트 | 자산별 자금 흐름(미국·한국 주식, 채권, 머니마켓, 금, 원자재, 부동산, 코인, 달러) + 국면 배지 + 매집/대피 신호 + 서울·수도권 부동산 매물·주간 가격·전세가율 + 스냅숏 기록 (매주 일요일 아침 갱신) |
| 관심종목 | 주간 스윙 후보(매주 일요일 20:00 갱신 · 최근 4주 롤링 추적) + 테마별 추적 종목 카드/요약 표 |
| 조건검색 | 장마감 확정 데이터 자동 스캔(20일선 돌파·52주 신고가·수급 포착·눌림목) + 조건별 적중률, 종목 클릭 시 5·20·60·120·240·360·480·720일선 차트 |
| 실시간 차트 | TradingView 위젯 + 종목 전환(NVDA·TSLA·TSM·AVGO·APH·SPY·QQQ·삼성전자·SK하이닉스·금), 20·200일선 |
| 뉴스 | 한국어 핵심 뉴스 카드 + TradingView 실시간 속보 |
| 스윙 전략 | 4대 선정 기준, 20일선 이격도 게이지/실측/기준표, 진입·청산 룰 |
| 손익 계산기 | 평단가·목표가·손절가·손익비 실시간 계산(원/달러) |
| 폭등 징표 | 테슬라 vs 엔비디아 비교, 공통 징표, 가짜 신호 반례, 13F 시차 |
| 13F 수급 | 2026 Q3 5사 수급, AI 병목주·암페놀 액면분할 주의 |

## 3D 홀로그램 브리핑 (`hologram.html`)

아침 브리핑을 40여 초 분량의 3D 홀로그램 씬 + 한국어 음성으로 재생하는 독립 페이지.
메인 페이지 히어로의 **🌌 3D 홀로그램 브리핑** 카드로 들어간다.

- 화면: 2D 카드·배지·큰 버튼 없이 **홀로그램 위에 얹힌 미니 HUD**만 둔다.
  - 상단: `얼미 브리핑` · 날짜 · `‹ 얼미리서치`
  - 하단: 원형 재생 버튼 + 얇은 진행바(4구간 틱) + 시간 + 다시보기 + 소리 — 한 줄
  - 그 위에 1~2줄 자막, 인트로는 코어 위에 `11.22` 외곽선 타이포 + 원형 ▶ (영화 스틸컷처럼)
- 3D: 자비스 스타일 코어 — 프레넬 림/스캔라인 셸 + 지오데식 와이어프레임(2겹) + 노드·연결선 + 분할 아크·방사 눈금 링 + 궤도 입자.
  단계별 HUD 프레임은 **깊은 Z에서 날아오고(fly-in) 다음 단계에서 접혀 빠진다(fly-out)**.
- 음성 반응: `AnalyserNode`로 음성 진폭·대역을 읽어 코어 꼭짓점 변위·글로우·링 회전·바닥 물결이 말소리에 맞춰 살아 움직인다.
  소리 끔/WebAudio 제한 환경에서는 음절 리듬을 합성한 fallback으로 같은 호흡을 유지한다.
- 구성: `hologram.html`(화면 틀) · `hologram.css`(HUD·인트로·모바일 390px) · `hologram.js`(Three.js 씬 + 타임라인·음성 동기화)
- 데이터: `data/briefing-hologram.json` **한 파일** — 날짜·문단(단계)·수치·음성 대본이 모두 여기에 있다.
- 음성: `data/briefing-<날짜>.mp3` (edge-tts `ko-KR-InJoonNeural`). 브라우저가 음성을 못 읽으면 `speechSynthesis`(ko-KR)로 대체 재생.
- 렌더링: three@0.160 CDN(importmap) + UnrealBloom. CDN·3D가 막히면 자동으로 자막+음성 모드(`body.no3d`)로 떨어진다. 빌드 도구·API 비용 없음.

### 매일 아침 갱신 절차

1. `data/briefing-hologram.json`의 `date`·`weekday`·`as_of`와 `stages[]`의 `headline`·`sub`·`metrics`·`caption`·`speech`를 오늘 내용으로 고친다.
   - `stages[].visual`은 3D 연출 종류: `weather`(앰버 게이지 링) / `us`(네온 낙폭 막대) / `kr`(코스피 HUD + 지표 칩) / `calendar`(3겹 홀로그램 레이어).
   - `metrics[].pct`(숫자)는 미증시 막대 높이·국장 유리판 등락 표시에 쓰인다.
   - `audio.duration`과 `stages[].start/end`는 비워 두어도 된다(아래 스크립트가 채운다).
2. 음성과 타임라인을 만든다.

   ```bash
   python3 -m pip install edge-tts mutagen
   python3 scripts/build_hologram_audio.py            # --voice ko-KR-SunHiNeural --rate +8% 도 가능
   ```

   문단별 mp3를 만들고 실제 길이를 재서 `stages[].start/end`·`audio.duration`을 채운 뒤, 한 파일 `data/briefing-<날짜>.mp3`로 합친다.
3. `data/briefing-hologram.json` + `data/briefing-<날짜>.mp3`를 함께 커밋한다. (HTML/CSS/JS를 고쳤으면 `hologram.html`의 `?v=`도 올린다.)

```bash
git add data/briefing-hologram.json data/briefing-2026-11-22.mp3
git commit -m "hologram: 11/22 아침 브리핑 음성·타임라인 갱신"
git push
```

- 재생 정책상 자동재생이 막히므로 인트로의 **▶** 를 한 번 누른다(소리 켜기). 무음으로 보려면 **소리 없이 3D만 보기**.
- 소리는 사용자 클릭 안에서 `AudioContext.resume()` 후 재생한다. `AnalyserNode`는 `connect(destination)`까지 연결해 소리가 끊기지 않게 한다.
- 로컬에서 볼 때 `python3 -m http.server`는 Range 요청을 지원하지 않아 음성 구간 이동(배지·진행 바)이 먹지 않는다. 실제 동작은 GitHub Pages나 Range를 지원하는 서버에서 확인한다.
- 진행 바 드래그·스페이스(재생/정지)·←/→(파트 이동)·`1~4`(파트 점프)를 지원한다.

## 경제 인사이트 (자금 흐름)

`data/money-flow.json` 한 파일만 읽는다. 파일이 없거나 깨지면 `data/money-flow.sample.json`으로 화면을 채우고 상단에 “예시 데이터”라고 표시한다.

- 계약: `as_of`, `regime{label,note}`, `assets[{name,flow_label,flow_value,flow_unit,ytd_return,source_url}]`, `signals[{name,value,direction,interpretation}]`, `korea_property{listings,price,jeonse_ratio,source_urls}`, `history[]`(최신이 마지막)
- `direction`은 `매집`/`대피`/`중립`, `flow_unit`은 `USD`/`KRW`(달러처럼 가격 변동을 쓰는 항목은 `%`)
- 예전 스키마(`regime.verdict`, `flow_usd_or_krw` 문자열, `direction 개선/악화`)도 함께 읽는다. 둘 다 있으면 `flow_value`를 계산에, `flow_usd_or_krw`를 표시에 쓴다.
- 자산·부동산·신호·history 어느 값이 빠져도 그 블록만 “데이터 없음”으로 두고 나머지는 그린다.

## 조건검색 스캐너

```bash
python3 scripts/scan_krx.py                 # 직전 거래일 기준 스캔 (약 1~2분, 표준 라이브러리만 사용)
python3 scripts/scan_krx.py --seed 10       # 최근 10거래일 소급 계산(미래 데이터 미사용)
python3 scripts/scan_krx.py --as-of 20261113
```

- 데이터: 네이버 금융 장마감 확정 데이터(일봉 수정주가·시가총액·거래대금·투자자별 순매수). 유료 API·계정 불필요.
- 출력: `data/screener.json`(오늘 결과) · `data/screener-history.json`(날짜별 히트 + 1·5·10·20거래일 뒤 성과) · `data/ma-charts.json`(종목 차트용 일봉 900개 + 이동평균 8종)
- 자동 갱신: Pickle 정기 작업(장마감 브리핑 15:40 · 주간 스윙 일 20:00)에서 위 스크립트를 돌려 커밋·푸시한다. GitHub Actions로 옮기려면 `docs/daily-scan.workflow.yml`을 `.github/workflows/daily-scan.yml`로 옮기면 된다(저장소 workflow 권한 필요).

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
