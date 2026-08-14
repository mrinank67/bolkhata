"""Every environment variable the code reads must be documented in .env.example.

Without this, a new os.getenv() call is a silent production misconfiguration:
it returns None, the feature degrades quietly, and nothing in the repo records
that the variable was ever needed.
"""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = REPO_ROOT / ".env.example"

# First-party source only. A recursive glob would sweep in .venv and report the
# hundreds of env vars that third-party libraries read.
EXCLUDED_DIRS = {".venv", "env", "node_modules", "graphify-out", "tests", "__pycache__"}


def _source_files() -> list[Path]:
    return sorted(
        p
        for p in REPO_ROOT.rglob("*.py")
        if not any(part in EXCLUDED_DIRS for part in p.relative_to(REPO_ROOT).parts)
    )


def _env_keys_in(path: Path) -> set[str]:
    """Collect literal keys from os.getenv("X") and os.environ.get("X").

    AST-based rather than regex so a commented-out or string-embedded call is
    not counted.
    """
    keys: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue

        is_getenv = (
            func.attr == "getenv" and isinstance(func.value, ast.Name) and func.value.id == "os"
        )
        is_environ_get = (
            func.attr == "get"
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "environ"
        )
        if not (is_getenv or is_environ_get):
            continue

        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            keys.add(first.value)

    return keys


def _documented_keys() -> set[str]:
    keys = set()
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0].strip())
    return keys


def test_env_example_exists():
    assert ENV_EXAMPLE.exists(), ".env.example is the only checked-in record of required config"


def test_every_read_env_var_is_documented():
    used: dict[str, list[str]] = {}
    for path in _source_files():
        for key in _env_keys_in(path):
            used.setdefault(key, []).append(str(path.relative_to(REPO_ROOT)))

    missing = {k: v for k, v in used.items() if k not in _documented_keys()}
    assert not missing, (
        "These environment variables are read in code but absent from .env.example:\n"
        + "\n".join(
            f"  {k}  (read in {', '.join(sorted(files))})" for k, files in sorted(missing.items())
        )
    )


def test_no_stale_keys_in_env_example():
    """Documented-but-unread keys mislead whoever sets up a new environment."""
    used = set()
    for path in _source_files():
        used |= _env_keys_in(path)

    stale = _documented_keys() - used
    assert not stale, ".env.example documents variables nothing reads: " + ", ".join(sorted(stale))


@pytest.mark.parametrize(
    "secret_key", ["GROQ_API_KEY", "SARVAM_API_KEY", "PAY_LINK_SECRET", "FIREBASE_SERVICE_ACCOUNT"]
)
def test_env_example_ships_no_real_values(secret_key):
    """.env.example is committed — every secret slot must be blank."""
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith(f"{secret_key}="):
            assert line.split("=", 1)[1].strip() == "", (
                f"{secret_key} has a value in .env.example — that file is committed"
            )
