# JH_HOLDINGS

JH_HOLDINGS는 **투자전략과 자동매매 실행계층을 분리**해서 운영합니다.

- **투자전략 `JDSS 3.2.2`**: 시장상태를 판단하고 QQQ·TQQQ·SOXL의 목표비중을 결정합니다.
- **자동매매 `JH AUTO 1.0.0`**: 대표가 정한 자금 범위에서 JDSS 목표를 실제 계좌에 안전하게 반영합니다.

자동매매 상세는 [`docs/JH_AUTO_SPEC.md`](docs/JH_AUTO_SPEC.md), 전략 상세는 [`docs/JDSS_FINAL_SPEC.md`](docs/JDSS_FINAL_SPEC.md)를 따릅니다. 현재 실제 배포·시작승인 상태는 오직 [`CURRENT_WORK.md`](CURRENT_WORK.md)에서 확인합니다.

## 핵심 운영 원칙

JDSS 3.2.2의 `$50,000`은 **공식 연구·백테스트 비교를 위한 기준값**입니다. 실제 실거래 총 투자금액이 아닙니다.

실거래에서는 대표가 Telegram에서 다음 두 값을 정합니다.

```text
운용 기준자금 × 자동운용비율 = 목표 자동원금
```

예를 들어 운용 기준자금 `$50,000`, 자동운용비율 `20%`라면 목표 자동원금은 `$10,000`입니다. 최초 시작·증액 시에는 위험을 한 번에 열지 않고 JH AUTO의 50→75→100 단계에 따라 **현재 허용원금**을 점진적으로 확대합니다.

운용 기준자금과 자동운용비율은 최초 시작 이후에도 변경할 수 있습니다. 증액은 추가분만 단계적으로 열고, 감액은 위험축소를 우선합니다. 외부 자금 증감은 투자수익으로 계산하지 않습니다.

## JDSS를 한 문장으로

> **QQQ를 중심으로 0.5x~1.5x 시장 노출을 조절하고, 반도체가 상대적으로 강할 때만 SOXL을 제한적으로 사용하며, HWM75로 수익 전부를 다시 위험에 걸지 않는 투자전략입니다.**

## JH AUTO를 한 문장으로

> **대표가 한 번 정한 자금 범위와 최초 시작승인 안에서, 계좌·원장·주문·시장시간·위험예산을 매번 다시 검증하고 조건이 모두 맞을 때만 JDSS 목표를 자동으로 실행하는 실거래 계층입니다.**

정상 JH AUTO 운영에서 Telegram은 개별 BUY마다 사람이 승인하는 화면이 아니라 **자금설정·상태확인·긴급정지·성과확인용 운영 콘솔**입니다. 내부적으로는 기존 review → execution 2단계 검증코드를 재사용하지만 승인 주체는 JH AUTO입니다.

## 무엇을 먼저 보면 되나요?

| 목적 | 먼저 읽을 문서 | 무엇을 알 수 있나 |
|---|---|---|
| 지금 실제로 무엇이 돌아가는지 확인 | [`CURRENT_WORK.md`](CURRENT_WORK.md) | 현재 릴리즈, Oracle 배포, 실거래 잠금, 최초 시작 상태 |
| 전략을 빠르게 이해 | [`docs/ONE_PAGE_REPORT.md`](docs/ONE_PAGE_REPORT.md) | 자산 역할, 시장노출, HWM75, 기준 과거검증 |
| 전략을 자세히 이해 | [`docs/STRATEGY_GUIDE.md`](docs/STRATEGY_GUIDE.md) | JDSS 3.2.2의 노출·RS6M·HWM75·목표비중 |
| 투자전략 공식 계약 확인 | [`docs/JDSS_FINAL_SPEC.md`](docs/JDSS_FINAL_SPEC.md) | 전략 수학·목표비중·백테스트 규범 |
| 자동매매 공식 계약 확인 | [`docs/JH_AUTO_SPEC.md`](docs/JH_AUTO_SPEC.md) | 자금·성과·자동주문·시작·정지·복구 계약 |
| Telegram에서 실제로 조작 | [`docs/TELEGRAM_BOT_GUIDE.md`](docs/TELEGRAM_BOT_GUIDE.md) | `/dashboard` `/today` `/auto` `/halt` 등 운영법 |
| 개발·PR·배포 절차 확인 | [`docs/infra/DEVELOPMENT_WORKFLOW.md`](docs/infra/DEVELOPMENT_WORKFLOW.md) | 환경별 역할, 브랜치·PR·CI·인수인계 |
| Oracle 배포·복구 확인 | [`docs/infra/DEPLOYMENT.md`](docs/infra/DEPLOYMENT.md) | 배포·복구·fail-closed 절차 |
| 보안·주문 안전경계 확인 | [`docs/infra/SECURITY.md`](docs/infra/SECURITY.md) | Telegram, Toss, DB, 멱등성, SAFE_MODE, 실거래 잠금 |
| 새 전략을 연구 | [`docs/research/RESEARCH_PROTOCOL.md`](docs/research/RESEARCH_PROTOCOL.md) | 독립검증, 비용, 재표본검증, 채택 기준 |
| 과거 결정 확인 | [`docs/HISTORY.md`](docs/HISTORY.md) | 대표 릴리즈와 채택·기각·운영 결정 |

문서별 소유권과 서로 내용이 다를 때의 판정 규칙은 [`docs/README.md`](docs/README.md)가 기준입니다.

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
→ 대표가 /auto start 2단계 최초 시작승인
→ 해당 확인 처리에서는 주문 0건
→ 다음 독립 안전주기부터 조건 충족 시 자동매수 가능
```

`/halt`는 대표 긴급정지이며 시스템이 자동으로 해제하지 않습니다. `/resume`은 대표 긴급정지를 해제하기 위한 별도 안전확인입니다. `UNKNOWN` 주문이나 계좌·원장 불일치가 있으면 신규 BUY를 진행하지 않습니다.

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

작업을 시작하기 전에는 루트 [`AGENTS.md`](AGENTS.md)와 [`CURRENT_WORK.md`](CURRENT_WORK.md)를 먼저 읽습니다. 전략 변경, 주문 변경, 문서-only 변경에 필요한 검증은 각각 다르므로 [`docs/infra/DEVELOPMENT_WORKFLOW.md`](docs/infra/DEVELOPMENT_WORKFLOW.md)의 완료 기준을 따릅니다.

## 문서 원칙

- 현재 상태는 `CURRENT_WORK.md`에만 기록합니다.
- 전략 실행 수치는 `strategy.yaml`, 투자전략 계약은 `JDSS_FINAL_SPEC.md`, 자동매매 계약은 `JH_AUTO_SPEC.md`가 소유합니다.
- `$50,000` 연구 기준값과 실거래 운용 기준자금을 같은 의미로 쓰지 않습니다.
- 버전·날짜가 붙은 현행 문서 복사본을 만들지 않습니다.
- 미채택 연구의 상세 결과는 연구 PR·Actions artifact에 보존하고 `main`에는 결론만 남깁니다.
- 코드의 사용자 동작이 바뀌면 관련 운영 문서도 같은 작업에서 함께 갱신합니다.
