# JDSS 전략 연구 검증 프로토콜

## 목적

전략 연구가 **높은 백테스트 숫자를 만드는 작업**으로 변질되지 않도록 합니다. 모든 연구는 production baseline을 재현하고, 선택 과정과 OOS를 분리하며, 비용·경로·파라미터 주변값에서 개선이 유지되는지 확인해야 합니다.

좋은 후보의 기준은 단순히 CAGR이 높은 전략이 아니라 **설명 가능한 구조 + production parity + 반복 가능한 위험조정 개선**입니다.

## 1. 기본 원칙

1. production 전략은 연구 브랜치에서 직접 수정하지 않습니다.
2. 한 번에 가능한 한 **하나의 구조적 가설**만 바꿉니다.
3. 후보와 baseline은 같은 데이터·비용·체결시점·자금계약을 사용합니다.
4. train/validation에서 후보를 선택한 뒤 OOS를 엽니다.
5. OOS를 보고 고른 후보는 pristine OOS 후보가 아니라 **SHADOW**로만 분류합니다.
6. CAGR만 보지 않고 MDD, Sharpe/Sortino, Calmar, 평균노출, turnover, 거래수, 손실 지속기간과 rolling worst-window를 함께 봅니다.
7. 복잡한 후보가 단순 후보보다 명확히 우월하지 않으면 단순 후보를 선택합니다.
8. 개선이 애매하면 production을 바꾸지 않습니다.

## 2. 연구 시작 전에 고정할 것

연구 PR 또는 결과 manifest에 다음을 먼저 기록합니다.

- 연구 질문 한 문장
- production baseline SHA
- strategy ID / config version
- 데이터 시작·종료일
- 수수료·슬리피지·체결시점
- train / validation / OOS 구간
- 탐색할 후보 수와 파라미터 범위
- 핵심 승격 지표
- random/bootstrap seed가 있으면 seed

결과를 본 뒤 연구 질문이나 승격 기준을 바꾸면 **사후 변경**으로 기록하고 해당 결과의 독립성을 낮게 평가합니다.

## 3. Production parity gate

### 3.1 Baseline reproduction

후보 계산 전에 연구 harness가 현재 production baseline의 핵심 결과를 재현해야 합니다.

baseline 자체가 canonical reference와 다르면 후보 비교를 중단합니다.

### 3.2 실제 production 엔진 재사용

가능한 한 `StrategyBacktestEngine`, `PortfolioBacktestEngine`과 production allocation 함수를 직접 사용합니다.

연구 스크립트가 주문·체결·HWM·onboarding 회계를 별도로 재구현하면 parity 차이를 먼저 증명해야 합니다.

### 3.3 Override 적용 확인

parameter/config override를 사용하면 결과 계산 전에 실제 dataclass/config 객체를 다시 읽어 요청값이 정확히 반영됐는지 assertion합니다.

### 3.4 Binding-change sanity check

후보가 baseline의 특정 신호를 반드시 차단하거나 변경해야 하는데 결과가 완전히 동일하면 연구 harness 오류로 간주합니다.

예: baseline에서 score 59가 통과했고 후보 floor를 60으로 올렸는데 후보 신호·거래가 똑같다면 정상 결과로 인정하지 않습니다.

## 4. 기간 분리와 선택 방화벽

기본 장기 기준:

- train: 2011~2018
- validation: 2019~2022
- OOS: 2023~현재 완료 거래일
- recent stress: 2022~현재 완료 거래일
- full: 2011~현재 완료 거래일

QLD처럼 더 긴 데이터가 필요한 독립 전략은 자산 상장일 이후 2007~ 같은 확장 구간을 추가할 수 있지만, **후보 선택과 OOS 판정 규칙은 미리 고정**합니다.

이미 본 OOS로 파라미터를 조정했다면 다음부터 그 구간은 validation/shadow 자료로 취급합니다. 새 데이터가 쌓이기 전까지 다시 “OOS”라고 부르지 않습니다.

