"""common/은 순수 도메인 모듈이다.

FastAPI · Pydantic · SQLAlchemy · Starlette와 api/batch를 import하지 않는다.
"""

import ast
from pathlib import Path

import pytest

COMMON = Path(__file__).resolve().parents[1] / "common"
FORBIDDEN = {"fastapi", "pydantic", "pydantic_core", "sqlalchemy", "starlette", "api", "batch"}


def _imported_roots(path: Path) -> set[str]:
    roots = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize("path", sorted(COMMON.glob("*.py")), ids=lambda p: p.name)
def test_common_has_no_framework_imports(path):
    assert not (_imported_roots(path) & FORBIDDEN)


def test_obsolete_recommendation_concepts_are_gone():
    """7.11: 보수/기대 예산, 지원 유형 · 선정 방식, min-max, room 가중치는 남아 있으면 안 된다."""
    source = "\n".join(p.read_text(encoding="utf-8") for p in COMMON.glob("*.py"))
    for word in ("SUPPORT_GRANT", "SELECTION_AUTO", "RESERVE_MONTHS", "conservative", "min_max", '"room"'):
        assert word not in source, word
