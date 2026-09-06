# JH_HOLDINGS Agent Instructions

이 파일은 Codex, ChatGPT, IDE가 이 저장소에서 작업할 때 반드시 지킬 공통 규칙입니다.

## 1. 현재 구조

- **JDSS 3.2.2**: 시장판단, 목표비중, RS6M, HWM75 전략 수학
- **JH AUTO 1.0.0**: 운용 기준자금, 자동운용비율, 자동승인·주문, 최초 시작, 정지·복구, 성과회계
- GitHub 저장소 `hwanff11/JH_HOLDINGS`가 Source of Truth입니다.
- 현재 배포·실거래 상태는 `CURRENT_WORK.md`가 소유합니다.
- 전략 숫자는 `strategy.yaml`, 전략 계약은 `docs/JDSS_FINAL_SPEC.md`, 자동운용 계약은 `docs/JH_AUTO_SPEC.md`가 소유합니다.

전략과 자동매매 버전을 섞지 않습니다. 자동매매 안전기능을 고쳤다고 JDSS 전략 버전을 올리지 않습니다.

## 2. 실거래 절대 안전규칙

1. 배포·재시작은 `/auto start` 승인으로 해석하지 않습니다.
2. `launch_authorized=1`이 운영자의 최신 2단계 확인으로 기록되기 전 신규 매수는 0건이어야 합니다.
3. 최초 시작 확인 버튼 자체에서는 주문을 보내지 않습니다. 실제 주문은 이후 독립 안전주기에서 다시 검증합니다.
4. 운영자 `/halt`는 시스템이 자동해제할 수 없습니다.
5. `/halt` 중에는 신규 매수뿐 아니라 50→75→100 자금단계 확대도 멈춥니다.
6. 주문 결과가 `UNKNOWN`이거나 계좌·원장이 다르면 자동 재주문하지 않고 신규 매수를 차단합니다.
7. 자동 매수는 `TradingService → OrderManager → Toss` 최종 안전경계를 우회하지 않습니다.
8. Telegram 전송 실패가 이미 제출된 주문의 감시·계좌대조를 멈추게 해서는 안 됩니다.
9. 동일 live SQLite 원장에 두 운영 프로그램이 동시에 붙지 못해야 합니다.
10. 실제 자동 allocation 주문은 미국 정규장에만 실행합니다.

## 3. 문서 우선순위

같은 내용이 다르면 다음 순서로 확인합니다.

1. 현재 GitHub/Oracle 실제 상태 + `CURRENT_WORK.md`
2. 실행 숫자 `strategy.yaml`
3. 전략 계약 `docs/JDSS_FINAL_SPEC.md`
4. 자동운용 계약 `docs/JH_AUTO_SPEC.md`
5. 실제 구현 `src/jd_holdings/`
6. 운영 가이드
7. 과거 이력 `docs/HISTORY.md`, Git tag, PR, Actions artifact

불일치가 있으면 임의로 하나를 맞다고 가정하지 않고 결함으로 처리합니다.

## 4. 작업 시작

사용자가 `작업 시작`이라고 하면:

1. `CURRENT_WORK.md` 확인
2. 최신 protected `main` SHA 확인
3. 관련 계약·구현·테스트 확인
4. 기존 미완료 PR/브랜치 확인
5. 기능 변경은 별도 branch에서 시작
6. 실거래 위험이 있으면 BUY 잠금 상태부터 확인

로컬/IDE에서는 사용자 미커밋 변경을 임의로 덮어쓰지 않습니다. ChatGPT에서는 GitHub 원격 최신 파일을 다시 읽습니다.

## 5. 표준 변경 흐름

```text
최신 main 확인
→ 별도 branch
→ 재현 또는 결함 확인
→ 최소 범위 수정
→ 회귀테스트
→ 관련 문서 동기화
→ PR
→ Quality Gate + Security + Backtest
→ 최종 diff 검토
→ protected main 병합
→ runtime 영향이 있으면 현재 운영상태에 맞는 배포
→ smoke + 계좌·원장 대조 + read-only 확인
→ CURRENT_WORK를 짧은 현재상태로 갱신
```

`main` 직접 push는 금지합니다.

## 6. 배포 경로 선택

**배포 경로를 고정해서 외우지 말고 반드시 `CURRENT_WORK.md`의 실거래 준비상태를 먼저 확인합니다.**

- 실거래 준비 전 / 모의운용 서버: owner-only `[deploy-oracle-dry-run]`
- `live_commissioned=ON`인 실계좌 운영판: owner-only `[deploy-oracle-live-armed]`

