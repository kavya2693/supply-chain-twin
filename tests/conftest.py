from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from supply_twin.twin import Sources, TwinState, build_twin, load_sources

ROOT = Path(__file__).resolve().parents[1]


def _generator():
    spec = importlib.util.spec_from_file_location("generate_data", ROOT / "scripts" / "generate_data.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["generate_data"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def small_data(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("data")
    _generator().generate(out, seed=11, n_stores=6, n_skus=24, n_trucks=8)
    return out


@pytest.fixture(scope="session")
def sources(small_data: Path) -> Sources:
    return load_sources(small_data)


@pytest.fixture(scope="session")
def twin(sources: Sources) -> TwinState:
    return build_twin(sources)


@pytest.fixture(scope="session")
def categories():
    return _generator().CATEGORIES
