import ast
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RULE_LINE = re.compile(r"^- \*\*R(\d+)\*\* :(.*)$", re.MULTILINE)
TEST_NAME = re.compile(r"^test_r(\d+)_")
TASK_HEADING = re.compile(r"^### Task (\w+) :", re.MULTILINE)


def _active_rules() -> set[int]:
    spec = (ROOT / "docs" / "spec.md").read_text(encoding="utf-8")
    return {int(n) for n, rest in RULE_LINE.findall(spec) if "supprimée" not in rest}


def _tested_rules() -> set[int]:
    found: set[int] = set()
    for path in (ROOT / "tests").glob("test_*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                match = TEST_NAME.match(node.name)
                if match:
                    found.add(int(match.group(1)))
    return found


def _plan_tasks() -> set[str]:
    text = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "docs" / "plans").glob("*.md"))
    return set(TASK_HEADING.findall(text))


def _pending() -> dict[str, str]:
    with (ROOT / "tests" / "rules_pending.toml").open("rb") as handle:
        return tomllib.load(handle)


def test_spec_rules_are_found() -> None:
    rules = _active_rules()
    assert {1, 20, 47, 50} <= rules
    assert not {31, 42} & rules  # removed rules are not active


def test_every_active_rule_has_a_test_or_is_pending() -> None:
    waiting = {int(key[1:]) for key in _pending()}
    missing = sorted(_active_rules() - _tested_rules() - waiting)
    assert missing == [], f"rules without a test nor a pending task: {missing}"


def test_pending_values_are_plan_task_numbers() -> None:
    tasks = _plan_tasks()
    bad = {rule: task for rule, task in _pending().items() if task not in tasks}
    assert bad == {}


def test_pending_keys_are_active_rules() -> None:
    keys = _pending()
    assert all(re.fullmatch(r"R\d+", key) for key in keys)
    unknown = sorted(int(key[1:]) for key in keys if int(key[1:]) not in _active_rules())
    assert unknown == []
