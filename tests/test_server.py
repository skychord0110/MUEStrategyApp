# -*- coding: utf-8 -*-
"""中継サーバのテスト（ログの読み解き・取り込み・API の守り）。

ログの行は本番ログ（MUEStrategy の runner_*.log）からそのまま写したもの。

実行: .venv\\Scripts\\python -m pytest
"""
import json
import os

import pytest
from fastapi.testclient import TestClient

from server import db, ingest, logparse
from server.app import create_app
from server.settings import Settings

# ── 本番ログの実例 ──
L_ENTRY = "2026-10-23 09:09:20,386 [INFO] [AI投げ売り反発/エントリー] 4579 4579: 投げ売り を検知、590.0円で仮想買い（損切り-1.0%/利確+2.0%/残りは大引け・発注なし）"
L_EXIT = "2026-10-23 09:12:08,711 [INFO] [AI投げ売り反発/決済:利確] 4579 4579: 仮想決済 590.0円→606.0円 (+2.71%)"
L_ENTRY_NO_TP = "2026-10-23 09:24:46,012 [INFO] [AI買い集め追随/エントリー] 4013 4013: 定期買い集め を検知、786.0円で仮想買い（損切り-2.0%/利確なし/残りは大引け・発注なし）"
L_ENTRY_IF = "2026-09-03 13:10:00,000 [INFO] [AI午後引け戻り/エントリー] 6217 6217: UNDER急増[IF補完] を検知、794.0円で仮想買い（if補完・接続断の推計・発注なし）"
L_ORDER_BUY = "2026-08-31 14:29:13,663 [WARNING] [afternoon_reversal] [実発注] 5590 買 200株 指値 703円 [エントリー(指値買い @703.0)]"
L_ORDER_ACK = "2026-08-31 14:29:20,653 [WARNING] [afternoon_reversal] [実発注 受付] OrderId=20260831A02N09289795  5590 買 200株 指値 703円 [エントリー(指値買い @703.0)]"
L_DONE = "2026-08-31 14:29:21,340 [INFO] [注文終了] OrderId=20260831A02N09289795 約定200.0/200.0株"
L_OPEN = "2026-08-31 14:29:23,242 [INFO] [自動売買] 建玉成立 5590 200株 @703.0 [HOLDING] 目標718 損切り688 下限681"
L_ORDER_SELL = "2026-08-31 15:26:05,605 [WARNING] [afternoon_reversal] [実発注] 5590 売 200株 指値 682円 [引け手仕舞い(買い気配-3.0% 指値売り @682)]"
L_CLOSE = "2026-08-31 15:30:09,019 [WARNING] [自動売買/約定] 5590 200.0株 → 損益 -2.99%"
L_FAIL = "2026-08-14 13:30:38,524 [ERROR] [afternoon_reversal] [実発注 失敗] 500 Server Error: Internal Server Error for url: http://localhost:18080/kabusapi/sendorder ← 4579 買 100株 指値 512円 [エントリー(指値買い @512.0)]"
L_NOISE = "2026-10-23 09:03:53,136 [INFO] [小口売り連続/WATCH] 3692 3692: 買い気配8590.0円に小口売り5回連続 (直近の推定約定株数 100.0株)"


def test_parse_paper_lines():
    e = logparse.parse_line(L_ENTRY)
    assert e["ev"] == "paper_entry" and e["strategy"] == "panic_rebound"
    assert (e["symbol"], e["price"], e["stop_loss_pct"], e["take_profit_pct"]) == ("4579", 590.0, 1.0, 2.0)
    assert e["estimated"] is False
    x = logparse.parse_line(L_EXIT)
    assert (x["ev"], x["reason"], x["exit_price"], x["return_pct"]) == ("paper_exit", "利確", 606.0, 2.71)
    assert logparse.parse_line(L_ENTRY_NO_TP)["take_profit_pct"] is None
    assert logparse.parse_line(L_ENTRY_IF)["estimated"] is True
    assert logparse.parse_line(L_NOISE) is None


