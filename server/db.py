"""SQLite のスキーマと接続。

DBはログ・イベントから作り直せる「キャッシュ」。壊れたり形を変えたりしたら
ファイルを消して起動し直せば、全ログから取り込み直す（数秒で終わる量）。
"""
import os
import sqlite3

# スキーマを変えたら上げる。値が違うDBは作り直す
SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

-- 取り込み済みの位置（ファイルごと）。追記されたぶんだけ読むため
CREATE TABLE IF NOT EXISTS file_state (
    path   TEXT PRIMARY KEY,
    offset INTEGER NOT NULL
);

-- 仮想売買（ペーパートレード）。exit_* が NULL のものは保有中
CREATE TABLE IF NOT EXISTS paper_trades (
    id              INTEGER PRIMARY KEY,
    strategy        TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    trade_date      TEXT NOT NULL,          -- エントリー日 YYYY-MM-DD
    entry_at        TEXT NOT NULL,
    entry_price     REAL NOT NULL,
    trigger         TEXT,
    stop_loss_pct   REAL,
    take_profit_pct REAL,
    exit_at         TEXT,
    exit_price      REAL,
    exit_reason     TEXT,
    return_pct      REAL,
    estimated       INTEGER NOT NULL DEFAULT 0, -- 1=接続断を後から推計で埋めたもの（IF補完）
    source          TEXT NOT NULL,          -- log / events
    UNIQUE (strategy, symbol, trade_date)   -- 仮想売買は同一銘柄1日1回まで（PaperBook）
);
CREATE INDEX IF NOT EXISTS ix_paper_date ON paper_trades (trade_date);

-- 実売買（建玉成立〜決済約定）
CREATE TABLE IF NOT EXISTS real_trades (
    id          INTEGER PRIMARY KEY,
    strategy    TEXT,
    symbol      TEXT NOT NULL,
    trade_date  TEXT NOT NULL,
    qty         REAL NOT NULL,
    entry_at    TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_at     TEXT,
    exit_price  REAL,
    exit_reason TEXT,
    return_pct  REAL,
    pnl         REAL,                       -- 円（手数料・税は含まない）
    UNIQUE (symbol, entry_at)
);
CREATE INDEX IF NOT EXISTS ix_real_date ON real_trades (trade_date);

-- 実発注・取消・注文終了のログ（注文の履歴表示用）
CREATE TABLE IF NOT EXISTS order_events (
    id       INTEGER PRIMARY KEY,
    at       TEXT NOT NULL,
    strategy TEXT,
    what     TEXT NOT NULL,
    order_id TEXT,
    symbol   TEXT,
    side     TEXT,
    qty      REAL,
    price    REAL,
    note     TEXT,
    text     TEXT,
    UNIQUE (at, what, order_id, text)
);
CREATE INDEX IF NOT EXISTS ix_order_at ON order_events (at);

-- ランナーの稼働に関わる出来事（自動売買のモード・接続断・エラー）
CREATE TABLE IF NOT EXISTS runner_events (
    id    INTEGER PRIMARY KEY,
    at    TEXT NOT NULL,
    kind  TEXT NOT NULL,                    -- autotrade_mode / runner_notice / error
    level TEXT,
    text  TEXT NOT NULL,
    UNIQUE (at, kind, text)
);
CREATE INDEX IF NOT EXISTS ix_runner_at ON runner_events (at);
"""


def connect(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    ver = None
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        ver = int(row[0]) if row else None
    except sqlite3.OperationalError:
        pass
    if ver is not None and ver != SCHEMA_VERSION:
        conn.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(path + suffix)
            except FileNotFoundError:
                pass
        return connect(path)
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    conn.commit()
    return conn
