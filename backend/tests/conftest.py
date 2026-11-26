"""共享测试夹具：独立 DB + FakeDriver。"""
from __future__ import annotations

import pytest

from app import config
from app import db as appdb
from app import logs as applogs

# 测试绝不能把日志写进真实数据目录（~/.autotest/data 或用户自定义目录）：
# 必须在任何 test 模块 import app.main、触发 create_app() 之前关掉文件日志。
applogs.disable_file_logging()


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """每个测试一个独立的 data_dir（环境变量层），绝不写 ~/.autotest 或仓库 data/。

    之前测试能跑通是因为本机 ~/.autotest/config.json 把 data_dir 指到仓库里；
    默认目录现在在工作区之外（也符合真实默认 ~/.autotest/data），必须显式隔离。
    """
    monkeypatch.setenv("AUTOTEST_DATA_DIR", str(tmp_path / "data"))
    config.reset_to_defaults()
    yield


@pytest.fixture(autouse=True)
def isolated_diag_dir(tmp_path, monkeypatch):
    """诊断 JSONL 一律写到 tmp，测试绝不污染用户真实数据目录。"""
    from app import diagnostics

    target = tmp_path / "diag"
    target.mkdir(exist_ok=True)
    monkeypatch.setattr(diagnostics, "diag_dir", lambda: target)
    yield target


@pytest.fixture()
def temp_db(tmp_path):
    appdb.reset_engine_for_tests(tmp_path / "test.db")
    yield
    appdb.reset_engine_for_tests  # noqa: B018 - 下一测试自行重置
