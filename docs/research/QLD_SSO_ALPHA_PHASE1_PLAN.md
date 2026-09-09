# QLD/SSO Alpha Phase 1 Validation Plan

## Objective

Maximize long-run CAGR relative to QQQ while keeping maximum drawdown below QLD Buy & Hold.

## Anti-overfitting controls

- Train 2007-2014
- Validation 2015-2020
- OOS 2021-present
- Recent stress 2022-present
- Candidate ordering uses Train + Validation only.
- OOS is reported after candidate ranking.
- Cost stress: 0.05%, 0.10%, 0.20% per traded notional.
- 3-year and 5-year rolling QQQ beat rates are reported.

## Phase 1 decision

Phase 1 does not modify production code. A candidate advances only if it improves on QQQ CAGR, stays below QLD Buy & Hold MDD, remains competitive in OOS, and does not rely on a narrow single-period result.
