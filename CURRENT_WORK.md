# JH_HOLDINGS 현재 작업 상태

> 이 문서는 **현재 운영 상태만 보는 롤링 상태판**입니다. 전략 설명은 [`docs/STRATEGY_GUIDE.md`](docs/STRATEGY_GUIDE.md), 투자전략 계약은 [`docs/JDSS_FINAL_SPEC.md`](docs/JDSS_FINAL_SPEC.md), 자동운용 계약은 [`docs/JH_AUTO_SPEC.md`](docs/JH_AUTO_SPEC.md), Telegram 운영은 [`docs/TELEGRAM_BOT_GUIDE.md`](docs/TELEGRAM_BOT_GUIDE.md), 실거래 전환·사고 대응은 [`docs/infra/LIVE_COMMISSIONING.md`](docs/infra/LIVE_COMMISSIONING.md)를 따릅니다.

## 1. 마지막 확인된 운영 상태

- 투자전략: **JDSS 3.2.2**
- 전략 ID: **`JDSS-3.2.2-RS6M-ONEWAY-HWM75`**
- config/package: **3.2.2**
- 자동매매 실행계층: **JH AUTO 1.0.0**
- Oracle 실거래 runtime 배포본: **`b111c14192e6e7b1b267cd38f24654b25328ee82`**
- Oracle 서비스: **active**
- 운용 모드: **실계좌 연결(`trading_mode=live`)**
- 실거래 준비 완료 표시(`live_commissioned`): **ON**
- 신규 BUY 잠금: **ON (`operator_buy_halt=1`)**
- JH AUTO 최초 시작승인: **이번 작업에서 수행하지 않음 — 실제 승인 상태는 Telegram에서 확인**
- 자동운용 실제 BUY: **배포 작업으로 시작하지 않음**
- `portfolio.live_enabled`: **false** — 일반 경로 오작동 방지 잠금 유지
- SGOV 자동운용: **OFF**
- 관리종목: **QQQ / TQQQ / SOXL**
- 주문: **정수주만 허용**
- 위험축소 SELL: **자동**, 불확실 상태에서는 안전정지와 계좌·원장 대조 우선
- UNKNOWN 주문: **자동 재전송 금지**

2026-09-07 한국시간 배포 후 외부 점검에서 위 운영 SHA, 서비스 정상, DB 무결성, 주문감시 최근 동작시각, 실계좌 연결 및 신규매수 잠금 유지를 확인했습니다. 배포 중 계좌·원장 대조, Toss 읽기 전용 점검, Telegram 운영자 메뉴 확인도 통과했습니다. 외부 점검에서는 운영 토큰 보호를 위해 별도 Toss 토큰을 발급하지 않았습니다.

## 2. 완료한 작업과 검증

- 첫 운용 승인·주문감시 안전강화와 Telegram·문서 정리: **PR #334 병합·운영 반영 완료**
- 검증된 DB 연결·트랜잭션 개선의 배포 호환성: **PR #336 병합·운영 반영 완료**
- 알림 전송 실패와 주문감시 분리, 예상하지 못한 감시 오류의 매수 차단 및 재점검
- 과거 설정의 확인 버튼·중복 승인을 차단하고 시작·자금변경·단계확대를 원자적으로 저장
- 긴급정지·임시격리 중 자금확대 중지, 시작 전 성과와 다음 행동·주문 체결내역 표시 개선
- 전체 테스트 **498건 통과**, 커버리지 **71.48%**, Ruff·설정·셸 문법 검사 통과
- GitHub 품질·보안·기존 전략 백테스트 통과, 운영 배포 사전 안전 테스트 **145건 통과**
- 첫 배포는 DB 파일 전체 동일성 검사에서 운영 전환 전 중단. 정확한 검증 파일 쌍만 허용하도록 보완 후 배포 성공
- 현재 문서 마감은 운영 코드·설정 변경이 없어 재배포 대상이 아님

## 3. 작업 마감과 다음 운영 행동

요청한 코드 개선·운영 배포·배포 후 확인을 완료했습니다. 미배포 기능 변경은 없습니다. 현재 문서와 배포본의 SHA가 달라도 위 운영 SHA 이후 변경이 문서뿐이면 정상입니다. 상세 실행 기록은 병합 PR과 Actions에서 확인합니다.

최초 시작은 대표가 Telegram에서 운용 기준자금·비율·계좌를 확인한 뒤 직접 승인합니다. `/dashboard` → `/auto` → 자금·비율 설정 → `/account` → `/auto start` 순서이며, 최초 시작 버튼 자체는 주문을 제출하지 않습니다. 다음 독립 안전주기에서 주문 조건을 재검증합니다. `/halt`는 시스템이 자동으로 해제하지 않으며 배포로 매수 재개나 최초 시작승인을 대신하지 않습니다.

## 4. 기준 문서

전략 수학은 `strategy.yaml`과 [투자전략 계약](docs/JDSS_FINAL_SPEC.md), 자동운용은 [JH AUTO 계약](docs/JH_AUTO_SPEC.md), 운영 화면은 [Telegram 가이드](docs/TELEGRAM_BOT_GUIDE.md)를 따릅니다. 완료된 과거 작업은 [이력](docs/HISTORY.md)과 병합 PR에서 확인합니다.
