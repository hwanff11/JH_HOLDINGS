# JH_HOLDINGS

JH_HOLDINGS는 **QQQ를 기본 자산으로 두고 시장 상태에 따라 TQQQ·SOXL 비중을 조절하는 JDSS 반자동 운용 시스템**입니다. 전략 판단과 위험축소 SELL은 시스템이 처리하고, 위험을 늘리는 BUY는 Telegram에서 운영자가 검토한 뒤 최종 승인합니다.

현재 릴리즈·배포 SHA·Oracle 동기화 여부·live 잠금 상태처럼 자주 바뀌는 정보는 [`CURRENT_WORK.md`](CURRENT_WORK.md)에서만 확인합니다. 이 README는 프로젝트의 **입구와 문서 지도**만 담당합니다.

## 무엇을 먼저 보면 되나요?

| 목적 | 먼저 읽을 문서 | 무엇을 알 수 있나 |
|---|---|---|
| 지금 실제로 무엇이 돌아가는지 확인 | [`CURRENT_WORK.md`](CURRENT_WORK.md) | 현재 릴리즈, 배포 상태, live 잠금, 다음 작업 |
| 전략을 빠르게 이해 | [`docs/ONE_PAGE_REPORT.md`](docs/ONE_PAGE_REPORT.md) | 한 문장 요약, 자산 역할, 핵심 규칙, 장점·한계 |
| 전략을 자세히 이해 | [`docs/STRATEGY_GUIDE.md`](docs/STRATEGY_GUIDE.md) | 노출·RS6M·HWM75·거래 흐름·기준 백테스트 |
| 구현이 반드시 따라야 할 계약 확인 | [`docs/JDSS_FINAL_SPEC.md`](docs/JDSS_FINAL_SPEC.md) | 전략·자금·주문·최초진입·백테스트의 규범 계약 |
| Telegram에서 실제로 조작 | [`docs/TELEGRAM_BOT_GUIDE.md`](docs/TELEGRAM_BOT_GUIDE.md) | 오늘 주문 검토, 순차 실행, 상태·오류 대응 |
| 개발·PR·배포 절차 확인 | [`docs/infra/DEVELOPMENT_WORKFLOW.md`](docs/infra/DEVELOPMENT_WORKFLOW.md) | 환경별 역할, 브랜치·PR·CI·인수인계 |
| Oracle 배포·롤백 확인 | [`docs/infra/DEPLOYMENT.md`](docs/infra/DEPLOYMENT.md) | forced dry-run 배포, smoke, rollback |
| 보안·주문 안전경계 확인 | [`docs/infra/SECURITY.md`](docs/infra/SECURITY.md) | Telegram 승인, Toss, DB, SAFE_MODE, live 잠금 |
| 새 전략을 연구 | [`docs/research/RESEARCH_PROTOCOL.md`](docs/research/RESEARCH_PROTOCOL.md) | baseline parity, OOS, 비용, bootstrap, 채택 기준 |
| 과거 결정 확인 | [`docs/HISTORY.md`](docs/HISTORY.md) | 대표 릴리즈와 채택·기각·SHADOW 결정 |

문서별 소유권과 서로 내용이 다를 때의 판정 규칙은 [`docs/README.md`](docs/README.md)가 기준입니다.

## JDSS를 한 문장으로

> **QQQ를 중심으로 0.5x~1.5x 시장 노출을 조절하고, 반도체가 상대적으로 강할 때만 SOXL을 제한적으로 사용하며, 위험은 자동으로 줄이고 BUY만 사람이 최종 승인하는 시스템입니다.**

현재 production 계약은 `strategy.yaml`과 [`docs/JDSS_FINAL_SPEC.md`](docs/JDSS_FINAL_SPEC.md)가 정의합니다. 연구 브랜치의 후보 전략은 사용자가 별도로 채택하기 전까지 production이 아닙니다.

## 실제 운영 흐름

```text
미국장 완결 데이터
  → 목표 노출·자산비중 계산
  → 현재 보유와 목표 비교
  → 줄여야 할 수량은 SELL 우선 처리
  → 정합성 확인
  → 늘려야 할 수량은 Telegram '오늘 주문 한번에 검토'
  → 운영자 최종 승인
  → 종목별 순차 주문·체결 감시
  → 원장 대조 / 이상 시 SAFE_MODE
```

현재 운용이 forced dry-run이면 위 주문 흐름도 **모의 주문**에만 적용됩니다. 실제 Toss 계좌 조회와 실제 주문 활성화는 서로 다른 경계이며, 배포 성공만으로 live가 켜지지 않습니다.

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
- 실행 수치는 `strategy.yaml`, 규범 계약은 `JDSS_FINAL_SPEC.md`가 소유합니다.
- 쉬운 설명과 공식 계약을 같은 문서에 중복 작성하지 않습니다.
- 버전·날짜가 붙은 현행 문서 복사본을 만들지 않습니다.
- 미채택 연구의 상세 결과는 연구 PR·Actions artifact에 보존하고 `main`에는 결론만 남깁니다.
- 코드의 사용자 동작이 바뀌면 관련 운영 문서도 같은 작업에서 함께 갱신합니다.
