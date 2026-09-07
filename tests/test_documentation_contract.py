from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")

CURRENT_MARKDOWN = {
    "README.md",
    "AGENTS.md",
    "CURRENT_WORK.md",
    "SECURITY.md",
    "docs/HISTORY.md",
    "docs/JDSS_FINAL_SPEC.md",
    "docs/JH_AUTO_SPEC.md",
    "docs/STRATEGY_GUIDE.md",
    "docs/TELEGRAM_BOT_GUIDE.md",
    "docs/infra/DEPLOYMENT.md",
    "docs/infra/DEVELOPMENT_WORKFLOW.md",
    "docs/infra/SECURITY.md",
    "docs/research/RESEARCH_PROTOCOL.md",
}

RETIRED_MARKDOWN = {
    "docs/README.md",
    "docs/ONE_PAGE_REPORT.md",
    "docs/TELEGRAM_LIVE_MESSAGE_STANDARD.md",
    "docs/JH_AUTO_PRELIVE_HARDENING.md",
    "docs/infra/LIVE_COMMISSIONING.md",
    "docs/research/STRATEGY_FREEZE.md",
}


def _managed_markdown() -> set[str]:
    documents = [*ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md")]
    return {str(document.relative_to(ROOT)) for document in documents}


def test_markdown_relative_links_resolve():
    missing: list[str] = []
    for document in ROOT.rglob("*.md"):
        for target in MARKDOWN_LINK.findall(document.read_text(encoding="utf-8")):
            raw = target.split("#", 1)[0].strip()
            if not raw or raw.startswith(("http://", "https://", "mailto:")):
                continue
            resolved = (document.parent / raw).resolve()
            if not resolved.exists():
                missing.append(f"{document.relative_to(ROOT)} -> {target}")
    assert missing == []


def test_current_markdown_set_is_consolidated_to_13_files():
    assert _managed_markdown() == CURRENT_MARKDOWN
    assert len(CURRENT_MARKDOWN) == 13
    for retired in RETIRED_MARKDOWN:
        assert not (ROOT / retired).exists()


def test_document_roles_and_change_impact_are_explicit():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    for required in (
        "문서 소유권과 우선순위",
        "CURRENT_WORK.md",
        "strategy.yaml",
        "JDSS_FINAL_SPEC.md",
        "JH_AUTO_SPEC.md",
        "TELEGRAM_BOT_GUIDE.md",
        "DEVELOPMENT_WORKFLOW.md",
        "DEPLOYMENT.md",
        "SECURITY.md",
        "RESEARCH_PROTOCOL.md",
        "HISTORY.md",
    ):
        assert required in readme

    for required in (
        "변경 영향별 필수 동기화",
        "strategy.yaml",
        "JDSS_FINAL_SPEC.md",
        "JH_AUTO_SPEC.md",
        "TELEGRAM_BOT_GUIDE.md",
        "DEPLOYMENT.md",
        "SECURITY.md",
        "CURRENT_WORK.md",
    ):
        assert required in agents


def test_document_lifecycle_uses_fixed_current_files_and_git_history():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    history = (ROOT / "docs/HISTORY.md").read_text(encoding="utf-8")
    workflow = (ROOT / "docs/infra/DEVELOPMENT_WORKFLOW.md").read_text(encoding="utf-8")

    assert "롤링 상태판" in agents
    assert "제자리 갱신" in readme
    assert "append-only 역사 색인" in history
    assert "Git tag" in history
    assert "한 규칙의 소유 문서 하나" in workflow
    assert "docs/archive/" not in "\n".join((readme, agents, workflow))

    versioned_name = re.compile(r"(?:^|[_-])v\d+(?:[._-]\d+)+", re.IGNORECASE)
    dated_name = re.compile(r"(?:19|20)\d{2}[-_]\d{2}[-_]\d{2}")
    forbidden_reports = {"BACKTEST_REPORT.md", "FINAL_REPORT.md"}
    unexpected = []
    for relative in CURRENT_MARKDOWN:
        document = ROOT / relative
        if versioned_name.search(document.stem) or dated_name.search(document.stem):
            unexpected.append(relative)
        if document.name.upper() in forbidden_reports:
            unexpected.append(relative)
    assert unexpected == []


def test_mutable_runtime_status_has_single_source():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    current = (ROOT / "CURRENT_WORK.md").read_text(encoding="utf-8")
    workflow = (ROOT / "docs/infra/DEVELOPMENT_WORKFLOW.md").read_text(encoding="utf-8")

    assert "현재 실제 배포·시작승인 상태" in readme
    assert "CURRENT_WORK.md" in readme
    assert "Oracle 실거래 runtime 배포본" in current
    assert "현재 SHA·배포·다음 행동" in workflow
    assert "Oracle 실거래 runtime 배포본" not in readme
    assert "Oracle 실거래 runtime 배포본" not in workflow


def test_legacy_strategy_config_is_clearly_archived():
    legacy = (ROOT / "configs/strategy_v1.1.2.yaml").read_text(encoding="utf-8")
    assert legacy.startswith("# ARCHIVE ONLY:")
    assert "저장소 루트 strategy.yaml만 사용" in legacy


def test_history_preserves_representative_versions_and_rejected_candidate():
    history = (ROOT / "docs/HISTORY.md").read_text(encoding="utf-8")
    guide = (ROOT / "docs/STRATEGY_GUIDE.md").read_text(encoding="utf-8")

    for version in ("v1.1.2", "v2.2.2", "v3.0.0", "v3.2.2"):
        assert version in history
    assert "SEMIMONTHLY_BAND_H05" in history
    assert "월간 코어 + 5% 부스터" in history
    assert "2026-09-01 — V3.2.2 유지와 최초진입 방식 결정" in history
    assert "현재 운영지시로 사용하지 않습니다" in history
    assert "JDSS 3.2.2" in guide


def test_strategy_guide_contains_summary_detail_and_plain_language_topics():
    guide = (ROOT / "docs/STRATEGY_GUIDE.md").read_text(encoding="utf-8")

    for required in (
        "3분 요약",
        "flowchart",
        "HWM75",
        "RS6M",
        "실제 하루 주문 흐름",
        "자동매수 후보",
        "JH AUTO 내부 2단계 검증",
        "정규장",
        "SAFE_MODE",
        "QQQ 단순보유 비교",
        "64.29%",
        "미니 용어사전",
        "과거검증은 미래수익을 보장하지 않습니다",
    ):
        assert required in guide


def test_jh_auto_live_capital_launch_and_hardening_contract_is_explicit():
    spec = (ROOT / "docs/JH_AUTO_SPEC.md").read_text(encoding="utf-8")
    security = (ROOT / "docs/infra/SECURITY.md").read_text(encoding="utf-8")
    telegram = (ROOT / "docs/TELEGRAM_BOT_GUIDE.md").read_text(encoding="utf-8")
    deployment = (ROOT / "docs/infra/DEPLOYMENT.md").read_text(encoding="utf-8")
    text = "\n".join((spec, security, telegram, deployment))

    for required in (
        "운용 기준자금",
        "자동운용비율",
        "현재 허용원금",
        "HWM75 현재 위험예산",
        "/auto start",
        "주문 0건",
        "50→75→100",
        "실제 JH AUTO 주문 체결 증거",
        "broker POST 직전",
        "동일 신호 자동시도",
        "최대 3회 / UTC 일자",
        "전체 자동 BUY 시도",
        "최대 5회 / UTC 일자",
        "/halt",
        "자동해제",
        "UNKNOWN",
        "blind retry",
    ):
        assert required in text

    # Mutable hardening numbers have a single owner: JH_AUTO_SPEC.
    assert "최대 3회 / UTC 일자" in spec
    assert "최대 5회 / UTC 일자" in spec
    assert "최대 3회 / UTC 일자" not in security
    assert "최대 5회 / UTC 일자" not in security


def test_telegram_guide_owns_usage_and_message_standard():
    telegram = (ROOT / "docs/TELEGRAM_BOT_GUIDE.md").read_text(encoding="utf-8")

    for required in (
        "/dashboard",
        "/today",
        "/auto",
        "/account",
        "/portfolio",
        "/halt",
        "/resume",
        "메시지 표현 표준",
        "용어 표준",
        "성과 표시 표준",
        "대표님 행동",
        "4,096자",
        "자금투입 50→75→100",
    ):
        assert required in telegram


def test_deployment_owns_live_commissioning_and_document_only_policy():
    deployment = (ROOT / "docs/infra/DEPLOYMENT.md").read_text(encoding="utf-8")

    for required in (
        "최초 실계좌 연결",
        "live_commissioned=1",
        "launch_authorized=1",
        "startup quarantine",
        "rollback",
        "문서-only 변경",
        "Oracle runtime은 재배포하지 않습니다",
    ):
        assert required in deployment


def test_public_markdown_omits_operational_identifiers():
    documents = [ROOT / relative for relative in CURRENT_MARKDOWN]
    text = "\n".join(document.read_text(encoding="utf-8") for document in documents)

    for forbidden in (
        "/home/ubuntu/",
        "jh_holdings_bot",
        "jd_holdings_bot",
        "migration-backup",
    ):
        assert forbidden not in text

    assert re.search(r"github\.com/[^\s)]+/actions/runs/\d+", text) is None
    assert re.search(r"jdss-\d{8}T\d{6}Z-[0-9a-f]+\.db", text) is None