## 5. 복잡도 예산

연구는 다음 순서로 진행합니다.

1. 기존 규칙 제거 또는 단순 치환
2. 신호 하나
3. 구조적 변수 하나 추가
4. 그 뒤에도 필요할 때만 두 번째 변수

조건을 여러 개 붙여 성과를 올리는 방향은 마지막 수단입니다.

후보가 baseline보다 복잡하다면 **복잡도 증가가 어떤 시장현상을 해결하는지 한 문장으로 설명**할 수 있어야 합니다.

같은 성과라면 다음 순서로 선호합니다.

`적은 자산 수 → 적은 신호 수 → 적은 파라미터 → 낮은 turnover → 쉬운 운영`

## 6. 파라미터 주변값 검증

최고 한 점만 선택하지 않습니다.

유망 후보는 선택값 주변의 작은 neighborhood를 다시 검사합니다.

예:

- lookback 10일이 1위라면 5/10/15/20일
- threshold 25%라면 22.5/25/27.5%

판정:

- **넓은 plateau**: 주변값에서도 위험조정 성과가 비슷함 → 신뢰도 증가
- **좁은 sweet spot**: 한 점만 좋고 주변에서 급격히 붕괴 → 과최적화 경고

국소 sweet spot이라고 즉시 기각할 필요는 없지만 SHADOW 판정의 강한 근거로 사용합니다.

## 7. Rolling window

유망 후보는 최소한 rolling 3Y와 5Y를 확인합니다.

보고 항목:

- baseline 대비 CAGR 승률
- MDD 승률
- Sharpe/Calmar 승률
- 4지표 동시우위 비율
- median ΔCAGR
- worst ΔCAGR
- 최악의 drawdown window와 recovery 기간

Full-period CAGR 하나가 높아도 rolling worst-window가 크게 나쁘면 production 대체 근거가 약합니다.

## 8. 경로 강건성: paired block bootstrap

실제 역사 순서에서 MDD가 좋았다는 이유만으로 위험 개선을 확정하지 않습니다.

baseline과 candidate의 동일 월수익률을 **같은 block index로 paired resampling**해 상대우위를 비교합니다.

기본 권장:

- block: 3 / 6 / 12개월
- 충분한 표본 수: 예 3,000~5,000회
- 동일 seed 기록

보고 항목:

- CAGR 우위 확률
- MDD 우위 확률
- Sharpe 우위 확률
- Calmar 우위 확률
- 핵심 지표 동시우위 확률
- median / 5% percentile ΔCAGR
- median MDD improvement

역사적 실제경로에서 네 지표를 모두 이겼더라도 bootstrap 동시우위가 낮다면 **“상위호환”이 아니라 경로 의존성이 있는 후보**로 표현합니다.

## 9. 비용·체결 스트레스

기본 비교는 production 수수료·슬리피지를 사용합니다.

유망 후보는 최소한:

- 낮은 slippage
- 기본 slippage
- 높은 slippage

를 비교합니다.

레버리지·turnover가 높은 후보는 필요 시 0.30~0.50% 같은 극단 비용도 추가합니다.

체결시점 의존성이 의심되면 1거래일·2거래일 지연 또는 다음 세션 가격 변형을 추가해 방향성이 유지되는지 확인합니다.

## 10. 시장 국면과 실패원인

연도별 수익률을 나열하는 데서 끝내지 않습니다.

후보가 baseline보다 나쁜 구간을 최소 하나 이상 해부합니다.

확인 예:

- 2008형 급락
- 2015~2016 회복 지연
- 2018 whipsaw
- 2020 급락·급반등
- 2022 추세 하락
- 2023+ 강한 기술주 상승

“왜 이겼는지”보다 **“어디서 왜 실패하는지”**를 설명할 수 있어야 합니다.

## 11. 다중 탐색 기록

