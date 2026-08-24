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

## 최근 완료 작업 — yfinance 일봉 조회 장애 내성 강화

- 07:00 일일 분석 및 V3.2.2 배분 점검에서 SOXL yfinance 조회가 일시적으로 실패하면 전체 분석이 중단되던 문제를 수정했습니다.
- yfinance 일봉 조회를 최대 3회 재시도하고 지수 백오프를 적용했습니다.
- `refresh=true` 실전 분석은 요청한 완결 거래일까지 포함하는 검증된 캐시만 fallback으로 허용하며 stale 데이터로 매매 판단하지 않습니다.
- `refresh=false` 연구·일반 조회는 기존 캐시 fallback 호환성을 유지합니다.
- yfinance `repair=True` 경로에 필요한 `scikit-learn` 런타임 의존성을 명시했습니다.
- 재시도 성공, 최신 캐시 fallback, stale cache 거부 회귀 테스트를 추가했습니다.
- 최신 Yahoo adjusted history 변화에 따른 canonical CAGR 소폭 변동을 반영해 Backtest CAGR guard를 제한적으로 조정했습니다.
- 기준 검증 결과: CAGR 약 **21.89%**, MDD 약 **-30.94%**, Sharpe 약 **0.987**. 초기 위험예산 $50,000, HWM75, 최초진입 50→75→100 계약도 통과했습니다.
- Quality Gate·Security Gate·JDSS V3 Backtest 모두 통과 후 PR #185를 병합했습니다.
- 최신 main을 Oracle forced dry-run으로 배포했고 배포 및 smoke test 전체 성공을 확인했습니다.
- 전략·배분·주문 로직은 변경하지 않았으며 live 잠금은 그대로 유지합니다.

## 현재 안전장치

- `strategy.yaml`의 `portfolio.live_enabled=false`
- 런타임 live hard lock과 빈 live confirmation
- 위험증가 BUY는 최신 가격·수량 검토 후 60초 최종 승인
- 위험축소 SELL은 자동이지만 미완료·UNKNOWN이면 신규 BUY 차단
- 주문 client ID 멱등성, 브로커 응답 종목·방향·수량 검증, 부분체결 delta 반영
- 시작·주기 reconciliation 불일치 시 sticky SAFE_MODE
- 최초진입 50% → 75% → 100%, 단계별 전량 체결 후 최소 3 미국 거래일, 단계 개방은 운영자 확인 필요
- 배포 workflow는 최신 `main`만 받아 pinned SSH·강제 dry-run·rollback-safe smoke를 검증

## 현재 개발 상태

이번 yfinance/SOXL 긴급 장애 수정 작업은 **종료** 상태입니다. Telegram 운영 화면 개선, 일일 운용보고 통합, 최초진입 로직, yfinance 장애 내성 강화까지 최신 main 및 Oracle forced dry-run에 반영됐습니다. 주문 감시·정합성 점검·안전 경고의 1분 주기와 live 잠금은 유지됩니다.

## live 전환 전에만 남아 있는 항목

- 실제 Toss 관리 티커 기존 보유·열린 주문·주문가능금액의 live 전환 계획 확정
- 실제 주문 어댑터·회계·migration 리허설과 별도 명시적 live 승인
- 충분한 forced dry-run soak와 운영자 최종 확인

## 바로 다음 작업

1. 다음 미국 시장일에 SOXL을 포함한 일일 분석이 yfinance 일시 장애에도 정상 완료되는지 확인합니다.
2. 한국시간 오전 7시 일일 운용보고가 정상 도착하는지 확인합니다.
3. 운영 로그에서 주문 감시·정합성 점검의 1분 주기가 유지되는지 확인합니다.
4. 별도 live 승인 전까지 live 잠금을 해제하지 않습니다.
