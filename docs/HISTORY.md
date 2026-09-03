# JDSS 대표 버전과 연구 역사

이 문서는 과거 버전·운영결정·대표 연구결론을 찾기 위한 **append-only 역사 색인**입니다. 현재 전략을 정의하지 않습니다.

현재 계약은 [`JDSS_FINAL_SPEC.md`](JDSS_FINAL_SPEC.md)와 [`../strategy.yaml`](../strategy.yaml), 현재 배포·live 상태는 [`../CURRENT_WORK.md`](../CURRENT_WORK.md)가 기준입니다. 상세 과거 원본은 Git tag, 병합 PR, 연구 PR과 Actions artifact에서 복구합니다.

## 1. 대표 릴리즈

| 버전 | 핵심 변화 | 보존 위치 |
|---|---|---|
| v1.1.2 | 초기 점수·분할매수 계약의 회귀 기준 | [`configs/strategy_v1.1.2.yaml`](../configs/strategy_v1.1.2.yaml) |
| v2.2.2 | SGOV 현금화 재개와 BUY 2단계 승인까지 완성한 v2 대표판 | Git tag `v2.2.2` |
| v3.0.0 | 월간 코어 + 5% 부스터를 도입한 v3 기준선 | Git tag `v3.0.0` |
| v3.1.1 | $50,000 고정원금·SGOV OFF를 도입한 전환판 | Git history·병합 PR |
| v3.2.2 | QQQ 동적노출·RS6M·HWM75·5% virtual 추가 레버리지 도입 | Git tag `v3.2.2` |

태그는 당시 코드·설정·문서를 함께 보존하므로 현재 `main`에 버전별 Markdown 복사본을 두지 않습니다.

## 2. V3.2.2 채택 결정

V3.1.1은 최대낙폭이 비교적 낮았지만 평균 시장노출이 낮아 장기 자금 활용과 수익이 제한적이었습니다. 후속 연구는 단순 고정 레버리지 대신 QQQ 추세에 따라 0.5/1.0/1.25/1.5x를 오가고, 반도체 상대강도가 있을 때만 SOXL을 섞는 구조를 검증했습니다.

V3.2.2를 채택한 핵심 이유:

- QQQ 대비 장기 CAGR과 위험조정 성과 개선이 확인됨
- SOXL sleeve·RS 기간·대체 proxy·월 reset 날짜의 주변값에서도 결과가 즉시 붕괴하지 않음
- 월중 고변동 감속과 SOXL→TQQQ one-way exit로 위험증가보다 위험축소를 쉽게 설계
- HWM75로 최고점 이익의 25%를 추가 위험 확대에서 제외
- BUY는 사람 승인, 위험축소 SELL은 자동이라는 운영 경계를 유지

당시 채택 시점의 정확한 수치는 당시 PR·artifact에 보존합니다. 데이터 공급자의 adjusted history가 이후 소폭 수정될 수 있으므로 **현재 재현 수치는 [`STRATEGY_GUIDE.md`](STRATEGY_GUIDE.md)**가 소유합니다.

## 3. 대표 운영 결정

| 결정 | 이유·결과 |
|---|---|
| QQQ/TQQQ/SOXL을 하나의 allocation 원장과 HWM75 위험예산으로 관리 | 종목별 독립 전략보다 전체 위험과 현금을 한 경계에서 관리 |
| 위험축소 SELL 자동 / 위험증가 BUY 승인 | 위험 감소는 지연하지 않고 위험 증가는 사람이 확인 |
| forced dry-run 모의원장과 실제 Toss read-only 조회 분리 | 모의 보유를 실제 계좌 보유로 오인하지 않기 위함 |
| release-local venv + DB snapshot + atomic switch + 자동 rollback | 배포 중단점과 DB 복구를 명확히 함 |
| pinned SSH host trust | 새 runner가 임의 host key를 즉석 신뢰하지 않게 함 |
| 현재판 문서를 제자리 갱신 | 버전별 Markdown 복제와 불일치 방지 |

### 2026-08-25 — 오늘 주문 일괄 검토·순차 실행

PR #197에서 Telegram BUY 운영 UX를 종목별 반복승인 중심에서 **`오늘 주문 한번에 검토` → `N건 순차 실행`** 중심으로 정리했습니다.

전략 산식은 변경하지 않았으며 다음 운영리스크를 추가로 방어했습니다.

- stale 전략계산과 진짜 주문없음 구분
- 다음 거래일 / target_qty 준비 상태 구분
- SELL-first와 최종 실행 직전 reconciliation
- 전체 BUY 합계 HWM75 사전·최종 재검사
- 중복 batch·동시 callback 직렬화
- 검토 중 예외 approval cleanup
- 순차 제출 중 fail-closed 중단
- onboarding 단계대기 오인 방지
- batch lifecycle 감사로그

