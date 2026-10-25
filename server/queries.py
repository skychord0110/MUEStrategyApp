"""画面に出すデータを組み立てる。すべて読み取りのみ。"""
import json
import os
import re
from datetime import date, datetime

import yaml

from .logparse import STRATEGY_TO_LABEL


# 仮想売買をしない基礎ストラテジー・外部ツールの表示名（MUEStrategy の notifier.STRATEGY_LABELS）
BASE_LABELS = {
    "small_lot_sell_detector": "小口売り連続",
    "panic_sell_detector": "投げ売り",
    "under_surge_detector": "UNDER急増",
    "periodic_buy_zscore": "定期買い集め",
}


def label(strategy):
    return STRATEGY_TO_LABEL.get(strategy) or BASE_LABELS.get(strategy, strategy)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _read_yaml(path):
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return None


# ── 口座 ──────────────────────────────────────────────

def account(settings) -> dict:
    """ランナーが15秒ごとに書く account.json をそのまま返す（＋鮮度）。

    ランナー停止中はファイルが無い（終了時に消す）。異常終了で残った古い
    ファイルを「いまの値」と見せないよう、更新時刻で鮮度を判定する。
    """
    snap = _read_json(settings.account_path)
    if not snap:
        return {"available": False, "fresh": False, "snapshot": None, "age_seconds": None}
    age = None
    try:
        age = (datetime.now() - datetime.fromisoformat(snap["updated_at"])).total_seconds()
    except (KeyError, TypeError, ValueError):
        pass
    fresh = age is not None and age <= settings.account_stale_seconds
    return {"available": True, "fresh": fresh, "age_seconds": age, "snapshot": snap}


# ── 稼働状況 ──────────────────────────────────────────

def _last_log_time(settings):
    path = os.path.join(settings.logs_dir, f"runner_{date.today():%Y-%m-%d}.log")
    try:
        return datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec="seconds")
    except OSError:
        return None


def strategies_config(settings) -> dict:
    """ストラテジーのON/OFFと自動売買の設定（表示のみ。ここからは変えられない）。"""
    runner = _read_yaml(settings.runner_config_path) or {}
    auto = _read_yaml(settings.autotrade_config_path) or {}
    rows = []
    for key, cfg in (runner.get("strategies") or {}).items():
        if isinstance(cfg, dict):
            rows.append({"key": key, "label": label(key), "enabled": bool(cfg.get("enabled"))})
    real = [k for k, v in (auto.get("strategies") or {}).items() if v is True]
    return {
        "environment": runner.get("environment"),
        "strategies": rows,
        "autotrade": {
            "enabled": bool(auto.get("enabled")),
            "dry_run": auto.get("dry_run", True) is not False,
            "strategies": real,
        },
    }


def status(conn, settings) -> dict:
    acc = account(settings)
    today = date.today().isoformat()
    paper_today = conn.execute(
        "SELECT COUNT(*) AS entries, SUM(exit_at IS NOT NULL) AS exits,"
        " SUM(exit_at IS NULL) AS holding, SUM(return_pct) AS total_return"
        " FROM paper_trades WHERE trade_date=? AND estimated=0", (today,)).fetchone()
    mode = conn.execute(
        "SELECT at, text FROM runner_events WHERE kind='autotrade_mode' ORDER BY at DESC LIMIT 1"
    ).fetchone()
    notices = conn.execute(
        "SELECT at, kind, level, text FROM runner_events WHERE kind IN ('runner_notice','error')"
        " AND at >= ? ORDER BY at DESC LIMIT 20", (today,)).fetchall()
    error_count = conn.execute(
        "SELECT COUNT(*) FROM runner_events WHERE kind='error' AND at >= ?", (today,)).fetchone()[0]
    return {
        "now": datetime.now().isoformat(timespec="seconds"),
        "runner_running": acc["fresh"],
        "account_age_seconds": acc["age_seconds"],
        "last_log_at": _last_log_time(settings),
        "autotrade_mode": dict(mode) if mode else None,
        "paper_today": dict(paper_today),
        "errors_today": error_count,
        "notices": [dict(r) for r in notices],
        "config": strategies_config(settings),
    }


# ── 売買履歴・成績 ────────────────────────────────────

_TABLE = {"paper": "paper_trades", "real": "real_trades"}


def _where(kind, date_from, date_to, strategy, include_estimated):
    clauses, args = [], []
    if date_from:
        clauses.append("trade_date >= ?")
        args.append(date_from)
    if date_to:
        clauses.append("trade_date <= ?")
        args.append(date_to)
    if strategy:
        clauses.append("strategy = ?")
        args.append(strategy)
    if kind == "paper" and not include_estimated:
        clauses.append("estimated = 0")
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", args


