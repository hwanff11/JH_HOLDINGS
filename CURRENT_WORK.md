# JH_HOLDINGS Current Work

> **현재 상태만 보는 롤링 상태판**입니다. 전략 설명은 [`docs/STRATEGY_GUIDE.md`](docs/STRATEGY_GUIDE.md), 규범 계약은 [`docs/JDSS_FINAL_SPEC.md`](docs/JDSS_FINAL_SPEC.md), 운영 방법은 [`docs/TELEGRAM_BOT_GUIDE.md`](docs/TELEGRAM_BOT_GUIDE.md)를 따릅니다. 완료된 상세 작업은 이 파일에 누적하지 않습니다.

## 1. Production 상태

- 공식 릴리즈: **v3.2.2**
- 전략 ID: **`JDSS-3.2.2-RS6M-ONEWAY-HWM75`**
- config/package: **3.2.2**
- GitHub `main`: 보호 브랜치, PR + 필수 CI 경유
- Oracle runtime 기능 revision: **`d00668456a644d5701d24cb35e88463ed6df85d9`**
- Oracle 서비스: **active**
- 운용 모드: **forced dry-run**
- `portfolio.live_enabled`: **false**
- `JDSS_LIVE_CONFIRMATION`: **empty**
- 실제 Toss 주문: **LOCKED OFF**
- SGOV 자동운용: **OFF**

`main`에 runtime 영향이 없는 문서-only commit이 추가될 수 있으므로 GitHub HEAD와 Oracle 기능 revision이 항상 같은 문자열일 필요는 없습니다. **runtime 코드·설정이 달라졌는데 Oracle revision이 뒤처진 경우만 배포 불일치**로 봅니다.

## 2. 최근 완료

### Telegram 아침 운용 브리핑 가독성 개선

매일 아침 V3.2.2 내부 배분 이벤트를 그대로 여러 줄 발송하던 방식을 운영자가 바로 행동을 판단할 수 있는 **단일 브리핑**으로 통합해 production에 배포했습니다.

기본 순서:

```text
오늘의 결론
  → 현재 목표 비중
  → 전략 판단
  → 쉬운 설명
  → 자금관리
```

주요 변경:

- `매수 승인 필요 / 위험축소 진행 / 즉시 주문 없음`을 최상단에서 구분
- QQQ / TQQQ / SOXL 목표비중을 종목별로 읽기 쉽게 표시
- `RS6M`, `overlay`, `위험예산` 같은 내부 용어 대신 `반도체 상대강도`, `추가매수 판단`, `오늘 매수규모 계산 기준` 등 사용자 표현 우선
- HWM75는 수익분의 75%만 다음 위험예산 증가에 반영하는 의미를 함께 설명
- 전략 계산·주문 규칙·HWM75 수학·live 잠금은 변경하지 않음
- 전용 포맷 테스트와 Telegram 운영 가이드 동기화 완료

관련 변경은 PR #201로 `main`에 병합했고, Oracle forced dry-run에 **`d00668456a644d5701d24cb35e88463ed6df85d9`** revision을 배포했습니다.

### Telegram 오늘 주문 일괄 검토·순차 실행

운영자가 종목별 BUY 카드를 반복해서 처리하지 않고 다음 흐름을 기본으로 사용하도록 배포했습니다.

```text
/dashboard
  → 오늘 주문 한번에 검토
  → 최신 계산·세션·SELL·미체결·정합성·HWM75 한도 사전검사
  → QQQ/TQQQ/SOXL BUY를 한 화면에서 확인
  → N건 순차 실행 최종 승인
  → 종목별 독립 제출·체결 감시
```

핵심 안전장치인 **SELL 우선, 최종 실행 직전 reconciliation, 합계 HWM75 재검사, 중복 batch 차단, 가격·수량 변경 시 재승인, 중간 실패 시 이후 BUY fail-closed 중단**이 반영되어 있습니다. 세부 계약은 공식 사양·Telegram 가이드·보안 기준이 소유합니다.

## 3. 기준 검증 상태

현재 production V3.2.2는 다음 공통 게이트를 유지합니다.

- Quality Gate ✅
- Security Gate ✅
- JDSS V3 canonical Backtest ✅
- forced dry-run Oracle 배포·smoke ✅
- pinned SSH trust·DB snapshot·rollback-safe release ✅
- Toss read-only smoke ✅
- live hard lock ✅

아침 브리핑 변경 PR의 최종 CI에서 Quality Gate, Security, JDSS V3 Backtest가 모두 성공했고, 배포 전 focused deployment gate 28건과 config validation도 통과했습니다.

최신 canonical 백테스트의 사람이 읽는 승인 기준은 [`docs/STRATEGY_GUIDE.md`](docs/STRATEGY_GUIDE.md)에 기록하고, 실행별 artifact와 로그는 GitHub Actions에 둡니다.

## 4. 연구 상태

Production은 **V3.2.2를 유지**합니다.

QLD/SSO 연구는 production과 분리된 Draft 연구 PR에서 관리합니다. 현재 가장 단순한 SHADOW 후보는 **`QLD_VOL10_25_CASH`**이며, 역사적 CAGR·Calmar는 유망하지만 parameter neighborhood가 넓지 않고 최근 구간에서 V3.2.2보다 약했으며 paired block bootstrap에서 MDD 동시우위 재현성이 낮아 **production 대체 후보로 승격하지 않았습니다.**

연구 후보의 상세 수치와 일회성 스크립트는 research PR·Actions artifact에 두고 production 문서에는 복제하지 않습니다. 대표 결론만 [`docs/HISTORY.md`](docs/HISTORY.md)에 남깁니다.

## 5. 운영상 유지해야 할 원칙

- 위험축소 SELL은 자동, 위험증가 BUY는 운영자 승인 방식 유지
- `N건 순차 실행`은 원자적 basket 주문이 아니므로 일부 종목만 제출될 수 있음
- 이미 제출된 앞 주문을 임의로 되팔아 자동 rollback하지 않음
- JDSS가 관리하는 QQQ/TQQQ/SOXL을 같은 Toss 계좌에서 동시에 수동 거래하지 않음
- dry-run 원장과 실제 Toss read-only 계좌를 같은 보유수량으로 해석하지 않음
- 장애·UNKNOWN·원장 불일치·미완료 위험축소는 신규 BUY보다 SAFE_MODE가 우선
- Oracle 배포 승인과 live 활성화 승인을 같은 것으로 해석하지 않음

## 6. 다음 우선순위

1. 다음 정상 일일 분석 시점에 새 아침 운용 브리핑이 실제 Telegram에서 의도한 순서·문구로 발송되는지 운영 관찰
2. 현재 forced dry-run 환경에서 `주문 없음 / 다음 거래일 대기 / SELL 진행 / 다건 BUY / 부분실패` 화면을 실제 운영 루틴으로 충분히 관찰
3. 실제 Toss 계좌 적용을 검토할 때만 별도 preflight·migration·주문 어댑터 리허설 수행
4. QLD SHADOW 후보는 새 데이터가 쌓이는 동안 production과 분리해 관찰하고 추가 조건을 쉽게 붙이지 않음
5. live 전환은 별도 명시적 승인 전까지 진행하지 않음
