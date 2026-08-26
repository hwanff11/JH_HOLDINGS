import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINNED_ACTION_RE = re.compile(r"^\s*(?:-\s+)?uses:\s+[^@\s]+@[0-9a-f]{40}(?:\s+#.*)?$")


def _normalize_pin(value: str) -> str:
    name, version = value.strip().lower().split("==", 1)
    return f"{re.sub(r'[-_.]+', '-', name)}=={version}"


def test_runtime_dependencies_exclude_unused_scientific_packages():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    dependencies = tuple(str(value).lower() for value in project["dependencies"])

    assert not any(value.startswith("scipy") for value in dependencies)
    assert not any(value.startswith("scikit-learn") for value in dependencies)


def test_requirements_runtime_pins_match_pyproject():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    pyproject_pins = {_normalize_pin(str(value)) for value in project["dependencies"]}
    requirements_pins = {
        _normalize_pin(line)
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert requirements_pins == pyproject_pins


def test_production_lock_covers_all_direct_dependencies_with_exact_versions():
    direct_pins = {
        _normalize_pin(line)
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    lock_lines = [
        line.strip().lower()
        for line in (ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert all(line.count("==") == 1 for line in lock_lines)
    assert direct_pins <= {_normalize_pin(line) for line in lock_lines}


def test_install_paths_apply_the_production_constraint_file():
    deploy = (ROOT / "deploy.sh").read_text(encoding="utf-8")
    assert '--constraint "$release_dir/requirements.lock"' in deploy

    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        text = workflow.read_text(encoding="utf-8")
        if "pip install -e" in text:
            assert "--constraint requirements.lock" in text, workflow.name


def test_all_external_github_actions_are_pinned_to_full_commit_sha():
    unpinned: list[str] = []
    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        for line_number, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), 1):
            if "uses:" not in line or "uses: ./" in line:
                continue
            if not PINNED_ACTION_RE.fullmatch(line):
                unpinned.append(f"{workflow.name}:{line_number}:{line.strip()}")

    assert unpinned == []
