"""Tests verifying that the provider-agnostic architecture holds.

Profile calculation must not import from tiingo-specific modules.
Factory must produce correct concrete types.
"""

import ast
from pathlib import Path

import pytest

_SRC_ROOT = Path(__file__).parent.parent.parent / "src"


def _module_imports(rel_path: str) -> set[str]:
    """Return all imported module names from a Python source file."""
    src = (_SRC_ROOT / rel_path).read_text()
    tree = ast.parse(src)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
    return names


def test_profile_module_has_no_tiingo_imports():
    """market/profile.py must not import from tiingo adapter."""
    imports = _module_imports("invest_ml/market/profile.py")
    tiingo = [i for i in imports if "tiingo" in i]
    assert not tiingo, f"profile.py imports tiingo: {tiingo}"


def test_service_module_has_no_tiingo_imports():
    """market/service.py must not import from tiingo adapter."""
    imports = _module_imports("invest_ml/market/service.py")
    tiingo = [i for i in imports if "tiingo" in i]
    assert not tiingo, f"service.py imports tiingo: {tiingo}"


def test_factory_produces_eod_provider_for_tiingo():
    from invest_ml.market.providers.factory import create_price_provider
    from invest_ml.market.providers.tiingo.eod_provider import TiingoEodProvider

    provider = create_price_provider("tiingo", api_token="test", base_url="http://fake")
    assert isinstance(provider, TiingoEodProvider)


def test_factory_raises_for_unknown_provider():
    from invest_ml.market.providers.factory import create_price_provider

    with pytest.raises(ValueError, match="Unknown"):
        create_price_provider("polygon", api_token="test", base_url="http://fake")
