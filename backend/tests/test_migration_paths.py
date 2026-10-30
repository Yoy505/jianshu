"""F01 迁移路径回归（SQLite 常驻，PG 版见 tests/integration/test_pg_integration.py）。

工作一线 a7d0e4f6b8c3 与工作二线 e9a4c7f1b5d8 对 mp_user_consents 的 created_at /
updated_at 做了同源修复，合并头 9d5def0e96c6。本文件覆盖任务书要求的关键反例：
空库、两条旧头、全量回退再升级、回退到分叉点、单侧回退（另一侧仍需这组列）、
存量同意记录不丢失。每条路径以子进程真实执行 alembic（与部署一致，不走 create_all）。
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
TABLE = "mp_user_consents"
TS_COLUMNS = {"created_at", "updated_at"}
MERGE_HEAD = "9d5def0e96c6"  # 工作一 × 工作二 合并点（之后仍可有新修订）
WORK1_HEAD = "a7d0e4f6b8c3"
WORK2_HEAD = "e9a4c7f1b5d8"
WORK1_PARENT = "f6c9d3e5a7b2"
WORK2_PARENT = "d4f7b2c8e6a1"
FORK_POINT = "e5c9a3f7b1d8"


def script_head() -> str:
    """迁移配置的有效目标头（不写死修订号，F08 同口径）。"""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1, f"迁移存在多个头，需先合并: {heads}"
    return heads[0]


def alembic(db_path: Path, *args: str) -> str:
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}", "APP_ENV": "test"}
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"alembic {' '.join(args)} 失败:\n{proc.stderr[-1500:]}"
    return proc.stdout + proc.stderr


def columns(db_path: Path, table: str = TABLE) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def versions(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {row[0] for row in conn.execute("SELECT version_num FROM alembic_version")}


def insert_consent(db_path: Path, created_at: str = "2026-01-02 03:04:05") -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"INSERT INTO {TABLE} (user_id, purpose, version, text, created_at, updated_at) "
            "VALUES (1, 'steward_service', 'v1', '同意文本', ?, ?)",
            (created_at, created_at),
        )


def consent_rows(db_path: Path) -> list[tuple]:
    with sqlite3.connect(db_path) as conn:
        cols = columns(db_path)
        picked = ["user_id", "purpose", "version"] + [c for c in ("created_at",) if c in cols]
        return conn.execute(f"SELECT {', '.join(picked)} FROM {TABLE} ORDER BY id").fetchall()


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "migration.db"


def test_empty_to_head(db: Path):
    alembic(db, "upgrade", "head")
    assert versions(db) == {script_head()}
    assert TS_COLUMNS <= set(columns(db))


@pytest.mark.parametrize("old_head", [WORK1_HEAD, WORK2_HEAD], ids=["work1_old_head", "work2_old_head"])
def test_old_head_upgrades_to_merge_head(db: Path, old_head: str):
    """两条旧库路径：先升到各自旧头（已含时间戳列），再升到合并头不得重复加列。"""
    alembic(db, "upgrade", old_head)
    assert versions(db) == {old_head}
    assert TS_COLUMNS <= set(columns(db))
    insert_consent(db)
    alembic(db, "upgrade", "head")
    assert versions(db) == {script_head()}
    cols = columns(db)
    assert cols.count("created_at") == 1 and cols.count("updated_at") == 1
    assert consent_rows(db) == [(1, "steward_service", "v1", "2026-01-02 03:04:05")]


def test_head_to_base_and_back(db: Path):
    """全量回退不得因两条线重复删列而中断；回退后可再次升级到合并头。"""
    alembic(db, "upgrade", "head")
    alembic(db, "downgrade", "base")
    with sqlite3.connect(db) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert TABLE not in tables
    alembic(db, "upgrade", "head")
    assert versions(db) == {script_head()}
    assert TS_COLUMNS <= set(columns(db))


def test_downgrade_to_fork_point_drops_columns_once_and_keeps_rows(db: Path):
    """回退到分叉点：两条线都已撤销，列应恰好被删一次；存量同意记录保留。"""
    alembic(db, "upgrade", "head")
    insert_consent(db)
    alembic(db, "downgrade", FORK_POINT)
    assert versions(db) == {FORK_POINT}
    assert not (TS_COLUMNS & set(columns(db)))
    assert consent_rows(db) == [(1, "steward_service", "v1")]
    alembic(db, "upgrade", "head")
    assert TS_COLUMNS <= set(columns(db))
    assert len(consent_rows(db)) == 1


@pytest.mark.parametrize(
    ("target", "expected_heads", "label"),
    [
        (WORK1_PARENT, {WORK1_PARENT, WORK2_HEAD}, "撤销工作一线的时间戳修订，工作二线仍需这组列"),
        (WORK2_PARENT, {WORK1_HEAD, WORK2_PARENT}, "撤销工作二线的时间戳修订，工作一线仍需这组列"),
    ],
    ids=["revert_work1_keep_for_work2", "revert_work2_keep_for_work1"],
)
def test_single_line_revert_keeps_columns_for_remaining_line(
    db: Path, target: str, expected_heads: set[str], label: str
):
    """单侧回退（降到旧头的父修订）：另一条线仍在已应用修订内，这组列必须保留且数据不变。"""
    alembic(db, "upgrade", "head")
    insert_consent(db)
    alembic(db, "downgrade", target)
    assert versions(db) == expected_heads, label
    assert TS_COLUMNS <= set(columns(db)), label
    assert consent_rows(db) == [(1, "steward_service", "v1", "2026-01-02 03:04:05")], label
    alembic(db, "upgrade", "head")
    assert versions(db) == {script_head()}
    assert columns(db).count("created_at") == 1
