# JH_HOLDINGS 현재 작업 상태

> 이 문서는 **현재 운영 상태만 보는 롤링 상태판**입니다. 전략 설명은 [`docs/STRATEGY_GUIDE.md`](docs/STRATEGY_GUIDE.md), 투자전략 계약은 [`docs/JDSS_FINAL_SPEC.md`](docs/JDSS_FINAL_SPEC.md), 자동운용 계약은 [`docs/JH_AUTO_SPEC.md`](docs/JH_AUTO_SPEC.md), Telegram 운영은 [`docs/TELEGRAM_BOT_GUIDE.md`](docs/TELEGRAM_BOT_GUIDE.md), 실계좌 연결·배포·복구는 [`docs/infra/DEPLOYMENT.md`](docs/infra/DEPLOYMENT.md), 주문 안전불변식은 [`docs/infra/SECURITY.md`](docs/infra/SECURITY.md)를 따릅니다.

## 1. 마지막 확인된 운영 상태

- 투자전략: **JDSS 3.2.2**
- 전략 ID: **`JDSS-3.2.2-RS6M-ONEWAY-HWM75`**
- config/package: **3.2.2**
- 자동매매 실행계층: **JH AUTO 1.0.0**
- Oracle 실거래 runtime 배포본: **`fac090b3bca02541c17d7fddb8c2678873754aeb`**
- Oracle 서비스: **active**
- 운용 모드: **실계좌 연결(`trading_mode=live`)**
- 실거래 준비 완료 표시(`live_commissioned`): **ON**
- 신규 BUY 잠금: **ON (`operator_buy_halt=1`)**
- JH AUTO 최초 시작승인: **배포 작업에서 수행하지 않음 — 실제 승인상태는 Telegram에서 확인**
- 자동운용 실제 BUY: **배포 작업으로 시작하지 않음**
- `portfolio.live_enabled`: **false** — 일반 경로 오작동 방지 잠금 유지
- SGOV 자동운용: **OFF**
- 관리종목: **QQQ / TQQQ / SOXL**
- 주문: **정수주만 허용**
- 위험축소 SELL: **자동**, 불확실 상태에서는 안전정지와 계좌·원장 대조 우선
- UNKNOWN 주문: **자동 재전송 금지**

2026-09-10 한국시간 운영배포 후 외부 점검에서 위 runtime SHA, systemd active, SQLite quick check, 스케줄러 heartbeat, 설정 검증, 서버 시계/디스크 상태를 확인했습니다. 배포 중 Toss read-only 인증·QQQ/TQQQ/SOXL 가격 조회와 Telegram 운영 메뉴 검증도 통과했으며, 신규 BUY 잠금은 기존 최초운용 전 안전상태대로 유지했습니다.

## 2. 최근 완료 상태

