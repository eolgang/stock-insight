# money-flow.json 스키마 (자산 간 자금 흐름 스냅숏)

`data/money-flow.json`은 **날짜별로 쌓는** 자금 흐름 스냅숏입니다. `money-flow.js` 탭이 이 파일 하나를 읽고,
파일이 없으면 `data/money-flow.sample.json`(예시)으로 대체합니다. 생성·갱신: Pickle 정기 작업(주 1회).
최초 실데이터 스냅숏 **2026-11-21**.

## 최상위 구조

| 키 | 타입 | 설명 |
|---|---|---|
| `schema_version` | number | 현재 `1` |
| `as_of` | string `YYYY-MM-DD` | 이 스냅숏의 기준일 |
| `regime` | object | `{ label, note }` 필수 + `{ verdict, reason, basis_date }` 보조 |
| `assets` | array | 자산별 자금 흐름 (아래 표) |
| `signals` | array(3~5) | 매집/대피 판별 지표 |
| `korea_property` | object | 서울·수도권 아파트 매물/가격 (아래 표) |
| `history` | array | 과거 스냅숏 누적. 오래된 것 → 최신 순, **마지막 항목 = 최상위 현재 값** |

### `regime`

```json
{ "label": "조정", "note": "판정 근거 한 줄", "verdict": "조정", "reason": "한 줄", "basis_date": "2026-11-20" }
```

- `label`은 **`상승` / `조정` / `하락`** 만. UI가 `상승`=up, `조정`=mid, `하락`=down으로 색을 칠한다.
- `note`는 수치·날짜를 넣은 한 줄 근거 (카드에 그대로 노출).
- `verdict`·`reason`·`basis_date`는 같은 내용의 보조 필드(다른 도구가 읽을 때용). 값은 `label`·`note`와 일치시킨다.

### `assets[]`

| 필드 | 타입 | 설명 |
|---|---|---|
| `name` | string | `미국주식` `한국주식` `채권` `머니마켓` `금` `원자재` `부동산` `코인(비트코인·이더리움)` `달러` (9종 고정 순서) |
| `flow_label` | string | `순유입` / `순유출` / `보합` (UI가 색·막대 방향 결정) |
| `flow_value` | number \| null | **기간 순유입/유출 금액**. USD는 달러, KRW는 원 단위의 절대 숫자. 통계가 없으면 `null` |
| `flow_unit` | string | `USD` / `KRW` / `%` — `%`는 가격·지수 변동률밖에 없을 때만(현재 달러) |
| `ytd_return` | number \| null | 연초 대비 수익률 **숫자**(% 기호 없이, 예: `-18.0`). 모르면 `null` |
| `source_url` | string | 출처 1개 |
| `flow_usd_or_krw` | string | 같은 값을 사람이 읽는 문자열로 (`+$126.0B`, `-14.74조원`). 다른 도구·보고서가 그대로 인용 |
| `flow_period` | string | **어느 기간·어떤 모집단의 수치인지** (예: `2026-10 미국 주식형 ETF 월간 순유입`) |
| `ytd_return_label` | string | 수익률의 기준 지표·날짜 (예: `+1.0% (S&P 500, 2026-11-20)`) |
| `note` | string | 보조 설명 |

주의: 자산마다 모집단이 다르다(ETF 순설정 / 펀드 순유입 / 외국인 순매수 / 거래액). `flow_period`에 반드시 적는다.
부동산은 거래액 감소, 달러는 가격 변동을 대체 지표로 쓰고 그 사실을 `note`에 남긴다.

### `signals[]`

| 필드 | 타입 | 설명 |
|---|---|---|
| `name` | string | 지표 이름 |
| `value` | string | 현재 값(+ 기준일) |
| `direction` | string | **`매집` / `대피` / `중립`** — 그 값이 어느 쪽 신호인지. UI가 이 값으로 색을 칠한다 |
| `interpretation` | string | 왜 그 방향인지 + 과거 적중 사례와 한계 |
| `source_url` | string | 출처(권장) |

### `korea_property`

```json
{ "as_of": "2026-11-17",
  "listings": { "seoul": 80477, "metro": 246800, "direction": "증가", "note": "..." },
  "price": { "seoul_weekly": 0.03, "metro_weekly": 0.02, "note": "..." },
  "jeonse_ratio": 66.4,
  "source_urls": ["https://..."] }
```

- `listings.seoul`·`listings.metro`는 **건수(number)** — 모르면 `null`. `direction`은 `증가`/`보합`/`감소`.
- `price.seoul_weekly`·`metro_weekly`는 **주간 매매가격지수 변동률 %(number)** — 모르면 `null`.
- `jeonse_ratio`는 %(number). 현재 값은 **KB 전국 아파트 전세가율**(지역별 전세가율은 공표 기준이 달라 섞지 않는다).
- 확인되지 않은 값은 키를 넣지 않거나 `null`로 둔다(UI는 `—`로 표시). **추정치를 채우지 않는다.**

## history append 규칙

1. 새 스냅숏에서 `as_of`·`regime`·`assets`·`signals`·`korea_property`를 새 값으로 갱신한다.
2. `history`에 같은 내용(간략형: `as_of`·`regime`·`assets`·`signals`·`korea_property`)을 append한다.
3. 최상위 값과 `history`의 마지막 항목은 항상 같아야 한다. 차트·추세는 `history`만 읽는다.
4. 같은 날짜를 다시 갱신하면 항목을 추가하지 말고 마지막 항목을 덮어쓴다.

## 공개 저장소 주의

공개 저장소다. 계좌번호·보유 종목·매수 단가·개인정보를 넣지 않는다. 시장 전체 수치만 기록한다.
