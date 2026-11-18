"""关闭后迟到击点旁路（late_hit_sidecar）。

对外定死的规则：
- 同访客不存在 open 会话、且存在已 closed 会话时，本批该访客的击点一律进入迟到
  旁路通道：不改任何 closed 会话成员、不在本规则下自动开新 open（开新 open 仅由
  空闲切窗/着陆等其它已实现规则显式触发）。
- 旁路落库采用独立表 :data:`TABLE`（与正式 hits 物理隔离），旁路点永不计入
  sessions.hit_count，也不进 session_proofs。
- 分流在收流入口完成（入口分流路径），与整批收流共用同一事务；任一环节失败整趟
  回滚，不存在“先误写正式再补救”的中间态。
"""
import json

STATUS = "implemented"

CHANNEL = "sidecar"
REASON = "late_after_closed"
TABLE = "late_hit_sidecar"

# 建表 DDL 由 app.seed 在初始化时执行（独立 sidecar 表形态）。
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  hit_id TEXT NOT NULL UNIQUE,
  visitor_id TEXT NOT NULL,
  closed_session_id TEXT,
  event_ts TEXT NOT NULL,
  page_path TEXT NOT NULL,
  event_name TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  batch_id TEXT,
  reason TEXT NOT NULL DEFAULT '{REASON}',
  created_at TEXT NOT NULL
);
"""


def latest_closed_session(conn, visitor_id: str):
    """返回该访客最近一个 closed 会话；无 closed 成员时返回 None。"""
    return conn.execute(
        """
        SELECT id FROM sessions
        WHERE visitor_id=? AND status='closed'
        ORDER BY ended_at DESC, started_at DESC, id DESC
        LIMIT 1
        """,
        (visitor_id,),
    ).fetchone()


def has_open_session(conn, visitor_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sessions WHERE visitor_id=? AND status='open' LIMIT 1",
        (visitor_id,),
    ).fetchone() is not None


def should_divert(conn, visitor_id: str) -> bool:
    """入口分流判定：无 open 且有 closed（仅有 closed 的迟到场景）。"""
    return not has_open_session(conn, visitor_id) and (
        latest_closed_session(conn, visitor_id) is not None
    )


def divert_hit(conn, hit: dict, batch_id: str, now: str, closed_session_id: str | None) -> bool:
    """把单个迟到击点写入旁路表。

    幂等：hit_id 唯一，重复收流重放不会产生第二条旁路记录。
    返回 True 表示新写入，False 表示该击点已在旁路（重放去重）。
    """
    conn.execute(
        f"""
        INSERT OR IGNORE INTO {TABLE}(
          hit_id, visitor_id, closed_session_id, event_ts, page_path,
          event_name, payload_json, batch_id, reason, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            hit["id"],
            hit["visitor_id"],
            closed_session_id,
            hit["event_ts"],
            hit["page_path"],
            hit["event_name"],
            json.dumps(hit, ensure_ascii=False),
            batch_id,
            REASON,
            now,
        ),
    )
    return conn.execute("SELECT changes()").fetchone()[0] == 1


def list_sidecar(conn, visitor_id: str | None = None):
    """核证只读接口的取数函数：不提供任何写操作。"""
    if visitor_id:
        rows = conn.execute(
            f"SELECT * FROM {TABLE} WHERE visitor_id=? ORDER BY id DESC",
            (visitor_id,),
        ).fetchall()
    else:
        rows = conn.execute(f"SELECT * FROM {TABLE} ORDER BY id DESC").fetchall()
    out = []
    for r in rows:
        item = dict(r)
        try:
            item["payload"] = json.loads(item.pop("payload_json"))
        except (ValueError, TypeError):
            item["payload"] = None
        out.append(item)
    return out
