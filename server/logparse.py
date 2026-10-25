"""ランナーのログ（runner_YYYY-MM-DD.log）1行を、取り込める形に読み解く。

ログは人が読むための文言なので、文言が変わると読めなくなる。
仮想売買はランナーが state/events/ に構造のまま書き出すようになったので
（MUEStrategy の strategies/runner/src/events.py）、そちらがある日はそちらを使う。
ここはそれ以前の過去分と、実売買（まだイベントが無い）のためにある。

文言の出どころ（MUEStrategy 側）:
  仮想売買 … strategies/runner/src/notifier.py の build_message
  実売買   … strategies/autotrade/src/trader.py・executor.py
"""
import re
from datetime import datetime

# notifier.STRATEGY_LABELS の逆引き（表示名 → 戦略キー）。
# 新しい戦略が増えたら足す。知らない表示名は表示名のまま戦略キーにする。
LABEL_TO_STRATEGY = {
    "AI午後引け戻り": "afternoon_reversal",
    "AI午後引け戻り(順位優先)": "afternoon_reversal_ranked",
    "AI複合シグナル": "confluence",
    "AI投げ売り反発": "panic_rebound",
    "AI投げ売り反発(幅広)": "panic_rebound_wide",
    "AI買い集め追随": "accumulation_follow",
    "AI VWAP乖離反発": "vwap_discount_reversal",
    "ボラ高流動銘柄UNDER急増": "liquid_under_surge",
    "ボラ急変銘柄 売り板消化反発": "volatile_panic_rebound",
}
STRATEGY_TO_LABEL = {v: k for k, v in LABEL_TO_STRATEGY.items()}

_HEAD = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(\d{3}) \[(\w+)\] (.*)$")
_SYM = r"([0-9A-Z]{4,5})"

_PAPER_ENTRY = re.compile(
    r"^\[(?P<label>[^\]]+)/エントリー\] " + _SYM + r" \S+: (?P<trigger>.+?) を検知、"
    r"(?P<price>[\d.]+)円で仮想買い（(?P<tail>.*)")
_SL = re.compile(r"損切り-(?P<sl>[\d.]+)%")
_TP = re.compile(r"利確\+(?P<tp>[\d.]+)%")
_PAPER_EXIT = re.compile(
    r"^\[(?P<label>[^\]]+)/決済:(?P<reason>[^\]]+)\] " + _SYM + r" \S+: 仮想決済 "
    r"(?P<entry>[\d.]+)円→(?P<exit>[\d.]+)円 \((?P<ret>[+-]?[\d.]+)%\)")

# 実売買
_REAL_ORDER = re.compile(
    r"^\[(?P<strategy>[a-z_]+)\] \[(?P<what>実発注|実発注 受付|実発注 失敗|実発注 エラー|実取消|取消 受付|取消 エラー)\] "
    r"(?:OrderId=(?P<oid>\S+)\s+)?(?P<rest>.*)$")
_ORDER_DESC = re.compile(
    r"^" + _SYM + r" (?P<side>買|売) (?P<qty>\d+)株 (?P<how>指値 (?P<price>[\d.]+)円|成行)"
    r"(?: \[(?P<note>.+)\])?")
_ORDER_DONE = re.compile(r"^\[注文終了\] OrderId=(?P<oid>\S+) 約定(?P<filled>[\d.]+)/(?P<qty>[\d.]+)株")
_POS_OPEN = re.compile(r"^\[自動売買\] 建玉成立 " + _SYM + r" (?P<qty>[\d.]+)株 @(?P<price>[\d.]+)")
_POS_CLOSE = re.compile(
    r"^\[自動売買/約定\] " + _SYM + r" (?P<qty>[\d.]+)株(?: @(?P<price>[\d.]+))? → 損益 (?P<ret>[+-]?[\d.]+)%")

