import pytest


@pytest.fixture()
def isolated_env(tmp_path, monkeypatch):
    import app.db as db_mod
    from app import seed as seed_mod
    from app.services import ingest as ingest_svc

    data_dir = tmp_path / "data"
    fixtures_dir = tmp_path / "fixtures"
    data_dir.mkdir()
    fixtures_dir.mkdir()
    db_file = data_dir / "clickspan.db"
    # 路径常量被各模块以 `from app.db import ...` 在导入期绑定，
    # 必须同时 patch 持有该名字的每个模块。
    monkeypatch.setattr(db_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(db_mod, "FIXTURES_DIR", fixtures_dir)
    monkeypatch.setattr(db_mod, "DB_PATH", db_file)
    monkeypatch.setattr(ingest_svc, "FIXTURES_DIR", fixtures_dir)
    monkeypatch.setattr(seed_mod, "FIXTURES_DIR", fixtures_dir)
    monkeypatch.setattr(ingest_svc, "connect", db_mod.connect)
    # 仅建表与默认设置；夹具播种由具体测试在写入 seed_hits.json 后自行 init_db。
    seed_mod.init_db()
    yield fixtures_dir