- **2026-09-10 runtime provider self-healing 운영반영**: Toss `token-revoked` 읽기 복구, 동일 호스트 프로세스용 file-lock 공유 토큰 캐시, 주문/취소 POST blind replay 금지 유지, Yahoo 일봉 bounded retry + LIVE 독립 fallback 반영
- yfinance 1.6.0 `repair=True`가 이상가격 복구 시 요구하는 `scikit-learn==1.9.0`을 production dependency로 명시하고 canonical V3.2.2 backtest 재검증 통과
- PR #366 Quality/Security/Backtest 통과 후 `main` squash merge, main push Quality/Security 재검증, Oracle Live-Armed 배포와 별도 외부 health watch까지 PASS
- CCI Swing Bot 토큰공유/POST 무재전송 보강은 private `cci_nvdl` main `bc9a15d752191b57bbefa14b73dfb26e00ab590d`에 병합 및 CI 통과. CCI Oracle runtime은 보안경계상 신뢰 실행환경에서 별도 배포할 때까지 기존 `2302e8e91d95c945d30dacf48672959858a4fa1a` 유지
- **CCI Swing Bot v4.0.1 운영배포 완료(2026-09-08)**: exact source SHA `2302e8e91d95c945d30dacf48672959858a4fa1a`, Oracle `cci_bot` systemd active, Toss NVDL/USD read-only 및 OPEN 주문 조회, Telegram `/ping`·`/status`·`/order` 핸들러와 운영 알림, 최근 fatal 예외 점검을 모두 통과
- CCI Oracle runtime은 **managed CPython 3.12.13**으로 배포됨
- CCI v4.0.1 1회성 encrypted payload와 Oracle private key는 성공 시 폐기되었으며, 임시 key/patch/encrypted-deploy workflow는 운영 완료 후 제거
- cross-repository reusable deploy는 CCI 호출 시 JH Environment secret이 상속되지 않는 GitHub 저장소 경계를 실검증한 뒤 **폐기**. JH Oracle secret을 CCI 저장소에 중복 저장하지 않는 원칙 유지
- CCI 자동배포는 fail-closed로 제거하고, CCI 저장소의 `deploy.sh`를 managed Python 3.12 staging venv/rollback 방식으로 유지하며 신뢰 실행환경에서만 배포하도록 운영 경계를 확정
- 첫 운용 승인·주문감시 안전강화와 Telegram 개선: 운영 반영 완료
- 검증된 DB 연결·트랜잭션 개선의 배포 호환성: 운영 반영 완료
- 알림 전송 실패와 주문감시 분리, 예상하지 못한 감시 오류의 BUY 차단 및 재점검
- 과거 설정 확인버튼·중복승인 차단, 시작·자금변경·단계확대 원자적 저장
- 긴급정지·임시격리 중 자금확대 중지, 시작 전 성과와 다음 행동·체결내역 표시 개선
- 운영코드 기준 전체 테스트 498건, 커버리지 71.48%, Ruff·설정·셸 문법 검사 통과 이력
- GitHub 품질·보안·기존 전략 백테스트 통과 및 운영배포 사전 안전테스트 145건 통과 이력
- 문서 체계는 **13개 현행 Markdown**으로 통합: 한 규칙 = 한 소유문서 원칙 적용
- `ONE_PAGE_REPORT` → `STRATEGY_GUIDE` 통합
- Telegram 메시지 표준 → `TELEGRAM_BOT_GUIDE` 통합
- pre-live hardening → `JH_AUTO_SPEC`/보안계약 통합
- live commissioning → `DEPLOYMENT`/`JH_AUTO_SPEC`/Telegram/보안계약으로 분해 통합
- 과거 strategy freeze 결정 → `HISTORY` 요약, 상세는 Git 이력 보존
- 문서 지도·소유권 → 루트 `README`로 통합

## 3. 현재 운영 행동

최초 시작은 운영자가 Telegram에서 운용 기준자금·비율·계좌를 확인한 뒤 직접 승인합니다.

```text
/dashboard
→ /auto에서 자금·비율 확인/설정
→ /account
→ /auto start
→ 2단계 확인
→ 해당 callback 주문 0건
→ 다음 독립 안전주기 재검증
```

`/halt`는 시스템이 자동으로 해제하지 않으며, 배포나 서비스 재시작은 `/resume` 또는 최초 시작승인을 대신하지 않습니다.

## 4. 문서와 runtime 관계

상태문서 정리는 JH AUTO 전략·주문·DB·Oracle runtime 동작을 변경하지 않습니다. CCI는 별도 저장소/서비스이며, JH_HOLDINGS에는 CCI 1회성 및 cross-repository 배포 workflow를 두지 않습니다. CCI 배포 절차와 보안 경계는 CCI 저장소 `docs/OPERATIONS.md`를 기준으로 관리합니다.

완료된 과거 작업의 상세는 [`docs/HISTORY.md`](docs/HISTORY.md), 병합 PR, Git tag와 Actions artifact에서 확인합니다.
