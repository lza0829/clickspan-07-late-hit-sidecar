import json

import pytest

from app import seed
from app.db import connect
from app.modules import late_hit_sidecar as sidecar
from app.services import ingest as ingest_svc


def _hit(hid, vid, ts="2026-11-17T10:15:00Z", path="/x", name="view"):
    return {
        "id": hid,
        "visitor_id": vid,
        "event_ts": ts,
        "page_path": path,
        "event_name": name,
    }


def _seed_closed_visitor(conn, vid="V-9001", sid=None, n_hits=2):
    sid = sid or f"S-{vid}-closed-1"
    conn.execute(
        "INSERT INTO visitors(id, label, created_at) VALUES (?,?,?)",
        (vid, vid, "2026-11-17T08:00:00Z"),
    )
    conn.execute(
        """
        INSERT INTO sessions(id, visitor_id, started_at, ended_at, hit_count, status)
        VALUES (?,?,?,?,?,'closed')
        """,
        (sid, vid, "2026-11-17T08:10:00Z", "2026-11-17T08:11:00Z", n_hits),
    )
    ids = []
    for i in range(n_hits):
        hid = f"{vid}-H{i}"
        ids.append(hid)
        conn.execute(
            """
            INSERT INTO hits(id, visitor_id, session_id, event_ts, page_path, event_name, channel)
            VALUES (?,?,?,?,?,?,'primary')
            """,
            (hid, vid, sid, f"2026-11-17T08:1{i}:00Z", "/old", "view"),
        )
    conn.execute(
        """
        INSERT INTO session_proofs(session_id, visitor_id, hit_ids_json, hit_count, started_at, ended_at, created_at)
        VALUES (?,?,?,?,?,?,?)
        """,
        (sid, vid, json.dumps(ids), n_hits, "2026-11-17T08:10:00Z", "2026-11-17T08:11:00Z",
         "2026-11-17T08:12:00Z"),
    )
    return sid


def _closed_session_state(conn, sid):
    sess = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    member_ids = [
        r["id"] for r in conn.execute(
            "SELECT id FROM hits WHERE session_id=? AND channel='primary' ORDER BY id", (sid,)
        ).fetchall()
    ]
    proofs = conn.execute(
        "SELECT * FROM session_proofs WHERE session_id=? ORDER BY id", (sid,)
    ).fetchall()
    return dict(sess), member_ids, [dict(p) for p in proofs]


def test_divert_rule_requires_no_open_and_existing_closed(isolated_env):
    conn = connect()
    try:
        # 全新访客（无 open 无 closed）：不分流，走基座开新 open
        assert sidecar.should_divert(conn, "V-nobody") is False

        sid = _seed_closed_visitor(conn, "V-9001")
        assert sidecar.should_divert(conn, "V-9001") is True
        row = sidecar.latest_closed_session(conn, "V-9001")
        assert row["id"] == sid

        # 同访客一旦有 open（其它规则显式开窗后）：不再旁路
        conn.execute(
            "INSERT INTO sessions(id, visitor_id, started_at, ended_at, hit_count, status) "
            "VALUES ('S-V-9001-open-1','V-9001','2026-11-17T09:00:00Z',NULL,0,'open')"
        )
        assert sidecar.should_divert(conn, "V-9001") is False
        conn.rollback()
    finally:
        conn.close()


def test_late_hits_land_in_sidecar_and_never_touch_closed(isolated_env):
    conn = connect()
    try:
        vid = "V-9002"
        sid = _seed_closed_visitor(conn, vid)
        before = _closed_session_state(conn, sid)
        conn.commit()

        # 模拟收流入口对两个迟到点的分流（同事务语义）
        conn.execute("BEGIN IMMEDIATE")
        for h in (_hit("L1", vid), _hit("L2", vid, ts="2026-11-17T10:16:00Z", name="click")):
            assert sidecar.should_divert(conn, vid)
            closed = sidecar.latest_closed_session(conn, vid)
            assert sidecar.divert_hit(conn, h, "B-test", "2026-11-17T10:17:00Z",
                                      closed["id"] if closed else None)
        conn.commit()

        rows = sidecar.list_sidecar(conn)
        assert {r["hit_id"] for r in rows} == {"L1", "L2"}
        assert all(r["visitor_id"] == vid for r in rows)
        assert all(r["closed_session_id"] == sid for r in rows)
        assert all(r["reason"] == sidecar.REASON for r in rows)
        # 旁路行不挂正式 session_id、不属于 hits.channel 模型
        assert all("session_id" not in r and "channel" not in r for r in rows)
        assert all(r["closed_session_id"] is not None for r in rows)
        assert rows[0]["payload"]["id"] in {"L1", "L2"}

        # closed 成员、hit_count、证明一律未变
        after = _closed_session_state(conn, sid)
        assert after == before

        # 旁路点不进正式 hits、不产生该访客的新 open 会话
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM hits WHERE id IN ('L1','L2')"
        ).fetchone()["c"] == 0
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM sessions WHERE visitor_id=? AND status='open'", (vid,)
        ).fetchone()["c"] == 0
    finally:
        conn.close()


def test_sidecar_idempotent_replay(isolated_env):
    conn = connect()
    try:
        vid = "V-9003"
        sid = _seed_closed_visitor(conn, vid)
        conn.commit()
        h = _hit("L9", vid)
        assert sidecar.divert_hit(conn, h, "B-test", "2026-11-17T10:17:00Z", sid)
        conn.commit()
        # 重放同一击点：不产生第二条
        assert sidecar.divert_hit(conn, h, "B-test", "2026-11-17T10:18:00Z", sid) is False
        conn.commit()
        assert conn.execute(
            f"SELECT COUNT(*) AS c FROM {sidecar.TABLE} WHERE hit_id='L9'"
        ).fetchone()["c"] == 1
    finally:
        conn.close()


