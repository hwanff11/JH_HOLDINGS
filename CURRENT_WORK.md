# JH_HOLDINGS Current Work

> 현재 전략·개발·배포·검증 상태의 단일 상태판입니다. 이전 값을 교체하는 롤링 문서이며, 상세 전략과 승인된 기준 백테스트는 [`docs/STRATEGY_GUIDE.md`](docs/STRATEGY_GUIDE.md), 공식 계약은 [`docs/JDSS_FINAL_SPEC.md`](docs/JDSS_FINAL_SPEC.md)를 따릅니다.

## 현재 릴리즈와 운영

- GitHub 저장소: **`hwanff11/JH_HOLDINGS`** (public)
- 공식 릴리즈: **`v3.2.2`**
- 전략 ID: **`JDSS-3.2.2-RS6M-ONEWAY-HWM75`**
- config/package: **3.2.2**
- Oracle runtime: **최신 main 배포 완료 / 서비스 active**
- 기능 source/runtime revision: **일치 확인 완료** (문서-only revision 제외)
- 최근 forced dry-run 배포: **성공 / smoke test 성공**
- live: **LOCKED OFF**
- Oracle 환경: **`JDSS_TRADING_MODE=dry_run` / `JDSS_LIVE_CONFIRMATION` empty**
- 설정 잠금: **`portfolio.live_enabled=false`**

## 최근 완료 작업

- yfinance 일봉 조회 장애 내성 강화와 SOXL 일시 조회 실패 fallback 검증을 완료했습니다.
- 최신 Yahoo adjusted history 변화에 맞춘 canonical guard를 제한적으로 조정했고, V3.2.2 전략·주문 계약은 변경하지 않았습니다.
- 기준 검증 결과는 CAGR 약 **21.89%**, MDD 약 **-30.94%**, Sharpe 약 **0.987**이며 초기 위험예산 $50,000, HWM75, 최초진입 50→75→100 계약도 통과했습니다.
- Quality Gate·Security Gate·JDSS V3 Backtest 통과 후 최신 main을 Oracle forced dry-run으로 배포했고 smoke test 성공을 확인했습니다.

## 현재 안전장치

- `strategy.yaml`의 `portfolio.live_enabled=false`
- 런타임 live hard lock과 빈 live confirmation
- 매수는 최신 가격·수량 검토 후 60초 최종 승인
- 위험축소 SELL은 자동이지만 미완료·UNKNOWN이면 신규 BUY 차단
- 주문 client ID 멱등성, 브로커 응답 종목·방향·수량 검증, 부분체결 delta 반영
- 시작·주기 reconciliation 불일치 시 sticky SAFE_MODE
- 최초진입 50% → 75% → 100%, 단계별 전량 체결 후 최소 3 미국 거래일, 단계 개방은 운영자 확인 필요
- 배포 workflow는 최신 `main`만 받아 pinned SSH·강제 dry-run·rollback-safe smoke를 검증

## 현재 개발 상태 — Telegram 운영 화면 정리

- 활성 개발 브랜치: **`feature/telegram-operator-ux`**
- Draft PR: **#194 `feat: simplify Telegram operator UX and order shortcuts`**
- 목적: 내부 개발용 표현인 `배분`, `오버레이`, `allocation`, `위험증가 BUY`를 운영자가 바로 이해할 수 있는 `목표비중`, `보유/목표`, `추가매수 판단`, `매수 주문 승인 대기` 중심 표현으로 정리합니다.
- 대시보드에 `매수 승인 대기 보기`, `미체결 주문 보기` 바로가기 버튼을 추가했습니다.
- 기존 매수 흐름은 이미 `매수 주문 검토하기` → 최신 가격·수량·현금·세션 재검증 → `종목 N주 모의/실매수 실행`의 2단계 승인과 주문번호·상태·체결수량 회신을 지원함을 확인했습니다.
- Toss OpenAPI `place_order()` 어댑터도 구현돼 있지만, V3.2.2 실제 live 주문은 애플리케이션 hard lock과 forced dry-run으로 계속 차단합니다. 이 UX PR은 live 잠금을 해제하지 않습니다.
- `docs/TELEGRAM_BOT_GUIDE.md`와 Telegram 포맷·버튼 테스트를 같은 PR에서 동기화했습니다.
- 현재 PR은 **아직 main 미병합 / Oracle 미배포** 상태이며 최종 CI 확인 중입니다.

## live 전환 전에만 남아 있는 항목

- 실제 Toss 관리 티커 기존 보유·열린 주문·주문가능금액의 live 전환 계획 확정
- 실제 주문 어댑터·회계·migration 리허설과 별도 명시적 live 승인
- 충분한 forced dry-run soak와 운영자 최종 확인

## 바로 다음 작업

1. PR #194의 Quality Gate·Security Gate·JDSS V3 canonical Backtest를 모두 통과시킵니다.
2. Telegram 새 버튼과 문구가 관리자 1:1 권한·기존 2단계 승인·stale callback 안전장치를 보존하는지 최종 확인합니다.
3. 병합 승인 전까지 PR은 Draft로 유지하고 Oracle에는 배포하지 않습니다.
4. 별도 live 승인 전까지 실제 Toss 주문 잠금을 해제하지 않습니다.