많은 후보를 돌렸다면 최종 1위만 보고하지 않습니다.

연구 결과에 최소한 다음을 남깁니다.

- 총 후보 수
- 후보군의 구조적 차이
- 상위 후보와 하위 후보 범위
- 선택 기준
- OOS를 보기 전에 선택했는지 여부

수십·수백 개 후보 중 최고 하나를 뽑은 경우 그 자체를 과최적화 위험으로 기록합니다.

## 12. 비교 지표

기본 지표:

- Total Return
- CAGR
- MDD
- Sharpe
- Sortino
- Calmar
- 평균노출
- turnover
- 거래일/체결 수
- 최대 손실 지속기간
- recovery 기간

레버리지 전략에서는 **CAGR 증가와 MDD 증가를 반드시 같이** 보고합니다.

단순 benchmark(QQQ, 필요 시 QLD B&H 등)는 전략의 절대적 난이도를 보여주는 참고선으로 사용합니다.

## 13. 결과 판정

### KEEP

- baseline이 더 안정적
- 개선폭이 작음
- 복잡도 대비 효과가 없음
- 특정 구간 또는 비용에서 쉽게 붕괴

→ production 변경 없음.

### SHADOW

- 구조가 설명 가능하고 성과가 유망
- 하지만 OOS 독립성이 훼손됐거나 표본이 부족
- parameter plateau가 좁음
- bootstrap MDD/Sharpe 재현성이 약함
- 최근 국면에서 baseline보다 명확히 약함

→ production 유지, 새 데이터에서 독립 추적.

### ADOPT CANDIDATE

최소한 다음이 필요합니다.

- production parity gate 통과
- train/validation에서 선택 논리가 성립
- OOS에서 방향성 유지
- 비용·실행 지연에 견딤
- neighborhood에서 급격히 붕괴하지 않음
- rolling worst-window가 허용 가능
- 경로 bootstrap에서 핵심 위험조정 우위가 의미 있게 재현
- 복잡도 증가가 있다면 충분한 보상

이 판정도 자동 production 승격이 아닙니다. **사용자 승인 후 별도 구현 PR**에서 정식 반영합니다.

## 14. GitHub와 artifact 규칙

- 일회성 연구 workflow는 연구 브랜치에만 둡니다.
- production에 필요 없는 연구 workflow를 `main`에 병합하지 않습니다.
- 상세 JSON·Markdown·차트는 연구 PR과 Actions artifact에 보관합니다.
- 결과 artifact에는 baseline SHA, data end date, candidate parameters, 비용, seed를 포함합니다.
- 미채택 후보의 대표 결론만 [`../HISTORY.md`](../HISTORY.md)에 짧게 추가합니다.
- 채택 후보만 별도 구현 PR에서 `strategy.yaml`, 공식 사양, 전략 가이드, 한 장 보고서를 제자리 갱신합니다.

## 15. 연구 종료 체크리스트

- [ ] baseline canonical 결과를 재현했는가
- [ ] override가 실제 적용됐음을 assertion했는가
- [ ] 후보 수와 선택과정을 기록했는가
- [ ] train/validation/OOS 경계를 지켰는가
- [ ] 이미 본 OOS를 pristine이라고 부르지 않았는가
- [ ] 비용·실행 스트레스를 확인했는가
- [ ] neighborhood plateau를 확인했는가
- [ ] rolling 3Y/5Y와 worst-window를 봤는가
- [ ] 필요 시 paired block bootstrap을 수행했는가
- [ ] 후보가 실패하는 시장구간을 설명했는가
- [ ] 복잡도가 늘었다면 그 이유와 보상이 충분한가
- [ ] production 문서·코드를 연구 결과만으로 변경하지 않았는가

이 체크리스트의 목적은 후보를 많이 탈락시키는 것입니다. **변경하지 않는 결정도 성공적인 연구 결과**입니다.
