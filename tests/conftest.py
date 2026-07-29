import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from daybreak.config import load_config          # noqa: E402
from daybreak.data.ingest import ingest_fixture  # noqa: E402


@pytest.fixture(scope="session")
def cfg_store(tmp_path_factory):
    """Small synthetic fixture shared by the slower integration tests."""
    cfg = load_config()
    cfg["run"]["data_dir"] = str(tmp_path_factory.mktemp("pit"))
    cfg["universe"]["fixture_size"] = 40
    store = ingest_fixture(cfg)
    return cfg, store
