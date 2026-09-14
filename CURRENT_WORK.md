# JH_HOLDINGS 현재 작업 상태

> 이 문서는 **현재 운영 상태만 보는 롤링 상태판**입니다. 전략 설명은 [`docs/STRATEGY_GUIDE.md`](docs/STRATEGY_GUIDE.md), 투자전략 계약은 [`docs/JDSS_FINAL_SPEC.md`](docs/JDSS_FINAL_SPEC.md), 자동운용 계약은 [`docs/JH_AUTO_SPEC.md`](docs/JH_AUTO_SPEC.md), Telegram 운영은 [`docs/TELEGRAM_BOT_GUIDE.md`](docs/TELEGRAM_BOT_GUIDE.md), 실계좌 연결·배포·복구는 [`docs/infra/DEPLOYMENT.md`](docs/infra/DEPLOYMENT.md), 주문 안전불변식은 [`docs/infra/SECURITY.md`](docs/infra/SECURITY.md)를 따릅니다.

## 1. 마지막 확인된 운영 상태

- 투자전략: **JDSS 3.2.2**
- 전략 ID: **`JDSS-3.2.2-RS6M-ONEWAY-HWM75`**
- config/package: **3.2.2**
- 자동매매 실행계층: **JH AUTO 1.0.0**
- Oracle 실거래 runtime 배포본: **`87985cc6255d3b99a2722f50ed4a657b75ef26f3`**
- Oracle service: **active**
- 운용 모드: **실계좌 연결(`trading_mode=live`)**
- `live_commissioned`: **ON**
- 신규 BUY 잠금: **ON (`operator_buy_halt=1`)**
- `portfolio.live_enabled`: **false** — 일반 경로 오작동 방지 잠금 유지
- SGOV 자동운용: **OFF**
- 관리종목: **QQQ / TQQQ / SOXL**
- 주문: **정수주만 허용**
- `/auto start` / `/resume`: **배포 작업에서 실행하지 않음**

2026-09-14 PR #377의 아침 브리핑 시간창 개선은 Quality / Security / canonical Backtest를 모두 통과한 뒤 `main`에 병합했고, Issue #378 Live-Armed ChatOps로 위 exact SHA를 Oracle에 배포했습니다. 배포 중 신규 BUY 잠금을 보존했고, live-release 안전검사 149건, DB 호환성·계좌/원장 정합성, Toss read-only 인증·QQQ/TQQQ/SOXL 가격 조회, Telegram 운영메뉴 확인을 모두 통과했습니다. 이후 Issue #379 외부 health watch에서 service active, SQLite `quick_check`, scheduler heartbeat, config, clock/disk 상태를 다시 확인해 **PASS**했습니다. 외부 health process는 LIVE token 보호를 위해 독립 Toss token을 발급하지 않습니다.

## 2. 2026-09-14 아침 브리핑 시간 정책

정규 전략 브리핑은 미국 정규장이 완전히 끝난 뒤 **한국시간 07:00**부터 직전 완결 미국장 데이터를 기준으로 작성합니다. `strategy.yaml`의 `daily_analysis_time_kst: "07:00"`은 그대로 유지하고, **분석/복구 작업과 Telegram 표시 시간창을 분리**했습니다.

```text
07:00 KST
→ 직전 완결 미국장 데이터로 일일 분석·목표비중 계산
→ 정규 아침 브리핑 전송

07:00~09:00
→ provider 지연이면 기존 bounded recovery로 재시도
→ 성공하면 정규 아침 브리핑 전송 가능

09:00 KST까지 미복구
→ [오늘 아침 브리핑 미확정] 하루 1회 알림
→ 신규 BUY 차단 유지
→ 백그라운드 데이터 복구는 계속

09:00 이후 provider 복구
→ 전체 아침 브리핑 및 routine allocation 요약은 지연전송하지 않음
→ [일일 시세 자동복구 완료]만 1회 알림
→ 다음 독립 안전주기에서 주문 가능 여부 재검증

미국장 개장 이후
→ 실제 주문·체결·거부·오류·SAFE_MODE 등 필요한 운영 이벤트만 알림
```

이 시간창은 **Telegram 표시 정책**이며 거래·안전 로직의 시간 기준을 바꾸지 않습니다. 실제 주문 가능 세션은 NYSE 캘린더를 계속 사용하므로 미국 서머타임에 따라 한국시간 개장시각이 바뀌어도 별도 하드코딩이 없습니다.