LIVE-ARMED 배포는 다음 계약을 지킵니다.

- 배포 전 신규 BUY 잠금 ON
- 기존 실거래 DB·환경·계좌 연결 보존
- 미체결·UNKNOWN·계좌 불일치가 있으면 fail-closed
- pinned SSH trust
- 배포 후 서비스·DB·계좌대조·Toss read-only·Telegram 메뉴 확인
- 배포 성공 후에도 `/resume` 자동실행 금지
- 배포 성공 후에도 `/auto start` 자동실행 금지

상세 배포 절차는 `docs/infra/DEPLOYMENT.md`, 보안·실거래 안전기준은 `docs/infra/SECURITY.md`를 기준으로 합니다.

## 7. 운영자 승인 역할

운영자가 직접 승인하는 것은 다음입니다.

- 최초 `/auto start`
- 기준자금·자동운용비율 변경
- 대표 긴급정지 `/halt`
- 긴급정지 해제 `/resume`

정상 JH AUTO 운용의 **개별 자동매수마다 Telegram 최종승인을 요구하지 않습니다.** 개별 자동매수는 내부 2단계 검증과 OrderManager 최종경계를 통과해야 합니다.

확인 버튼은 검토 당시 자금·비율·운용상태에 묶여야 하며, 그 상태가 바뀌면 오래된 버튼은 무효입니다.

## 8. 변경 영향별 필수 동기화

| 변경 | 함께 확인·갱신할 기준 | 반드시 확인할 것 |
|---|---|---|
| 전략 조건·비중·지표 | `strategy.yaml`, `JDSS_FINAL_SPEC.md` | 설정 검증, no-lookahead 백테스트, OOS/비용 |
| 자동운용 계약·자금·시작 | `JH_AUTO_SPEC.md`, `SECURITY.md` | 단일 트랜잭션, stale 확인버튼, 증액/감액, 재시작 |
| 주문·승인·DB | `TELEGRAM_BOT_GUIDE.md`, `SECURITY.md` | 멱등성, 부분체결, UNKNOWN, 재시작, 계좌대조, 안전정지 |
| Telegram | `TELEGRAM_BOT_GUIDE.md` | 관리자 인증, 버튼 TTL/stale, 4096자, 전송장애가 scheduler를 죽이지 않는지 |
| Toss API | `SECURITY.md` | read retry와 write no-blind-retry 경계 |
| 배포 | `DEPLOYMENT.md`, `SECURITY.md` | BUY halt, pinned host, DB 보존, rollback 경계, smoke |
| 현재 운영상태 | `CURRENT_WORK.md` | GitHub main, Oracle runtime, BUY 잠금, 남은 작업 |

## 9. 문서 관리

- `CURRENT_WORK.md`는 **누적 일지가 아니라 롤링 상태판**입니다.
- 현재 릴리즈, source/runtime 일치, 실거래 안전상태, 남은 작업만 남깁니다.
- 지난 PR별 경과는 `docs/HISTORY.md`, Git 이력, PR에서 확인합니다.
- 현행 문서는 날짜별 복사본을 만들지 않고 제자리에서 갱신합니다.
- 운영자용 문서와 Telegram은 한글을 우선합니다.
- 공개 Markdown에 API 키, 토큰, 계좌번호, 서버 비밀값, 절대경로를 기록하지 않습니다.

## 10. 코드 품질

- 전략변경과 동작보존 리팩터링을 같은 커밋에 섞지 않습니다.
- 광범위한 `except Exception`은 scheduler/process 격리처럼 필요한 경계에서만 사용합니다.
- 외부 입력은 Telegram callback, API 응답, 설정, 주문 경계에서 검증합니다.
- 보안·안전 테스트를 통과시키기 위해 조건을 약화하지 않습니다.
- 코드 변경으로 사용자 동작이 바뀌면 관련 Markdown도 같은 작업에서 갱신합니다.

## 11. 작업 종료

사용자가 작업 종료 또는 배포까지 요청하면:

1. 변경 diff 확인
2. Quality/Security/Backtest 결과 확인
3. PR 병합
4. runtime 영향 여부 확인
5. `CURRENT_WORK.md` 기준에 맞는 배포 경로 선택
6. 배포 후 BUY 잠금·service active·DB·계좌대조·read-only·Telegram 확인
7. 실제 `/auto start`와 `/resume`은 사용자가 명시적으로 요청하지 않으면 실행하지 않음
8. `CURRENT_WORK.md`를 현재상태만 남기도록 정리
9. 최종 main SHA, 실제 Oracle runtime SHA, 테스트·배포 결과, 남은 작업을 보고