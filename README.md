# JH_HOLDINGS

JH_HOLDINGS는 **투자전략(JDSS)** 과 **자동매매 실행계층(JH AUTO)** 을 분리해서 운영합니다.

- **JDSS 3.2.2**: 시장상태를 판단하고 QQQ·TQQQ·SOXL의 목표비중을 결정합니다.
- **JH AUTO 1.0.0**: 운영자가 정한 자금 범위 안에서 JDSS 목표를 실제 계좌에 안전하게 반영합니다.

전략의 공식 계약은 [`docs/JDSS_FINAL_SPEC.md`](docs/JDSS_FINAL_SPEC.md), 자동운용의 공식 계약은 [`docs/JH_AUTO_SPEC.md`](docs/JH_AUTO_SPEC.md), **현재 실제 배포·시작승인 상태는 오직 [`CURRENT_WORK.md`](CURRENT_WORK.md)** 에서 확인합니다.

## 운영자는 세 문서만 먼저 보면 됩니다

1. [`CURRENT_WORK.md`](CURRENT_WORK.md) — 지금 실제로 무엇이 배포되어 있고 어떤 잠금이 걸려 있는지
2. [`docs/STRATEGY_GUIDE.md`](docs/STRATEGY_GUIDE.md) — JDSS 전략을 쉬운 설명부터 상세 규칙까지 한 문서에서 이해
3. [`docs/TELEGRAM_BOT_GUIDE.md`](docs/TELEGRAM_BOT_GUIDE.md) — `/dashboard`, `/today`, `/auto`, `/halt` 등 실제 운영 방법

정확한 수학·자동주문·보안·배포 규칙은 아래 소유 문서를 따릅니다.

## 문서 소유권과 우선순위

| 확인하려는 사실 | 단일 기준(SSOT) |
|---|---|
| 현재 릴리즈·Oracle 배포·실거래 잠금·다음 운영 행동 | `CURRENT_WORK.md` |
| JDSS 실행 숫자·파라미터 | `strategy.yaml` |
| JDSS 전략 규범·목표비중·백테스트 계약 | `docs/JDSS_FINAL_SPEC.md` |
| JH AUTO 자금·성과·자동실행·시작·정지 계약 | `docs/JH_AUTO_SPEC.md` |
| 실제 프로그램 동작 | `src/jd_holdings/` + 테스트 |
| Telegram 사용법·메시지 표현 기준 | `docs/TELEGRAM_BOT_GUIDE.md` |
| 개발·PR·CI·환경별 역할 | `docs/infra/DEVELOPMENT_WORKFLOW.md` |
| Oracle 배포·실계좌 연결·복구·rollback | `docs/infra/DEPLOYMENT.md` |
| 인증·주문·DB·멱등성·SAFE_MODE 안전 불변식 | `docs/infra/SECURITY.md` |
| 새 전략 연구·승격 규칙 | `docs/research/RESEARCH_PROTOCOL.md` |
| 과거 채택·기각·대표 결정 | `docs/HISTORY.md` + 당시 PR/tag/artifact |
| AI/Codex/IDE가 반드시 지킬 실행 규칙 | `AGENTS.md` |
| 공개 저장소 보안정책 안내 | `SECURITY.md` |

문서·설정·구현·테스트가 서로 다르면 임의로 하나에 맞추지 않고 **불일치 자체를 결함으로 처리**합니다.

## 핵심 운영 원칙

JDSS의 `$50,000`은 **공식 연구·백테스트 비교용 기준값**이며 실거래 고정원금이 아닙니다.

실거래에서는 운영자가 Telegram에서 다음을 정합니다.

```text
운용 기준자금 × 자동운용비율 = 목표 자동원금
```

최초 시작과 증액은 JH AUTO가 `50% → 75% → 100%` 단계로 새 위험을 점진적으로 엽니다. 세부 승격조건과 예외는 `JH_AUTO_SPEC.md`가 소유하며 다른 문서는 숫자를 다시 정의하지 않습니다.

정상 JH AUTO 운영에서 Telegram은 개별 BUY마다 사람이 누르는 승인기가 아니라 **자금설정·상태확인·긴급정지·성과확인용 운영 콘솔**입니다. 배포·재시작은 `/auto start`를 대신하지 않고, 운영자 `/halt`는 시스템이 자동해제하지 않습니다.

## JDSS를 한 문장으로

> QQQ를 중심으로 시장 노출을 조절하고, 반도체 상대강도가 확인될 때만 SOXL을 제한적으로 사용하며, HWM75로 수익 전부를 다시 위험에 걸지 않는 투자전략입니다.

## JH AUTO를 한 문장으로

> 운영자가 정한 자금 범위와 최초 시작승인 안에서 계좌·원장·주문·시장시간·위험예산을 매번 다시 검증하고, 조건이 모두 맞을 때만 JDSS 목표를 자동 실행하는 실거래 계층입니다.

## 실제 JH AUTO 운영 흐름

```text
미국장 완결 데이터
  → JDSS 목표 노출·자산비중 계산
  → JH AUTO 현재 허용원금/HWM75 위험예산 반영
  → 현재 보유·미체결과 목표 비교
  → 위험축소 SELL 우선
  → 주문감시·계좌/원장 대조
  → 미국 정규장 + 최신 가격 + 모든 안전조건 확인
  → 신규 BUY가 필요하면 한 안전주기 최대 1건 자동 실행
  → 주문상태 확인·원장 반영·재대조
  → 이상하면 신규 BUY 차단
```

첫 실거래 시작은 배포와 별개입니다.

```text
배포 완료
→ 운용 기준자금 설정
→ 자동운용비율 설정
→ 운영자가 /auto start 2단계 최초 시작승인
→ 해당 확인 처리에서는 주문 0건
→ 다음 독립 안전주기부터 조건 충족 시 자동매수 가능
```

## 개발 빠른 시작

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -e '.[dev]'
.venv/bin/jdss validate-config
.venv/bin/ruff check .
.venv/bin/pytest
mkdir -p reports
.venv/bin/jdss backtest --symbol ALL --start 2011-01-01 --output reports/baseline.json
```

작업 전에는 [`AGENTS.md`](AGENTS.md)와 [`CURRENT_WORK.md`](CURRENT_WORK.md)를 먼저 읽고, 변경 유형별 완료 기준은 [`docs/infra/DEVELOPMENT_WORKFLOW.md`](docs/infra/DEVELOPMENT_WORKFLOW.md)를 따릅니다.

## 문서 수명주기

- 현재 상태는 `CURRENT_WORK.md`에만 기록합니다.
- 전략 실행 수치는 `strategy.yaml`, 전략 계약은 `JDSS_FINAL_SPEC.md`, 자동운용 계약은 `JH_AUTO_SPEC.md`가 소유합니다.
- 동일 규칙의 숫자·계약을 여러 Markdown이 각각 소유하지 않습니다. 파생 가이드는 소유 문서를 링크합니다.
- 현행 문서는 파일명을 고정하고 제자리 갱신합니다. 날짜·버전이 붙은 복사본을 `main`에 누적하지 않습니다.
- `CURRENT_WORK.md`는 짧은 롤링 상태판, `HISTORY.md`는 과거 결정의 요약 색인입니다.
- 미채택 연구 상세는 연구 PR·Actions artifact·Git 이력에 보존하고 `main`에는 결론만 남깁니다.
- 사용자 동작이 바뀌면 관련 운영 문서와 테스트를 같은 작업에서 함께 갱신합니다.
- 문서-only 변경은 운영 런타임을 불필요하게 재배포하지 않습니다.