기능은 forced dry-run으로 배포했고 **live hard lock은 유지**했습니다. 상세 현재 계약은 공식 사양·Telegram 가이드·보안 기준으로 이동했으며 이 역사 문서에는 결정만 남깁니다.

## 4. 대표 미채택·SHADOW 연구

### 반월·격주 밴드형 구조

PR #27의 `SEMIMONTHLY_BAND_H05` 등은 일부 구간 수익이 좋았지만 시작 위상에 민감했고 5년 순환구간·paired bootstrap·MDD 승격 기준에서 안정성이 부족했습니다. 복잡도를 늘리는 대신 단순 월간 기준을 유지했습니다.

### V3.3 리밸런싱·재가속 연구

일/주/격주/월 리밸런싱과 제한적 mid-month re-risk를 비교했지만 **월간 reset이 비용·MDD·안정성의 균형에서 계속 강했습니다.** 일부 mid-month 후보는 CAGR을 소폭 높였으나 특정 구간, 특히 whipsaw 환경에서 약해 production 승격 대신 연구 후보로만 남겼습니다.

핵심 교훈은 “더 자주 판단하면 더 빨리 수익을 회복할 것”이라는 직관이 항상 맞지 않으며, **빠른 re-risk가 오히려 whipsaw drawdown을 키울 수 있다**는 점입니다.

### QLD/SSO 및 QLD-only 연구 — PR #193

Production V3.2.2를 건드리지 않고 QLD·SSO와 단순 QLD-only 전략을 단계적으로 검증했습니다.

주요 결론:

- QLD↔SSO 상시 회전은 시스템적 약세장에서 MDD가 과도해 기각
- SSO는 핵심 alpha 자산으로 보기 어려움
- V3.2.2의 TQQQ/SOXL을 단순 QLD로 치환한 구조도 production 대체에 실패
- V3.2.2 + QLD blend는 역사 경로에서 강했지만 paired block bootstrap에서 MDD 우위 재현성이 낮아 SHADOW만 유지
- 가장 단순한 QLD-only 후보는 **`QLD_VOL10_25_CASH`**

`QLD_VOL10_25_CASH`는 QQQ 10일 연환산 변동성이 25% 미만이면 QLD 100%, 25% 이상이면 현금 100%, 위험회피 후 재진입은 월 reset에서만 허용하는 단순 구조입니다.

역사적 실제 경로에서는 높은 CAGR·Calmar와 약 -30% 수준 MDD를 보였지만:

- 10일/25% 주변 parameter plateau가 넓지 않음
- 최근 2023+ 구간에서 V3.2.2보다 수익이 낮음
- paired monthly block bootstrap에서 MDD·Sharpe 동시우위 재현성이 낮음

따라서 **production 대체가 아니라 독립 SHADOW 후보**로 보관했습니다. 상세 Phase 1~13 수치와 스크립트는 Draft 연구 PR #193과 Actions artifact에 남깁니다.

## 5. 연구에서 반복 확인한 경고

- 2023+ 데이터는 여러 후보 연구에서 반복 관찰되어 pristine OOS가 아님
- 단일 최고 파라미터보다 주변값의 plateau와 worst-window가 중요
- 실제 역사경로의 낮은 MDD가 경로 재배열에서도 재현되는지 별도 확인 필요
- 높은 CAGR만으로 production 대체를 결정하지 않음
- 복잡한 조건 추가는 수익 향상 자체가 아니라 **새로운 구조적 가설을 검증할 때만** 허용

새 연구는 [`research/RESEARCH_PROTOCOL.md`](research/RESEARCH_PROTOCOL.md)를 따릅니다. 미채택 상세 결과는 `main`에 별도 보고서로 복사하지 않고 연구 PR·artifact에 보존합니다.

## 2026-09-03 — JH AUTO 1.0.0 자동운용 계층 채택

- JDSS 3.2.2 전략 수학은 유지하고 자동매매 실행계층 버전을 `JH AUTO 1.0.0`으로 분리했습니다.
- 운용 기준자금과 자동운용비율을 Telegram에서 운영자가 정하며, 위험증가는 추가 원금 기준 50→75→100 단계로 엽니다.
- 전체 성과는 자금 유입·회수 영향을 분리하는 단위가치 방식으로 계산합니다.
- 최초 시작승인 전 실제 BUY 0건, 시작 callback 자체 주문 0건, 미국 정규장·한 안전주기 최대 한 BUY를 초기 안전계약으로 채택했습니다.
- 운영자 `/halt`는 자동해제 불가 durable latch, 시스템 재시작은 별도 임시격리로 구분합니다.
- 실제 배포·최초 시작 여부는 이 역사 문서가 아니라 `CURRENT_WORK.md`의 현재 상태를 따릅니다.
