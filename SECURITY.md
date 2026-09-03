# Security Policy

JH_HOLDINGS의 상세 보안·주문 안전 기준은 [`docs/infra/SECURITY.md`](docs/infra/SECURITY.md), 자동운용 계약은 [`docs/JH_AUTO_SPEC.md`](docs/JH_AUTO_SPEC.md)가 소유합니다.

## 핵심 원칙

- API key, Telegram token, SSH private key, 전체 계좌번호, 인증 header, approval raw token을 Git·로그·Markdown·Issue에 기록하지 않습니다.
- `main`은 보호된 branch, PR, Quality/Security gate를 통과한 변경만 받습니다.
- **배포 완료와 JH AUTO 최초 시작은 별개**입니다. `/auto start` 전 실제 신규 BUY는 0건이어야 합니다.
- JDSS 3.2.2의 `$50,000`은 공식 연구·백테스트 기준값이며 실거래 고정한도가 아닙니다.
- 실거래 자금은 대표가 Telegram에서 정한 운용 기준자금·자동운용비율·현재 허용원금과 HWM75 현재 위험예산으로 통제합니다.
- 최초 JH AUTO 시작 시 과거 V3.2.2의 legacy `$50,000` HWM/risk state를 실거래 HWM으로 이어받지 않습니다.
- 정상 JH AUTO에서는 개별 BUY마다 사람이 Telegram 승인하지 않습니다. 기존 review/execution 2단계 검증은 자동실행 계층이 내부적으로 재사용합니다.
- 자동 BUY는 미국 정규장과 최신 가격을 실제 제출 직전 다시 확인합니다.
- 위험축소 SELL은 BUY보다 먼저 처리하고, 미완료·부분체결·`UNKNOWN` 상태에서는 신규 BUY를 막습니다.
- 주문 쓰기 결과가 불명확하면 성공/실패를 추정하거나 자동 재전송하지 않습니다.
- 주문 멱등성, 부분체결 delta, broker receipt 검증, reconciliation, SAFE_MODE를 우회하지 않습니다.
- 최초진입·증액 50→75→100 각 단계는 실제 AUTO 체결 증거와 정합성 검증을 요구합니다.
- 대표 `/halt`는 durable latch를 남기며 시스템이 자동해제하지 않습니다.
- 같은 live 원장에 두 runtime이 동시에 붙지 못하도록 실행잠금을 유지합니다.

## 보안 문제가 의심될 때

1. 실제 주문 위험이 있으면 신규 BUY를 우선 차단합니다.
2. 불명확한 주문은 재주문하지 않고 Toss와 원장 상태를 확인합니다.
3. 계좌·원장 불일치나 취소결과 불명확은 SAFE_MODE에서 다룹니다.
4. 노출 가능성이 있는 자격증명은 폐기·재발급합니다.
5. Gitleaks와 접근통제된 로그로 범위를 확인합니다.

세부 체크리스트와 주문 안전경계는 [`docs/infra/SECURITY.md`](docs/infra/SECURITY.md)를 따릅니다.