def trades(conn, kind, date_from=None, date_to=None, strategy=None,
           include_estimated=False, limit=500) -> list:
    where, args = _where(kind, date_from, date_to, strategy, include_estimated)
    rows = conn.execute(
        f"SELECT * FROM {_TABLE[kind]}{where} ORDER BY entry_at DESC LIMIT ?",
        args + [limit]).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["label"] = label(d.get("strategy"))
        out.append(d)
    return out


def performance(conn, kind, date_from=None, date_to=None, include_estimated=False) -> dict:
    """戦略ごとの成績と、日ごとの損益率の合計（累積グラフ用）。

    損益率は1トレードあたりの%を単純に足したもの（同じ金額で毎回入った場合の目安）。
    実売買は円の損益も出す（手数料・税は含まない）。
    """
    where, args = _where(kind, date_from, date_to, None, include_estimated)
    table = _TABLE[kind]
    pnl = "SUM(pnl)" if kind == "real" else "NULL"
    rows = conn.execute(
        f"SELECT strategy, COUNT(*) AS trades, COUNT(return_pct) AS closed,"
        f" SUM(exit_at IS NULL) AS open,"
        f" SUM(return_pct > 0) AS wins, SUM(return_pct <= 0) AS losses,"
        f" AVG(return_pct) AS avg_return, SUM(return_pct) AS total_return,"
        f" MAX(return_pct) AS best, MIN(return_pct) AS worst, {pnl} AS pnl"
        f" FROM {table}{where} GROUP BY strategy ORDER BY total_return DESC", args).fetchall()
    reasons = conn.execute(
        f"SELECT strategy, exit_reason, COUNT(*) AS n FROM {table}{where}"
        f"{' AND' if where else ' WHERE'} exit_reason IS NOT NULL GROUP BY strategy, exit_reason",
        args).fetchall()
    by_reason = {}
    for r in reasons:
        by_reason.setdefault(r["strategy"], {})[r["exit_reason"]] = r["n"]
    strategies = []
    for r in rows:
        d = dict(r)
        d["label"] = label(d["strategy"])
        d["win_rate"] = (d["wins"] / d["closed"] * 100) if d["closed"] else None
        d["exit_reasons"] = by_reason.get(d["strategy"], {})
        strategies.append(d)
    daily = conn.execute(
        f"SELECT trade_date, strategy, SUM(return_pct) AS ret, COUNT(return_pct) AS n"
        f" FROM {table}{where}{' AND' if where else ' WHERE'} return_pct IS NOT NULL"
        f" GROUP BY trade_date, strategy ORDER BY trade_date", args).fetchall()
    return {"kind": kind, "from": date_from, "to": date_to,
            "strategies": strategies, "daily": [dict(r) for r in daily]}


def order_history(conn, date_from=None, date_to=None, limit=300) -> list:
    clauses, args = [], []
    if date_from:
        clauses.append("at >= ?")
        args.append(date_from)
    if date_to:
        clauses.append("at < date(?, '+1 day')")
        args.append(date_to)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(f"SELECT * FROM order_events{where} ORDER BY at DESC, id DESC LIMIT ?",
                        args + [limit]).fetchall()
    return [dict(r) for r in rows]


# ── 週次レポート（analysis/output/<日付>/*.md） ─────────

_REPORT_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}(-[a-z]+)?$")
_REPORT_FILE = re.compile(r"^[\w.-]+\.md$")


def reports(settings) -> list:
    base = settings.analysis_output_dir
    out = []
    try:
        names = sorted(os.listdir(base), reverse=True)
    except OSError:
        return out
    for name in names:
        d = os.path.join(base, name)
        if not (_REPORT_DIR.match(name) and os.path.isdir(d)):
            continue
        files = sorted(f for f in os.listdir(d) if _REPORT_FILE.match(f))
        if files:
            out.append({"dir": name, "files": files})
    return out


def report_text(settings, dirname, filename):
    """レポート1本の本文。名前は形式を照合し、output の外は読まない。"""
    if not (_REPORT_DIR.match(dirname) and _REPORT_FILE.match(filename)):
        return None
    base = os.path.realpath(settings.analysis_output_dir)
    path = os.path.realpath(os.path.join(base, dirname, filename))
    if os.path.dirname(os.path.dirname(path)) != base or not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()
