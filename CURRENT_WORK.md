# JH_HOLDINGS 현재 작업 상태

> 이 문서는 **현재 운영 상태만 보는 롤링 상태판**입니다. 전략 설명은 [`docs/STRATEGY_GUIDE.md`](docs/STRATEGY_GUIDE.md), 투자전략 계약은 [`docs/JDSS_FINAL_SPEC.md`](docs/JDSS_FINAL_SPEC.md), 자동운용 계약은 [`docs/JH_AUTO_SPEC.md`](docs/JH_AUTO_SPEC.md), Telegram 운영은 [`docs/TELEGRAM_BOT_GUIDE.md`](docs/TELEGRAM_BOT_GUIDE.md), 실계좌 연결·배포·복구는 [`docs/infra/DEPLOYMENT.md`](docs/infra/DEPLOYMENT.md), 주문 안전불변식은 [`docs/infra/SECURITY.md`](docs/infra/SECURITY.md)를 따릅니다.

## 1. 마지막 확인된 운영 상태

- 투자전략: **JDSS 3.2.2**
- 전략 ID: **`JDSS-3.2.2-RS6M-ONEWAY-HWM75`**
- config/package: **3.2.2**
- 자동매매 실행계층: **JH AUTO 1.0.0**
- Oracle 실거래 runtime 배포본: **`323a6a54727c926fb4d50f3a9ebbd4528e0f6991`**
- Oracle service: **active**
- 운용 모드: **실계좌 연결(`trading_mode=live`)**
- `live_commissioned`: **ON**
- 신규 BUY 잠금: **ON (`operator_buy_halt=1`)**
- `portfolio.live_enabled`: **false** — 일반 경로 오작동 방지 잠금 유지
- SGOV 자동운용: **OFF**
- 관리종목: **QQQ / TQQQ / SOXL**
- 주문: **정수주만 허용**
- `/auto start` / `/resume`: **배포 작업에서 실행하지 않음**

2026-09-16 PR #394의 **Yahoo → Toss adjusted daily candle 실운영 이중화**를 Quality / Security / canonical Backtest 모두 PASS 후 `main`에 병합했습니다. Issue #395 Live-Armed ChatOps로 exact runtime `323a6a54727c926fb4d50f3a9ebbd4528e0f6991`을 Oracle에 배포했고, 배포 전 BUY halt를 재확인한 뒤 기존 live DB를 보존했습니다. 배포 안전검사 149건 PASS, DB 호환성, 계좌/원장 정합성 `safe=true`, Toss read-only 인증 및 QQQ/TQQQ/SOXL 현재가, Telegram 운영메뉴를 모두 확인했습니다. Issue #396 외부 health watch도 service active, SQLite `quick_check`, scheduler heartbeat, config, clock/disk, exact runtime SHA를 확인해 **PASS**했습니다. 외부 health process는 LIVE 운영 토큰 보호를 위해 독립 Toss token을 발급하지 않습니다.

## 2. 07:00 아침 브리핑 정책

정규 아침 브리핑은 미국 정규장이 끝난 뒤 **한국시간 07:00~09:00**에만 전달합니다. 실제 매매 allocator는 미국 정규장 안전주기만 유지하며, 아침 브리핑은 주문·원장·HWM을 변경하지 않는 **읽기전용 전략 preview**가 담당합니다.

```text
07:00 KST
→ 직전 완결 미국장 일봉 확보
→ 읽기전용 JDSS/V3.2.2 목표비중 계산
→ [JH AUTO 아침 브리핑] 전송

07:00~09:00
→ provider 지연 시 bounded recovery
→ 데이터가 확보되면 브리핑 전송

09:00 KST까지 미완료
→ [오늘 아침 브리핑 미확정] 하루 1회
→ 전체 아침 브리핑은 그날 늦게 지연전송하지 않음
→ 신규 BUY 보호상태 유지

09:00 이후 / 미국장 개장 이후
→ [JDSS 실거래 아침 브리핑], [JH AUTO 아침 브리핑], routine allocation 요약 차단
→ 실제 주문·체결·거부·오류·SAFE_MODE·복구 이벤트만 정상 알림
```

PR #390은 JH AUTO에서 제목이 normalize된 뒤에도 같은 cutoff가 적용되도록 Telegram 안전경계에서 두 아침 브리핑 제목을 모두 인식합니다. 회귀테스트는 07:00/08:59 허용, 09:00/09:13/22:30 차단을 명시합니다. 미국장 개장시간은 한국시간 22:30으로 하드코딩하지 않고 NYSE 캘린더를 계속 사용합니다.

## 3. LIVE 일봉 provider 이중화 계약

실거래 `refresh=True` 일봉은 이제 **Yahoo primary → Toss secondary**입니다. 별도 shadow 단계 없이 실운영 fallback으로 활성화했습니다.

```text
Yahoo adjusted daily OHLCV
→ 정상 + 요청한 최신 완결 거래일까지 존재: Yahoo 사용
→ 예외 또는 최신 완결 거래일 누락(stale tail): Toss fallback

Toss GET /api/v1/candles
→ interval=1d
→ adjusted=true
→ 최근 최대 400봉을 기존 long-history cache에 병합
→ 동일 거래일은 Toss recent tail을 우선
→ 전략 warmup 시작점 + 요청한 최신 완결 거래일을 모두 덮으면 사용

Yahoo + Toss 모두 최신 완결봉 미확보
→ MarketDataError
→ 기존 provider recovery
→ 신규 BUY fail-closed 유지
```