def test_parse_real_lines():
    o = logparse.parse_line(L_ORDER_ACK)
    assert (o["what"], o["order_id"], o["symbol"], o["side"], o["qty"], o["price"]) == \
        ("実発注 受付", "20260831A02N09289795", "5590", "買", 200.0, 703.0)
    f = logparse.parse_line(L_FAIL)
    assert (f["what"], f["symbol"], f["side"]) == ("実発注 失敗", "4579", "買")
    assert logparse.parse_line(L_OPEN)["price"] == 703.0
    assert logparse.parse_line(L_CLOSE)["return_pct"] == -2.99


# ── 取り込み ──

@pytest.fixture
def env(tmp_path):
    s = Settings(mue_root=str(tmp_path / "MUEStrategy"), db_path=str(tmp_path / "app.db"),
                 allowed_logins=["me@example.com"])
    os.makedirs(s.logs_dir)
    os.makedirs(s.events_dir)
    return s


def write(path, lines):
    with open(path, "a", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")


def test_ingest_paper_and_real(env):
    write(os.path.join(env.logs_dir, "runner_2026-08-31.log"),
          [L_ORDER_BUY, L_ORDER_ACK, L_DONE, L_OPEN, L_ORDER_SELL, L_CLOSE])
    write(os.path.join(env.logs_dir, "runner_2026-10-23.log"), [L_ENTRY, L_NOISE, L_EXIT])
    conn = db.connect(env.db_path)
    ingest.run(conn, env)
    p = conn.execute("SELECT * FROM paper_trades").fetchall()
    assert len(p) == 1 and p[0]["exit_reason"] == "利確" and p[0]["return_pct"] == 2.71
    r = conn.execute("SELECT * FROM real_trades").fetchone()
    assert r["strategy"] == "afternoon_reversal"
    assert (r["entry_price"], r["exit_price"], r["exit_reason"]) == (703.0, 682.0, "引け手仕舞い")
    assert r["pnl"] == pytest.approx(-4200.0)
    # 2回目は何も増えない
    assert ingest.run(conn, env) == {"events": 0, "log": 0}
    assert conn.execute("SELECT COUNT(*) FROM order_events").fetchone()[0] == 4


def test_ingest_reads_only_complete_lines(env):
    path = os.path.join(env.logs_dir, "runner_2026-10-23.log")
    with open(path, "w", encoding="utf-8") as f:
        f.write(L_ENTRY + "\n" + L_EXIT[:40])        # 決済行を書きかけ
    conn = db.connect(env.db_path)
    ingest.run(conn, env)
    assert conn.execute("SELECT exit_at FROM paper_trades").fetchone()[0] is None
    with open(path, "a", encoding="utf-8") as f:
        f.write(L_EXIT[40:] + "\n")
    ingest.run(conn, env)
    assert conn.execute("SELECT return_pct FROM paper_trades").fetchone()[0] == 2.71


def test_exit_is_not_matched_to_an_older_unclosed_entry(env):
    # 10/22 のエントリーは接続断で決済が記録されなかった。10/23 の決済を付けてはいけない
    write(os.path.join(env.logs_dir, "runner_2026-10-22.log"), [L_ENTRY.replace("2026-10-23", "2026-10-22")])
    write(os.path.join(env.logs_dir, "runner_2026-10-23.log"), [L_EXIT])
    conn = db.connect(env.db_path)
    ingest.run(conn, env)
    rows = conn.execute("SELECT trade_date, exit_reason FROM paper_trades").fetchall()
    assert [tuple(r) for r in rows] == [("2026-10-22", None)]


def test_events_and_log_are_not_double_counted(env):
    write(os.path.join(env.events_dir, "events_2026-10-23.jsonl"), [
        json.dumps({"v": 1, "kind": "paper", "strategy": "panic_rebound", "type": "ENTRY",
                    "symbol": "4579", "price": 590.0, "time": "2026-10-23T09:09:19",
                    "stop_loss_pct": 1.0, "take_profit_pct": 2.0, "trigger": "panic_sell_detector",
                    "logged_at": "2026-10-23T09:09:20.386"}, ensure_ascii=False),
        json.dumps({"v": 1, "kind": "paper", "strategy": "panic_rebound", "type": "EXIT",
                    "symbol": "4579", "reason": "利確", "entry_price": 590.0, "exit_price": 606.0,
                    "return_pct": 2.7118, "time": "2026-10-23T09:12:08",
                    "logged_at": "2026-10-23T09:12:08.711"}, ensure_ascii=False),
    ])
    write(os.path.join(env.logs_dir, "runner_2026-10-23.log"), [L_ENTRY, L_EXIT])
    conn = db.connect(env.db_path)
    ingest.run(conn, env)
    rows = conn.execute("SELECT * FROM paper_trades").fetchall()
    assert len(rows) == 1
    assert rows[0]["source"] == "events" and rows[0]["return_pct"] == pytest.approx(2.7118)


# ── API の守り ──

@pytest.fixture
def client(env):
    write(os.path.join(env.logs_dir, "runner_2026-10-23.log"), [L_ENTRY, L_EXIT])
    conn = db.connect(env.db_path)
    ingest.run(conn, env)
    conn.close()
    out = os.path.join(env.analysis_output_dir, "2026-10-23")
    os.makedirs(out)
    with open(os.path.join(out, "metrics_2026-10-23.md"), "w", encoding="utf-8") as f:
        f.write("# 週次\n")
    with open(os.path.join(env.analysis_output_dir, "secret.md"), "w", encoding="utf-8") as f:
        f.write("x")
    return TestClient(create_app(env, run_ingest=False))


ME = {"Tailscale-User-Login": "Me@Example.com"}


def test_requires_allowed_tailscale_login(client):
    assert client.get("/api/status").status_code == 403
    assert client.get("/api/status", headers={"Tailscale-User-Login": "other@example.com"}).status_code == 403
    assert client.get("/", headers={"Tailscale-User-Login": "other@example.com"}).status_code == 403
    assert client.get("/api/status", headers=ME).status_code == 200


def test_read_only(client):
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)("/api/status", headers=ME).status_code == 405


