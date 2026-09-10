# JH_HOLDINGS 현재 작업 상태

> 이 문서는 **현재 운영 상태만 보는 롤링 상태판**입니다. 전략 설명은 [`docs/STRATEGY_GUIDE.md`](docs/STRATEGY_GUIDE.md), 투자전략 계약은 [`docs/JDSS_FINAL_SPEC.md`](docs/JDSS_FINAL_SPEC.md), 자동운용 계약은 [`docs/JH_AUTO_SPEC.md`](docs/JH_AUTO_SPEC.md), Telegram 운영은 [`docs/TELEGRAM_BOT_GUIDE.md`](docs/TELEGRAM_BOT_GUIDE.md), 실계좌 연결·배포·복구는 [`docs/infra/DEPLOYMENT.md`](docs/infra/DEPLOYMENT.md), 주문 안전불변식은 [`docs/infra/SECURITY.md`](docs/infra/SECURITY.md)를 따릅니다.

## 1. 마지막 확인된 운영 상태

- 투자전략: **JDSS 3.2.2**
- 전략 ID: **`JDSS-3.2.2-RS6M-ONEWAY-HWM75`**
- config/package: **3.2.2**
- 자동매매 실행계층: **JH AUTO 1.0.0**
- Oracle 실거래 runtime code baseline: **`e1a7183563b9fb63f8ec9a5c9ac6a5df8194fcb4`**
- Oracle service: **active**
- 운용 모드: **실계좌 연결(`trading_mode=live`)**
- `live_commissioned`: **ON**
- 신규 BUY 잠금: **ON (`operator_buy_halt=1`)**
- `portfolio.live_enabled`: **false** — 일반 경로 오작동 방지 잠금 유지
- SGOV 자동운용: **OFF**
- 관리종목: **QQQ / TQQQ / SOXL**
- 주문: **정수주만 허용**
- `/auto start` / `/resume`: **배포 작업에서 실행하지 않음**

2026-09-10 PR #370의 provider-recovery 수정은 Quality / Security / canonical Backtest를 모두 통과한 뒤 `main`에 병합했고, Live-Armed ChatOps로 위 exact SHA를 Oracle에 배포했습니다. 배포 단계에서 BUY halt 보존, live-release 안전검사, 계좌·원장 정합성, Toss read-only 인증/가격 조회, Telegram 운영메뉴를 확인했습니다. 이후 별도 외부 health watch에서도 service active, SQLite `quick_check`, scheduler heartbeat, config, 서버 clock/disk 상태를 다시 확인했고 **PASS**했습니다. 외부 health process는 실행 중 LIVE token을 보호하기 위해 독립 Toss token을 발급하지 않습니다.

## 2. 2026-09-10 장애 최종 해결

이번 장애는 두 종류를 분리해서 해결했습니다.

### Toss `token-revoked` / 일시적 broker read 장애

JH_HOLDINGS와 CCI Swing Bot이 동일 호스트에서 같은 Toss client credential을 사용할 수 있는 구조에서, 각 프로세스가 독립적으로 access token을 발급하면 기존 token이 폐기될 수 있습니다. 해결 후에는 **file-lock 기반 shared Toss token cache**를 사용해 token refresh를 process 간 직렬화합니다.

JH의 read-only recovery 계약은 다음과 같습니다.

```text
GET 일시오류 / token-revoked / expired / invalid / retryable 429·timeout·5xx
→ bounded read retry
→ 그래도 실패하면 신규 BUY 임시격리
→ holdings + OPEN orders + known core-order status를 다시 읽음
→ canonical 계좌·원장 정합성 2회 연속 PASS
→ 다른 안전조건도 정상일 때만 system quarantine 자동복구
```

- 단순히 broker GET을 못 읽은 것만으로 **원장 손상으로 오인한 sticky SAFE_MODE를 만들지 않습니다**.
- GET이 정상 응답한 뒤 실제 수량불일치, UNKNOWN, 주문 identity 불일치가 확인되면 기존처럼 **sticky SAFE_MODE + 수동 복구**입니다.
- 운영자 `/halt` durable latch는 어떤 자동복구도 해제하지 않습니다.
- 주문/취소 POST는 인증갱신·timeout·connection ambiguity 뒤 **blind replay 금지**를 유지합니다.
- transient recovery가 완료된 바로 그 호출에서 위험증가 주문을 이어서 보내지 않고 다음 독립 안전주기에서 다시 검증합니다.

