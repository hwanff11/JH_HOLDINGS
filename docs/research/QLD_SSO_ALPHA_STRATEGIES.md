# QLD/SSO Alpha Phase 1 Strategies

Phase 1은 파라미터 튜닝보다 구조 비교를 우선한다. 모든 회전 신호는 QQQ와 SPY 종가로 계산하고 다음 거래일부터 반영한다.

- `QLD_BUY_HOLD`: QLD 100%
- `SSO_BUY_HOLD`: SSO 100%
- `MIX_50_50`: QLD/SSO 50/50 고정
- `RS63_MONTHLY`: 월말 QQQ와 SPY 63일 수익률 중 강한 지수의 2배 ETF 선택
- `RS126_MONTHLY`: 월말 QQQ와 SPY 126일 수익률 중 강한 지수의 2배 ETF 선택
- `RS126_WEEKLY`: 주말 기준 126일 상대강도 승자 선택
- `RS_DUAL_MONTHLY`: QQQ가 63일과 126일 상대강도에서 모두 SPY를 이길 때만 QLD, 아니면 SSO
- `RISK_ADJ126_MONTHLY`: 126일 수익률을 20일 연환산 변동성으로 나눈 위험조정 점수가 높은 지수 선택
- `QLD_TREND_RS_MONTHLY`: QQQ가 SMA200 위이고 126일 상대강도도 SPY보다 강할 때만 QLD, 아니면 SSO
- `DUAL_TREND_MONTHLY`: QQQ/SPY 장기추세를 먼저 확인하고 둘 다 강하면 126일 상대강도 승자, 둘 다 약하면 20일 변동성이 낮은 쪽의 2배 ETF 선택
