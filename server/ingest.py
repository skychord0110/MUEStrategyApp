"""ランナーのログ・イベントファイルを SQLite に取り込む。

読むのは本番フォルダのファイルだけで、**書き込みは一切しない**。
ファイルごとに読んだ位置を覚えておき、追記されたぶんだけ読む（ランナーは
稼働中も書き足していくので、最後の改行より後ろ＝書きかけの行は次回に回す）。

同じ日に イベント（events_*.jsonl）と ログ の両方がある場合は、イベントを先に読む。
同じ取引がログ側からも来るが、UNIQUE 制約（同一戦略・銘柄・日）と
「保有中の建玉が無い決済は捨てる」で二重には数えない。ランナーを日中に
入れ替えてイベントが途中からしか無い日も、これでログから補える。
"""
import glob
import json
import logging
import os
import re
from datetime import datetime

from . import logparse

log = logging.getLogger("mueapp.ingest")

_DATE_IN_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _files(settings):
    """取り込むファイルを (日付, 順序, 種類, パス) の古い順で返す。"""
    out = []
    for p in glob.glob(os.path.join(settings.events_dir, "events_*.jsonl")):
        m = _DATE_IN_NAME.search(os.path.basename(p))
        if m:
            out.append((m.group(1), 0, "events", p))
    for p in glob.glob(os.path.join(settings.logs_dir, "runner_*.log")):
        m = _DATE_IN_NAME.search(os.path.basename(p))
        if m:
            out.append((m.group(1), 1, "log", p))
    out.sort()
    return out


def _read_new_lines(conn, path):
    """前回の続きから、改行で終わっている行だけを返す。"""
    row = conn.execute("SELECT offset FROM file_state WHERE path=?", (path,)).fetchone()
    offset = row[0] if row else 0
    size = os.path.getsize(path)
    if size < offset:          # ファイルが作り直された
        offset = 0
    if size == offset:
        return [], offset
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read(size - offset)
    end = data.rfind(b"\n")
    if end < 0:
        return [], offset
    chunk = data[:end + 1]
    lines = chunk.decode("utf-8", errors="replace").splitlines()
    return lines, offset + len(chunk)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# ── 仮想売買 ──────────────────────────────────────────

def _paper_entry(conn, strategy, symbol, at, price, trigger, sl, tp, source, estimated=False):
    conn.execute(
        "INSERT OR IGNORE INTO paper_trades (strategy, symbol, trade_date, entry_at, entry_price,"
        " trigger, stop_loss_pct, take_profit_pct, estimated, source) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (strategy, symbol, at.date().isoformat(), _iso(at), price, trigger, sl, tp,
         int(bool(estimated)), source))


def _paper_exit(conn, strategy, symbol, at, entry_price, exit_price, reason, ret):
    # 仮想建玉は当日中に決済される。翌営業日以降に決済されるのは「大引け(補完)」だけ。
    # それ以外で日をまたいで突き合わせると、ランナーの再起動や接続断で決済が
    # 記録されなかった古いエントリーに、別の日の決済を誤って付けてしまう
    same_day = "" if reason and "補完" in reason else " AND trade_date = ?"
    args = (strategy, symbol, _iso(at)) + (() if same_day == "" else (at.date().isoformat(),))
    row = conn.execute(
        "SELECT id, entry_price FROM paper_trades WHERE strategy=? AND symbol=? AND exit_at IS NULL"
        " AND entry_at <= ?" + same_day + " ORDER BY entry_at DESC LIMIT 1", args).fetchone()
    if row is None:
        return       # 既に決済済み（別ソースから取り込み済み）か、エントリーが取り込み範囲外
    if ret is None:
        ret = (exit_price - row["entry_price"]) / row["entry_price"] * 100
    conn.execute(
        "UPDATE paper_trades SET exit_at=?, exit_price=?, exit_reason=?, return_pct=? WHERE id=?",
        (_iso(at), exit_price, reason, ret, row["id"]))


def _apply_event(conn, e: dict):
    """events_*.jsonl の1行（MUEStrategy の runner/src/events.py が書く形）。"""
    if e.get("kind") != "paper":
        return
    at = datetime.fromisoformat(e.get("time") or e["logged_at"])
    if e.get("type") == "ENTRY":
        _paper_entry(conn, e["strategy"], str(e["symbol"]), at, float(e["price"]),
                     e.get("trigger"), e.get("stop_loss_pct"), e.get("take_profit_pct"), "events",
                     "IF補完" in (e.get("trigger") or ""))
    elif e.get("type") == "EXIT":
        _paper_exit(conn, e["strategy"], str(e["symbol"]), at, e.get("entry_price"),
                    float(e["exit_price"]), e.get("reason"), e.get("return_pct"))


