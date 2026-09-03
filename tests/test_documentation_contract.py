from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


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


def test_document_roles_and_change_impact_are_explicit():
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    guide = (ROOT / "docs/README.md").read_text(encoding="utf-8")

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
    for required in (
        "JDSS_FINAL_SPEC.md",
        "JH_AUTO_SPEC.md",
        "TELEGRAM_BOT_GUIDE.md",
        "DEPLOYMENT.md",
        "DEVELOPMENT_WORKFLOW.md",
        "SECURITY.md",
        "ONE_PAGE_REPORT.md",
        "STRATEGY_GUIDE.md",
        "HISTORY.md",
    ):
        assert required in guide


def test_document_lifecycle_uses_fixed_current_files_and_git_history():
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    guide = (ROOT / "docs/README.md").read_text(encoding="utf-8")
    history = (ROOT / "docs/HISTORY.md").read_text(encoding="utf-8")
    workflow = (ROOT / "docs/infra/DEVELOPMENT_WORKFLOW.md").read_text(encoding="utf-8")

    assert "롤링 상태판" in agents
    assert "제자리 갱신" in guide
    assert "파일명은 고정" in guide
    assert "append-only 역사 색인" in history
    assert "Git tag" in history
    assert not (ROOT / "docs/infra/DECISIONS.md").exists()

    lifecycle_text = "\n".join((agents, guide, workflow))
    assert "docs/archive/" not in lifecycle_text

    versioned_name = re.compile(r"(?:^|[_-])v\d+(?:[._-]\d+)+", re.IGNORECASE)
    dated_name = re.compile(r"(?:19|20)\d{2}[-_]\d{2}[-_]\d{2}")
    forbidden_reports = {"BACKTEST_REPORT.md", "FINAL_REPORT.md"}
    unexpected = []
    managed_documents = [*ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md")]
    for document in managed_documents:
        relative = document.relative_to(ROOT)
        if versioned_name.search(document.stem) or dated_name.search(document.stem):
            unexpected.append(str(relative))
        if document.name.upper() in forbidden_reports:
            unexpected.append(str(relative))
    assert unexpected == []


def test_mutable_runtime_status_has_single_source():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    docs_readme = (ROOT / "docs/README.md").read_text(encoding="utf-8")
    workflow = (ROOT / "docs/infra/DEVELOPMENT_WORKFLOW.md").read_text(encoding="utf-8")

    assert "현재 실제 배포·시작승인 상태" in readme
    assert "CURRENT_WORK.md" in readme
    assert "현재 브랜치·배포·다음 작업" in docs_readme
    assert "branch와 마지막 commit" in workflow
    assert "## 현재 운영 기준" not in docs_readme
    assert "## 현재 활성 개발 브랜치" not in workflow


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
    assert "JDSS 3.2.2" in guide


def test_one_page_report_and_guide_cover_required_plain_language_topics():
    report = (ROOT / "docs/ONE_PAGE_REPORT.md").read_text(encoding="utf-8")
    guide = (ROOT / "docs/STRATEGY_GUIDE.md").read_text(encoding="utf-8")

    for required in (
        "미니 용어사전",
        "QQQ 단순보유 비교",
        "실제 하루 주문 흐름",
        "SAFE_MODE",
        "과거검증은 미래수익을 보장하지 않습니다",
    ):
        assert required in report
    for required in (
        "flowchart",
        "HWM75",
        "RS6M",
        "자동매수 후보",
        "JH AUTO 내부 2단계 검증",
        "정규장",
        "64.29%",
    ):
        assert required in guide


def test_onboarding_contract_lives_in_existing_current_documents():
    spec = (ROOT / "docs/JDSS_FINAL_SPEC.md").read_text(encoding="utf-8")
    telegram = (ROOT / "docs/TELEGRAM_BOT_GUIDE.md").read_text(encoding="utf-8")
    docs_readme = (ROOT / "docs/README.md").read_text(encoding="utf-8")

    assert "최초진입 50% → 75% → 100%" in spec
    assert "50%" in spec and "75%" in spec and "100%" in spec
    assert "stale 버튼" in spec
    assert "/onboarding" in telegram
    assert "자금투입 50→75→100" in telegram
    assert "버튼 응답" in docs_readme
    assert not (ROOT / "docs/INITIAL_ONBOARDING.md").exists()
    assert "INITIAL_ONBOARDING.md" not in docs_readme


def test_jh_auto_live_capital_and_launch_contract_is_explicit():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    telegram = (ROOT / "docs/TELEGRAM_BOT_GUIDE.md").read_text(encoding="utf-8")
    messages = (ROOT / "docs/TELEGRAM_LIVE_MESSAGE_STANDARD.md").read_text(encoding="utf-8")
    security = (ROOT / "docs/infra/SECURITY.md").read_text(encoding="utf-8")
    commissioning = (ROOT / "docs/infra/LIVE_COMMISSIONING.md").read_text(encoding="utf-8")
    text = "\n".join((readme, telegram, messages, security, commissioning))

    for required in (
        "JDSS 3.2.2",
        "JH AUTO 1.0.0",
        "공식 연구·백테스트",
        "운용 기준자금",
        "자동운용비율",
        "현재 허용원금",
        "HWM75 현재 위험예산",
        "/auto start",
        "주문 0건",
        "개별 BUY",
        "/halt",
        "자동해제",
    ):
        assert required in text

    assert "JDSS 연구 기준 `$50,000` = 실거래 고정한도" in messages
    assert "최초 시작 전 legacy HWM `$50,000` = 현재 HWM75 위험예산" in messages


def test_public_markdown_omits_operational_identifiers():
    documents = [*ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md")]
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