핵심 운영 원칙:

- Yahoo가 HTTP/파싱 오류를 내는 경우뿐 아니라 **응답은 성공했지만 최신 완결 거래일이 빠진 경우도** Toss fallback을 사용합니다.
- Toss는 공식 `adjusted=true` 일봉을 사용합니다.
- V3.2.2의 장기 replay/warmup을 보존하기 위해 Toss의 최근 구간을 기존 장기 시세 cache 위에 병합합니다. Toss 최근봉만으로 장기 이력이 충족되지 않으면 성공으로 간주하지 않습니다.
- 동일한 LIVE `ResilientReadTossClient` 인스턴스를 시세 fallback에도 재사용하므로 shared Toss token cache 계약을 깨지 않고 별도의 OAuth token issuer를 만들지 않습니다.
- 이 이중화는 **읽기 전용 market-data 경로**입니다. 주문/취소 POST retry, 계좌 원장, HWM, SAFE_MODE 규칙은 변경하지 않습니다.
- research/non-refresh 경로는 기존 Yahoo/cache 의미론을 유지합니다.

## 4. Toss 인증·broker read 장애 복구 계약

JH_HOLDINGS와 CCI Swing Bot은 동일 호스트에서 같은 Toss client credential을 사용할 수 있는 구조를 고려해 **file-lock 기반 shared Toss token cache**로 token refresh를 process 간 직렬화합니다.

```text
GET 일시오류 / token-revoked / expired / invalid / retryable 429·timeout·5xx
→ bounded read retry
→ 그래도 실패하면 신규 BUY 임시격리
→ holdings + OPEN orders + known core-order status 재조회
→ canonical 계좌·원장 정합성 2회 연속 PASS
→ 다른 안전조건도 정상일 때만 system quarantine 자동복구
```

- 단순 broker GET 장애만으로 sticky SAFE_MODE를 만들지 않습니다.
- 정상 응답 뒤 실제 수량불일치, `UNKNOWN`, 주문 identity 불일치가 확인되면 기존처럼 **sticky SAFE_MODE + 수동 복구**입니다.
- 운영자 `/halt` durable latch는 자동복구가 해제하지 않습니다.
- 주문/취소 POST는 인증갱신·timeout·connection ambiguity 뒤 **blind retry 금지**를 유지합니다.

## 5. CCI Swing Bot 연계 상태

CCI 저장소도 같은 shared Toss token 계약과 write no-replay 안전경계를 사용합니다. CCI는 Oracle 전용 self-hosted runner와 owner-only Issue ChatOps를 갖추었고, **PC/터미널 없이 ChatGPT가 exact `main`을 production에 배포하고 health/결과보고/Issue close까지 완료하는 경로를 실검증**했습니다.

CCI의 상세 현재상태와 배포계약은 CCI 저장소 `CURRENT_WORK.md` / `docs/OPERATIONS.md`가 소유합니다. JH 저장소에 CCI용 SSH secret을 복제하거나 cross-repository secret 상속을 전제로 하지 않습니다.

## 6. ChatGPT 작업·배포 기본계약

사용자가 JH_HOLDINGS 작업에서 **“배포해”, “배포까지”, “끝까지 마무리”**라고 명확히 지시하면 완료의 의미는 코드 수정이나 PR merge가 아닙니다.

```text
현재 main / CURRENT_WORK 확인
→ branch 수정 + regression test
→ PR + 필수 CI
→ 실패하면 원인 수정 후 CI 재검증
→ main merge
→ owner-only Live-Armed Issue ChatOps로 Oracle production 배포
→ exact runtime / DB / scheduler / reconciliation / Telegram / read-only 상태 확인
→ 별도 Oracle health watch 확인
→ 새 문제가 생기면 수정 → CI → merge → 재배포 → health 반복
→ 성공 이슈 정리
→ CURRENT_WORK 마감
```

**runtime 영향 작업의 Definition of Done = CI PASS + merge + production deploy PASS + post-deploy health PASS**입니다.

이 포괄적 배포승인은 `/auto start`, `/resume`, 운영자 `/halt` 해제, 운용 기준자금/자동운용비율 임의 변경, 안전검증을 우회한 BUY를 승인하는 뜻이 아닙니다.

## 7. 현재 판정

- 07:00~09:00 읽기전용 아침 브리핑 + 09:00 이후 full brief 차단: **운영 적용 완료**
- Yahoo primary → Toss adjusted daily secondary: **실운영 활성화 완료**
- PR #394 Quality / Security / canonical Backtest: **PASS**
- Oracle LIVE runtime `323a6a54727c926fb4d50f3a9ebbd4528e0f6991`: **배포 완료**
- Live-Armed deploy run `34987037549`: **PASS**
- post-deploy health run `34987707469`: **PASS**
- 계좌·원장 정합성: **safe=true**
- 운영자 BUY halt: **ON 유지**
- `/auto start` / `/resume`: **미실행**
- 추가 운영배포 대기사항: **없음**

이 문서 갱신은 runtime 동작을 바꾸지 않는 문서-only 마감이므로 CI/merge 후 검증된 Oracle runtime을 불필요하게 재시작하지 않습니다.