# ── 実売買 ────────────────────────────────────────────

def _short_reason(note):
    """'引け手仕舞い(買い気配-3.0% 指値売り @682)' → '引け手仕舞い'"""
    if not note:
        return None
    return note.split("(", 1)[0].strip() or note


def _real_open(conn, p):
    row = conn.execute(
        "SELECT strategy FROM order_events WHERE symbol=? AND side='買' AND at<=?"
        " ORDER BY at DESC LIMIT 1", (p["symbol"], _iso(p["at"]))).fetchone()
    conn.execute(
        "INSERT OR IGNORE INTO real_trades (strategy, symbol, trade_date, qty, entry_at, entry_price)"
        " VALUES (?,?,?,?,?,?)",
        (row["strategy"] if row else None, p["symbol"], p["at"].date().isoformat(), p["qty"],
         _iso(p["at"]), p["price"]))


def _real_close(conn, p):
    row = conn.execute(
        "SELECT id, entry_price, qty FROM real_trades WHERE symbol=? AND exit_at IS NULL"
        " ORDER BY entry_at DESC LIMIT 1", (p["symbol"],)).fetchone()
    if row is None:
        return
    exit_price = p["price"]
    if exit_price is None:
        # 古い書式は約定価格を出していない。損益率から逆算する（0.01%単位の丸めが入る）
        exit_price = round(row["entry_price"] * (1 + p["return_pct"] / 100), 1)
    sell = conn.execute(
        "SELECT note FROM order_events WHERE symbol=? AND side='売' AND at<=?"
        " ORDER BY at DESC LIMIT 1", (p["symbol"], _iso(p["at"]))).fetchone()
    conn.execute(
        "UPDATE real_trades SET exit_at=?, exit_price=?, exit_reason=?, return_pct=?, pnl=? WHERE id=?",
        (_iso(p["at"]), exit_price, _short_reason(sell["note"]) if sell else None,
         p["return_pct"], (exit_price - row["entry_price"]) * row["qty"], row["id"]))


def _apply_log(conn, p: dict):
    ev = p["ev"]
    if ev == "paper_entry":
        _paper_entry(conn, p["strategy"], p["symbol"], p["at"], p["price"], p["trigger"],
                     p["stop_loss_pct"], p["take_profit_pct"], "log", p["estimated"])
    elif ev == "paper_exit":
        _paper_exit(conn, p["strategy"], p["symbol"], p["at"], p["entry_price"],
                    p["exit_price"], p["reason"], p["return_pct"])
    elif ev == "order":
        conn.execute(
            "INSERT OR IGNORE INTO order_events (at, strategy, what, order_id, symbol, side, qty,"
            " price, note, text) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (_iso(p["at"]), p["strategy"], p["what"], p["order_id"], p.get("symbol"),
             p.get("side"), p.get("qty"), p.get("price"), p.get("note"), p["text"]))
    elif ev == "order_done":
        conn.execute(
            "INSERT OR IGNORE INTO order_events (at, what, order_id, qty, text) VALUES (?,?,?,?,?)",
            (_iso(p["at"]), "注文終了", p["order_id"], p["filled"],
             f"約定{p['filled']:g}/{p['qty']:g}株"))
    elif ev == "real_open":
        _real_open(conn, p)
    elif ev == "real_close":
        _real_close(conn, p)
    elif ev in ("autotrade_mode", "runner_notice", "error"):
        conn.execute("INSERT OR IGNORE INTO runner_events (at, kind, level, text) VALUES (?,?,?,?)",
                     (_iso(p["at"]), ev, p.get("level"), p["text"][:500]))


def run(conn, settings) -> dict:
    """新しく追記されたぶんを取り込む。取り込んだ行数を返す。"""
    counts = {"events": 0, "log": 0}
    for _date, _, kind, path in _files(settings):
        lines, new_offset = _read_new_lines(conn, path)
        if not lines:
            continue
        with conn:
            for line in lines:
                if kind == "events":
                    try:
                        e = json.loads(line)
                    except ValueError:
                        log.warning("壊れたイベント行を読み飛ばしました: %s", path)
                        continue
                    _apply_event(conn, e)
                else:
                    p = logparse.parse_line(line)
                    if p is not None:
                        _apply_log(conn, p)
            conn.execute("INSERT OR REPLACE INTO file_state (path, offset) VALUES (?, ?)",
                         (path, new_offset))
        counts[kind] += len(lines)
    return counts