def test_performance_and_trades(client):
    p = client.get("/api/performance?kind=paper&from=2026-10-01&to=2026-10-31", headers=ME).json()
    s = p["strategies"][0]
    assert (s["strategy"], s["closed"], s["wins"], s["win_rate"]) == ("panic_rebound", 1, 1, 100.0)
    assert p["daily"] == [{"trade_date": "2026-10-23", "strategy": "panic_rebound", "ret": 2.71, "n": 1}]
    assert client.get("/api/performance?kind=real", headers=ME).json()["strategies"] == []
    assert client.get("/api/performance?kind=x", headers=ME).status_code == 400
    assert client.get("/api/trades?from=2026/10/01", headers=ME).status_code == 422
    t = client.get("/api/trades?kind=paper", headers=ME).json()
    assert t[0]["label"] == "AI投げ売り反発"


def test_reports_stay_inside_output_dir(client):
    assert client.get("/api/reports", headers=ME).json() == [
        {"dir": "2026-10-23", "files": ["metrics_2026-10-23.md"]}]
    assert client.get("/api/reports/2026-10-23/metrics_2026-10-23.md", headers=ME).text == "# 週次\n"
    assert client.get("/api/reports/2026-10-23/..%2Fsecret.md", headers=ME).status_code == 404
    assert client.get("/api/reports/../secret.md", headers=ME).status_code in (403, 404)


def test_account_when_runner_stopped(client):
    a = client.get("/api/account", headers=ME).json()
    assert a == {"available": False, "fresh": False, "snapshot": None, "age_seconds": None}
    assert client.get("/api/orders", headers=ME).json()["today"] is None


def test_security_headers(client):
    r = client.get("/", headers=ME)
    assert r.status_code == 200
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert client.get("/api/status", headers=ME).headers["cache-control"] == "no-store"
