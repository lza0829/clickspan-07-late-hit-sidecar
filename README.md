# 点跨（Clickspan）

点击流装载 → 按访客空闲超时切会话 → 脏事件隔离 → 会话证明互证。

**不是** 运单轨迹时间线（途迹）、变更投影流（changecast）、CDC Sink、券核销钉扎、日结封账、双边对齐、改价价签、证据包放行。  
去皮后是：**事件装载 + 会话窗物化 + 闸/隔离 + 证明**。

## 启动

```powershell
# 后端
cd backend
pip install -r requirements.txt
$env:DATA_DIR = "$PWD\data"
$env:FIXTURES_DIR = "$PWD\..\fixtures"
uvicorn app.main:app --host 127.0.0.1 --port 10900

# 前端
cd frontend
npm install
npm run dev
```

| 入口 | 地址 |
| --- | --- |
| 前端 | http://127.0.0.1:5900 |
| 接口 | http://127.0.0.1:10900 |

## UI

**泳道织机**：顶栏品牌 + 顶栏菜单（织机 / 收流 / 搁置 / 核证 / 切窗）+ 横向会话串珠。  
不用底栏导航、不用三栏测绘板、不用左轨编号、不用流程 ribbon。

## 迟到旁路（late_hit_sidecar）

- 定死规则：同访客**无 open 且有 closed** 时，该访客本批击点在收流入口直接分流到迟到旁路——不改任何 closed 成员、不在本规则下自动开新 open（开新 open 仅由空闲切窗/着陆等规则显式触发）。
- 落库形态：独立 `late_hit_sidecar` 表（访客 / 原始载荷 `payload_json` / 所属 closed 会话提示 `closed_session_id` / 批次 / 原因），与正式 `hits` 物理隔离；旁路点不计入 `sessions.hit_count`，也不进 `session_proofs`。
- 失败闭合：分流与正式写入同处一个 `BEGIN IMMEDIATE` 事务，任一环节失败整趟回滚；`hit_id` 唯一，重放幂等。
- 只读核证：`GET /api/late-sidecar`（可带 `visitor_id` 过滤），织机页底部「迟到旁路条」同源展示，无任何写/迁回入口。
- 夹具：`seed_hits.json` 中 V-1004 声明 `session_status: "closed"` 构造「无 open 仅有 closed」，收流批 H11/H12 即其迟到击点。

