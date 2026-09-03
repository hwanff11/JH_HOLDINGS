# JH_HOLDINGS 현재 작업 상태

> 이 문서는 **현재 운영 상태만 보는 롤링 상태판**입니다. 전략 설명은 [`docs/STRATEGY_GUIDE.md`](docs/STRATEGY_GUIDE.md), 투자전략 계약은 [`docs/JDSS_FINAL_SPEC.md`](docs/JDSS_FINAL_SPEC.md), 자동운용 계약은 [`docs/JH_AUTO_SPEC.md`](docs/JH_AUTO_SPEC.md), Telegram 운영은 [`docs/TELEGRAM_BOT_GUIDE.md`](docs/TELEGRAM_BOT_GUIDE.md), 실거래 전환·사고 대응은 [`docs/infra/LIVE_COMMISSIONING.md`](docs/infra/LIVE_COMMISSIONING.md)를 따릅니다.

## 1. 현재 운영 상태

- 투자전략: **JDSS 3.2.2**
- 전략 ID: **`JDSS-3.2.2-RS6M-ONEWAY-HWM75`**
- config/package: **3.2.2**
- 자동매매 실행계층: **JH AUTO 1.0.0**
- Oracle 실거래 runtime 배포본: **`a09c6bad2f95a002cc86ff3692a851a35c0d2adf`**
- 최신 `main`에는 위 runtime과 동일한 코드에 현재상태 문서 정리만 추가될 수 있으며, 문서-only 변경은 Oracle에 재배포하지 않습니다.
- Oracle 서비스: **active**
- 운용 모드: **실계좌 연결(`trading_mode=live`)**
- 실거래 준비 완료 표시(`live_commissioned`): **ON**
- 신규 BUY 잠금: **ON (`operator_buy_halt=1`)**
- JH AUTO 최초 시작승인: **이번 배포에서 수행하지 않음**
- 자동운용 실제 BUY: **아직 시작하지 않음**
- `portfolio.live_enabled`: **false** — 일반 경로 오작동 방지 잠금 유지
- SGOV 자동운용: **OFF**
- 관리종목: **QQQ / TQQQ / SOXL**
- 주문: **정수주만 허용**
- 위험축소 SELL: **자동**, 불확실 상태에서는 안전정지와 계좌·원장 대조 우선
- UNKNOWN 주문: **자동 재전송 금지**

## 2. JH AUTO 1.0.0 반영 완료

PR #312에서 JDSS 3.2.2 투자전략 수학은 유지하고 다음 실행 책임을 JH AUTO로 분리했습니다.

- 운영자가 정하는 운용 기준자금과 자동운용비율
- 실제 허용원금과 50→75→100 자금개방 단계
- 자금 유입·회수를 투자수익과 분리하는 성과회계
- 자동 BUY 승인·실행
- 최초 시작승인과 서버 재시작 임시격리
- 대표 `/halt` 지속정지표시
- Telegram 자동운용 통제·감시 화면

자동 BUY는 기존 `TradingService → OrderManager` 최종 주문 안전경계를 우회하지 않습니다.

## 3. 2026-09-03 실거래 안전배포 검증

소유자 전용 LIVE-ARMED 배포로 runtime 변경을 포함한 최신 검증본을 Oracle에 반영했습니다.

검증 결과:

- 배포 전 안전검사 **79개 통과**
- Ruff **통과**
- JDSS 설정 검증 **통과**
- 실거래 전략계약 **통과**
- 배포 전·후 `LIVE_COMMISSIONED=PASS`
- 배포 전·후 `BUY_HALT=1`
- 실거래 DB 안전검사 `safe=true`, 확인된 문제 없음
- Toss 조회전용 인증·QQQ/TQQQ/SOXL 시세 확인 **통과**
- Telegram 운영자 메뉴 확인 **통과**
- 기존 live DB 보존
- `/resume` 자동 실행하지 않음
- JH AUTO 최초 시작승인을 배포 작업으로 대신하지 않음

## 4. 현재 BUY 안전계약

현재는 **배포 완료 상태이지 자동운용 시작 상태가 아닙니다.**

자동매수를 시작하려면 다음 조건을 모두 통과해야 합니다.

1. 운영자가 Telegram에서 기준자금과 자동운용비율을 확정
2. `/auto start`의 2단계 최초 시작확인을 운영자가 직접 수행
3. 시작확인 처리 자체에서는 Toss 주문 0건
4. 다음 독립 안전주기에 계좌·원장 일치 확인
5. 미체결·UNKNOWN 없음
6. 안전정지 없음
7. 대표 긴급정지 없음
8. 미국 정규장
9. 최신 목표·가격·수량 재계산
10. OrderManager 최종검증

JH AUTO는 한 안전주기에서 신규 BUY를 최대 1건만 실행합니다. 부분체결·거부·불명확 결과가 발생해도 잔여수량을 즉시 반복주문하지 않습니다.

## 5. 대표 긴급정지와 재시작

- `/halt`는 신규 BUY를 즉시 막는 **지속되는 대표 긴급정지**입니다.
- 계좌가 정상으로 다시 보여도 시스템이 `/halt`를 자동으로 해제하지 않습니다.
- `/resume`은 기존 2단계 확인과 계좌·원장 재대조를 통과해야 합니다.
- 서버 재시작·배포는 대표 긴급정지와 별개의 **임시격리**로 시작합니다.
- 자동운용이 시작된 이후에도 재시작 직후에는 안전조건을 다시 증명한 뒤에만 자동운전으로 복귀합니다.

## 6. 바로 다음 작업

현재 코드·배포·실거래 안전검증은 완료했습니다. 다음 단계는 **대표의 JH AUTO 최초 운용설정과 소액 자동운용 시작**입니다.

Telegram에서 다음 순서로 진행합니다.

1. `/dashboard` — 실계좌·신규 BUY 차단·안전정지 상태 확인
2. `/auto` — 현재 JH AUTO 설정 확인
3. `/auto capital <금액>` — 운용 기준자금 설정 후 2단계 확정
4. `/auto ratio <비율>` — 자동운용비율 설정 후 2단계 확정
5. `/account` — 실제 매수가능금액과 관리종목 확인
6. `/auto start` — **대표가 직접** 최초 자동운용 시작을 2단계 확정
7. 첫 실제 BUY는 버튼 처리 중이 아니라 다음 정상 안전주기에서 발생 여부를 관찰

초기에는 소액으로 시작하고, 자동주문·체결·원장반영·계좌대조가 반복해서 정상임을 확인한 뒤 자동운용 원금을 단계적으로 높입니다.