또한 `/dashboard`, `/today`, `/account`, `/portfolio`처럼 운영자가 직접 요청하는 화면은 09:00 이후에도 정상 응답합니다. 주문감시, 계좌·원장 reconciliation, 위험축소 SELL, 실제 주문/체결 알림, 오류·복구 알림도 시간창 때문에 숨기지 않습니다.

## 3. provider 장애 복구 계약

### Toss `token-revoked` / 일시적 broker read 장애

JH_HOLDINGS와 CCI Swing Bot은 동일 호스트에서 같은 Toss client credential을 사용할 수 있는 구조를 고려해 **file-lock 기반 shared Toss token cache**로 token refresh를 process 간 직렬화합니다.

```text
GET 일시오류 / token-revoked / expired / invalid / retryable 429·timeout·5xx
→ bounded read retry
→ 그래도 실패하면 신규 BUY 임시격리
→ holdings + OPEN orders + known core-order status 재조회
→ canonical 계좌·원장 정합성 2회 연속 PASS
→ 다른 안전조건도 정상일 때만 system quarantine 자동복구
```

- 단순 broker GET 장애만으로 원장 손상으로 오인한 sticky SAFE_MODE를 만들지 않습니다.
- 정상 응답 뒤 실제 수량불일치, `UNKNOWN`, 주문 identity 불일치가 확인되면 기존처럼 **sticky SAFE_MODE + 수동 복구**입니다.
- 운영자 `/halt` durable latch는 자동복구가 해제하지 않습니다.
- 주문/취소 POST는 인증갱신·timeout·connection ambiguity 뒤 **blind retry 금지**를 유지합니다.

### 일봉 provider 장애

- 최신 일봉을 얻지 못하면 전일 stale cache를 오늘 데이터처럼 사용하지 않습니다.
- 재시도 간격은 **5 → 10 → 15 → 30 → 60분**, 이후 필요 시 60분 간격입니다.
- 최신 데이터 확보 전 신규 BUY는 fail-closed로 차단합니다.
- 복구가 끝난 바로 그 호출에서 위험증가 주문을 이어서 보내지 않고 다음 독립 안전주기에서 재검증합니다.
- Toss daily candle을 전략 fallback으로 쓰는 것은 조정주가/분할/전략 parity 검증 전까지 활성화하지 않습니다.

## 4. CCI Swing Bot 연계 상태

CCI 저장소도 같은 shared Toss token 계약과 write no-replay 안전경계를 사용합니다. CCI는 Oracle 전용 self-hosted runner와 owner-only Issue ChatOps를 갖추었고, **PC/터미널 없이 ChatGPT가 exact `main`을 production에 배포하고 health/결과보고/Issue close까지 완료하는 경로를 실검증**했습니다.

CCI의 상세 현재상태와 배포계약은 CCI 저장소 `CURRENT_WORK.md` / `docs/OPERATIONS.md`가 소유합니다. JH 저장소에 CCI용 SSH secret을 복제하거나 cross-repository secret 상속을 전제로 하지 않습니다.

## 5. ChatGPT 작업·배포 기본계약

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

단, 이 포괄적 배포승인은 `/auto start`, `/resume`, 운영자 `/halt` 해제, 운용 기준자금/자동운용비율 임의 변경, 안전검증을 우회한 BUY를 승인하는 뜻이 아닙니다.

## 6. 현재 운영 행동

최초 시작 또는 운영자 정지 해제는 배포와 별개입니다.

```text
/dashboard
→ /auto에서 자금·비율 확인/설정
→ /account
→ 필요할 때 운영자가 /auto start 또는 정식 /resume 절차 수행
→ callback 자체 주문 0건
→ 다음 독립 안전주기 재검증
```

`/halt`는 시스템이 자동으로 해제하지 않으며, 배포나 서비스 재시작은 `/resume` 또는 최초 시작승인을 대신하지 않습니다.

## 7. 현재 판정

- 07:00~09:00 정규 아침 브리핑 시간창 코드/회귀테스트: **완료**
- PR #377 Quality / Security / canonical Backtest: **PASS**
- Oracle LIVE runtime `87985cc6255d3b99a2722f50ed4a657b75ef26f3` 배포: **완료**
- 배포 후 계좌·원장/Toss read-only/Telegram 안전검증: **PASS**
- 외부 post-deploy health: **PASS**
- 운영자 BUY halt: **ON 유지**
- 이번 변경 관련 추가 운영배포 대기사항: **없음**

이 문서 갱신은 runtime 동작을 바꾸지 않는 문서-only 마감이므로 CI/merge 후 Oracle runtime을 불필요하게 재시작하지 않습니다.