# ランナーの稼働状況
_AUTOTRADE_MODE = re.compile(r"^自動売買: (?P<text>.+)$")
_OUTAGE = re.compile(r"^\[ランナー\] (?P<text>.+)$")


def _f(s):
    return float(s) if s not in (None, "") else None


def parse_line(line: str):
    """1行を読んで dict を返す。取り込み対象でなければ None。

    返す dict は必ず "ev"（種類）と "at"（ログの時刻 datetime）を持つ。
    """
    m = _HEAD.match(line.rstrip("\r\n"))
    if not m:
        return None
    at = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(
        microsecond=int(m.group(2)) * 1000)
    level, msg = m.group(3), m.group(4)

    if msg.startswith("[") and "/エントリー]" in msg:
        e = _PAPER_ENTRY.match(msg)
        if e:
            label = e.group("label")
            sl = _SL.search(e.group("tail"))
            tp = _TP.search(e.group("tail"))
            return {"ev": "paper_entry", "at": at, "label": label,
                    "strategy": LABEL_TO_STRATEGY.get(label, label), "symbol": e.group(2),
                    "trigger": e.group("trigger"), "price": float(e.group("price")),
                    "stop_loss_pct": _f(sl.group("sl")) if sl else None,
                    "take_profit_pct": _f(tp.group("tp")) if tp else None,
                    # 接続断のあいだを後から推計で埋めたエントリー（後追い再現ツール）。
                    # 実際に場中で検知したものではないので、集計では既定で除く
                    "estimated": "IF補完" in e.group("trigger") or "if補完" in e.group("tail")}
    if msg.startswith("[") and "/決済:" in msg:
        e = _PAPER_EXIT.match(msg)
        if e:
            label = e.group("label")
            return {"ev": "paper_exit", "at": at, "label": label,
                    "strategy": LABEL_TO_STRATEGY.get(label, label), "symbol": e.group(3),
                    "reason": e.group("reason"), "entry_price": float(e.group("entry")),
                    "exit_price": float(e.group("exit")), "return_pct": float(e.group("ret"))}

    e = _REAL_ORDER.match(msg)
    if e:
        out = {"ev": "order", "at": at, "level": level, "strategy": e.group("strategy"),
               "what": e.group("what"), "order_id": e.group("oid"), "text": e.group("rest")}
        d = _ORDER_DESC.match(e.group("rest"))
        if d:
            out.update(symbol=d.group(1), side=d.group("side"), qty=float(d.group("qty")),
                       price=_f(d.group("price")), note=d.group("note"))
        else:
            # 失敗・エラーは「<理由> ← <注文内容>」の形
            tail = e.group("rest").rsplit(" ← ", 1)
            d = _ORDER_DESC.match(tail[-1]) if len(tail) == 2 else None
            if d:
                out.update(symbol=d.group(1), side=d.group("side"), qty=float(d.group("qty")),
                           price=_f(d.group("price")), note=d.group("note"))
        return out
    e = _ORDER_DONE.match(msg)
    if e:
        return {"ev": "order_done", "at": at, "order_id": e.group("oid"),
                "filled": float(e.group("filled")), "qty": float(e.group("qty"))}
    e = _POS_OPEN.match(msg)
    if e:
        return {"ev": "real_open", "at": at, "symbol": e.group(1),
                "qty": float(e.group("qty")), "price": float(e.group("price"))}
    e = _POS_CLOSE.match(msg)
    if e:
        return {"ev": "real_close", "at": at, "symbol": e.group(1), "qty": float(e.group("qty")),
                "price": _f(e.group("price")), "return_pct": float(e.group("ret"))}

    e = _AUTOTRADE_MODE.match(msg)
    if e:
        return {"ev": "autotrade_mode", "at": at, "text": e.group("text")}
    e = _OUTAGE.match(msg)
    if e:
        return {"ev": "runner_notice", "at": at, "level": level, "text": e.group("text")}
    if level in ("ERROR", "CRITICAL"):
        return {"ev": "error", "at": at, "text": msg}
    return None
