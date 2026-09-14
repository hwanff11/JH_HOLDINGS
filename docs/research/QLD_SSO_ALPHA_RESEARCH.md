# JDSS QLD/SSO Alpha Research

## 목표

QQQ를 단순 보유하는 것보다 장기 CAGR을 높이되, QLD Buy & Hold보다 최대낙폭(MDD)을 낮추는 QLD/SSO 회전 전략을 연구한다.

## 연구 원칙

- 기초신호는 QQQ와 SPY로 계산한다.
- 신호 계산 당일에는 거래하지 않고 다음 거래일부터 반영한다.
- QLD와 SSO는 모두 일일 2배 레버리지 상품이므로 지수 선택 효과를 비교하기 쉽다.
- 1차 연구는 단순하고 설명 가능한 규칙만 비교하며 복잡한 파라미터 최적화는 하지 않는다.
- Train/Validation에서 후보를 사전 순위화하고 OOS는 마지막 확인에 사용한다.
- 기본 슬리피지 0.10%, 스트레스 0.05%/0.20%를 함께 확인한다.
- Production JDSS V3.2.2에는 영향을 주지 않는 독립 연구다.

## Phase 1 비교군

1. QLD Buy & Hold
2. SSO Buy & Hold
3. QLD/SSO 50/50
4. 63일 상대강도 월간 회전
5. 126일 상대강도 월간 회전
6. 126일 상대강도 주간 회전
7. 63일+126일 상대강도 합의형 월간 회전
8. 126일 수익률/20일 변동성 위험조정 회전
9. QQQ SMA200 + 126일 상대강도 조건형
10. QQQ/SPY 이중 추세 + 126일 상대강도 회전

## 검증구간

- Train: 2007~2014
- Validation: 2015~2020
- OOS: 2021~현재
- Recent stress: 2022~현재
- Full: 2007~현재

## 1차 성공 조건

- Full CAGR > QQQ Buy & Hold
- Full MDD 절대값 < QLD Buy & Hold MDD 절대값
- OOS에서 QQQ 대비 수익 우위가 유지되는지 확인
- 3년/5년 rolling 구간에서 우위 빈도와 최악 상대성과를 별도로 확인