### 07:00 Yahoo 일봉 조회 장애

현재 일봉을 확보하지 못한 경우 전일 stale cache를 오늘 데이터인 것처럼 사용하지 않습니다.

- provider 오류 시 신규 BUY를 임시 차단합니다.
- 재시도 간격은 **5 → 10 → 15 → 30 → 60분**, 이후 필요 시 60분 간격입니다.
- 동일 장애 Telegram 반복 알림은 억제하고 첫 장애와 정상복구를 명확히 알립니다.
- 당일 분석이 완료되기 전에는 전일 BUY 후보를 실행하지 않습니다.
- 현재 필수 데이터가 끝내 확보되지 않으면 fail-closed를 유지합니다.
- Toss daily candle을 전략 데이터 fallback으로 사용하는 것은 조정주가/분할/전략 parity가 검증되기 전까지 활성화하지 않습니다.

## 3. CCI Swing Bot 연계 상태

CCI 저장소도 같은 shared Toss token 계약과 write no-replay 안전경계를 사용합니다. CCI는 Oracle 전용 self-hosted runner와 owner-only Issue ChatOps를 갖추었고, **PC/터미널 없이 ChatGPT가 exact `main`을 production에 배포하고 health/결과보고/Issue close까지 완료하는 경로를 실검증**했습니다.

CCI의 상세 현재상태와 배포계약은 CCI 저장소 `CURRENT_WORK.md` / `docs/OPERATIONS.md`가 소유합니다. JH 저장소에 CCI용 SSH secret을 복제하거나 cross-repository secret 상속을 전제로 하지 않습니다.

## 4. ChatGPT 작업·배포 기본계약

사용자가 JH_HOLDINGS 작업에서 **“배포해”, “배포까지”, “끝까지 마무리”**라고 명확히 지시하면, 완료의 의미는 코드 수정이나 PR merge가 아닙니다.

ChatGPT/에이전트는 별도 재확인을 반복해서 요구하지 않고 다음을 끝까지 수행합니다.

```text
현재 main / CURRENT_WORK 확인
→ branch에서 원인 수정 + regression test
→ PR + 필수 CI
→ 실패하면 원인 수정 후 CI 재검증
→ main merge
→ owner-only Live-Armed Issue ChatOps로 Oracle production 배포
→ 배포 workflow 끝까지 추적
→ exact runtime SHA / service / DB / scheduler / 계좌대조 / Telegram 확인
→ 별도 Oracle health watch 확인
→ 새 문제가 생기면 수정 → CI → merge → 재배포 → health 반복
→ 성공 이슈 정리
→ CURRENT_WORK 마감
```

즉 **runtime 영향이 있는 작업의 Definition of Done = CI PASS + merge + production deploy PASS + post-deploy health PASS**입니다.

단, 이 포괄적 배포승인은 다음을 승인하는 뜻이 아닙니다.

- `/auto start`
- `/resume`
- 운영자 `/halt` 해제
- 운용 기준자금/자동운용비율 임의 변경
- 안전검증을 우회한 BUY

이 항목은 기존 운영자 안전계약을 그대로 따릅니다.

## 5. 현재 운영 행동

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

## 6. 현재 판정

- JH provider recovery 코드/테스트/CI/main/Oracle LIVE 배포: **완료**
- JH 외부 post-deploy health: **PASS**
- CCI shared-token 코드/CI/Oracle 배포: **완료**
- CCI PC-free ChatGPT Issue ChatOps end-to-end 검증: **PASS**
- 이번 장애 관련 추가 운영배포 대기사항: **없음**

이 문서 이후 문서-only 정리는 CI/merge로 마감하며, runtime 동작이 변하지 않는 한 불필요하게 Oracle service를 재시작하지 않습니다.