def test_batch_rolls_back_whole_trip_on_failure(isolated_env):
    conn = connect()
    try:
        vid = "V-9004"
        sid = _seed_closed_visitor(conn, vid)
        before = _closed_session_state(conn, sid)
        conn.commit()

        # 旁路先写两条，随后正式通道因 NOT NULL 缺字段失败：整趟回滚
        conn.execute("BEGIN IMMEDIATE")
        sidecar.divert_hit(conn, _hit("L10", vid), "B-fail", "2026-11-17T10:17:00Z", sid)
        sidecar.divert_hit(conn, _hit("L11", vid, ts="2026-11-17T10:18:00Z"),
                           "B-fail", "2026-11-17T10:17:00Z", sid)
        with pytest.raises(Exception):
            conn.execute(
                "INSERT INTO hits(id, visitor_id, session_id, event_ts, page_path, event_name, channel) "
                "VALUES (?,?,?,?,?,?,'primary')",
                ("L12", vid, sid, None, None, None),
            )
        conn.rollback()

        assert conn.execute(
            f"SELECT COUNT(*) AS c FROM {sidecar.TABLE}"
        ).fetchone()["c"] == 0
        assert _closed_session_state(conn, sid) == before
    finally:
        conn.close()


def test_end_to_end_batch_with_closed_fixture(isolated_env):
    fixtures_dir = isolated_env
    seed_hits = {
        "visitors": [
            {"id": "V-1001", "label": "甲", "created_at": "2026-11-17T08:00:00Z"},
            {"id": "V-1004", "label": "丁（已关闭）", "created_at": "2026-11-17T07:40:00Z",
             "session_status": "closed"},
        ],
        "hits": [
            {"id": "H1", "visitor_id": "V-1001", "event_ts": "2026-11-17T08:10:00Z",
             "page_path": "/home", "event_name": "view"},
            {"id": "H9", "visitor_id": "V-1004", "event_ts": "2026-11-17T07:45:00Z",
             "page_path": "/landing", "event_name": "view"},
            {"id": "H10", "visitor_id": "V-1004", "event_ts": "2026-11-17T07:48:00Z",
             "page_path": "/pricing", "event_name": "click"},
        ],
    }
    batch = {
        "batch_id": "B-demo-e2e",
        "hits": [
            {"id": "H6", "visitor_id": "V-1001", "event_ts": "2026-11-17T09:50:00Z",
             "page_path": "/checkout", "event_name": "view"},
            {"id": "H8", "visitor_id": "V-1003", "event_ts": "2026-11-17T10:00:00Z",
             "page_path": "/home", "event_name": "view", "visitor_label": "丙"},
            {"id": "H11", "visitor_id": "V-1004", "event_ts": "2026-11-17T10:15:00Z",
             "page_path": "/comeback", "event_name": "view"},
            {"id": "H12", "visitor_id": "V-1004", "event_ts": "2026-11-17T10:16:30Z",
             "page_path": "/comeback", "event_name": "click"},
        ],
    }
    (fixtures_dir / "seed_hits.json").write_text(
        json.dumps(seed_hits, ensure_ascii=False), encoding="utf-8")
    (fixtures_dir / "ingest_batch.json").write_text(
        json.dumps(batch, ensure_ascii=False), encoding="utf-8")

    # 夹具已建好空表；此处显式装载种子（含 closed 访客 V-1004）。
    conn0 = connect()
    try:
        seed._seed_fixtures(conn0)
        conn0.commit()
    finally:
        conn0.close()
    result = ingest_svc.run_batch()
    assert result["ok"] is True
    assert result["report"]["written"] == 2      # H6 进既有 open，H8 为新访客开 open
    assert result["report"]["sidecar"] == 2      # H11/H12 进旁路
    assert result["report"]["input"] == 4

    conn = connect()
    try:
        closed_sid = conn.execute(
            "SELECT id FROM sessions WHERE visitor_id='V-1004' AND status='closed'"
        ).fetchone()["id"]
        sess = conn.execute("SELECT * FROM sessions WHERE id=?", (closed_sid,)).fetchone()
        assert sess["hit_count"] == 2            # 仍是 H9/H10，未被迟到点污染
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM hits WHERE session_id=? AND channel='primary'",
            (closed_sid,),
        ).fetchone()["c"] == 2
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM sessions WHERE visitor_id='V-1004' AND status='open'"
        ).fetchone()["c"] == 0                   # 本规则未自动开新 open
        rows = sidecar.list_sidecar(conn, "V-1004")
        assert {r["hit_id"] for r in rows} == {"H11", "H12"}
        assert all(r["closed_session_id"] == closed_sid for r in rows)
        # 正式 hit_count 口径不含旁路：种子 H1/H9/H10 + 批次 H6/H8 共 5 条
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM hits WHERE channel='primary'"
        ).fetchone()["c"] == 5

        # 重跑整批：全部幂等，旁路不重复
        again = ingest_svc.run_batch()
        assert again["ok"] is True
        assert again["report"]["written"] == 0
        assert again["report"]["sidecar"] == 0
        assert conn.execute(
            f"SELECT COUNT(*) AS c FROM {sidecar.TABLE}"
        ).fetchone()["c"] == 2
    finally:
        conn.close()


def test_sidecar_endpoint_is_read_only(isolated_env):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        r = client.get("/api/late-sidecar")
        assert r.status_code == 200
        assert r.json() == []
        # 只读核证：不接受写方法
        assert client.post("/api/late-sidecar", json={}).status_code in (405, 404)
        ov = client.get("/api/overview").json()
        assert "sidecar" in ov
