"""pytest 全局配置：强制离线合成数据源，隔离数据目录。"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

# --------------------------------------------------------------- 环境隔离
# 必须在导入 app 之前设置：强制使用合成数据源，测试全程不触网
os.environ["DATA_SOURCE_MODE"] = "synthetic"
os.environ["UNIVERSE_SIZE"] = "300"
os.environ["CACHE_TTL_SECONDS"] = "300"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["HTTP_TIMEOUT"] = "3"

_TMP_DATA_DIR = tempfile.mkdtemp(prefix="lub_test_data_")
os.environ["DATA_DIR"] = _TMP_DATA_DIR

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="session")
def client():
    """会话级 TestClient（复用扫描缓存，避免重复全市场扫描）。"""
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session", autouse=True)
def _cleanup_tmp_data():
    """会话结束后清理临时数据目录。"""
    yield
    shutil.rmtree(_TMP_DATA_DIR, ignore_errors=True)


@pytest.fixture(scope="session")
def provider():
    """合成数据源实例（会话级，生成一次）。"""
    from app.providers.synthetic import SyntheticProvider

    instance = SyntheticProvider()
    instance._ensure()
    return instance


@pytest.fixture(scope="session")
def rule_set():
    from app.models import RuleSet

    return RuleSet()


@pytest.fixture(scope="session")
def signals(client):
    """预取一次 /signals，供其他用例复用。"""
    resp = client.get("/api/v1/signals", params={"pageSize": 200})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]
