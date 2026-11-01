# MUEStrategy モバイル

MUEStrategy の稼働状況・口座・仮想売買／実売買の成績をスマホから見るための
**閲覧専用**アプリ。PCで中継サーバ（FastAPI）を動かし、スマホは Tailscale 経由で
PWA（ホーム画面に追加できるWebアプリ）として開く。

```
スマホ（PWA）
  │ HTTPS（Tailscale のネットワーク内だけ。インターネットには公開しない）
  ▼
tailscale serve ── Tailscale-User-Login ヘッダーを付けて中継
  │
  ▼
127.0.0.1:8765  中継サーバ（このリポジトリの server/）
  │ ・GET/HEAD 以外は拒否（発注・設定変更などの書き込みAPIは無い）
  │ ・ログイン名が allowed_logins に無ければ 403
  │ ・ログを60秒ごとに SQLite（data/app.db）へ取り込む
  ▼
本番の MUEStrategy フォルダ（読むだけ）
  strategies/runner/state/account.json      口座（ランナーが15秒ごとに書く）
  strategies/runner/state/events/*.jsonl    仮想売買のイベント
  strategies/runner/logs/runner_*.log       過去の仮想売買・実売買・注文・エラー
  strategies/runner/config.yaml ほか         ストラテジーのON/OFF（表示のみ）
  analysis/output/<日付>/*.md               週次レポート
```

**kabuステーションAPIは呼ばない。** トークンを発行するとランナーのトークンが
失効し、自動売買の発注・取消まで止まるため（詳細は CLAUDE.md）。

## 画面

実売買（実際の口座・お金が動く）は**オレンジ**、仮想売買（シミュレーション・発注なし）は
**青**で、タブ・帯・カードの左線・「実」「仮想」の札を色分けしている。

| タブ | 内容 |
|---|---|
| 状況 | 上段が実売買（資産残高・買付余力・評価損益・建玉・自動売買のモード・本日の注文）、下段が仮想売買（本日のエントリーと損益）。最後にストラテジーのON/OFFと本日のエラー・接続断 |
| 実売買 | 現在の建玉、期間ごとの成績（件数・勝率・損益（円））と累積グラフ、売買履歴、発注・取消の記録 |
| 仮想売買 | 期間ごとの成績（ストラテジー別の勝率・平均・合計・決済理由）と累積グラフ、履歴（ストラテジーで絞り込み） |
| レポート | 週次分析（analysis/output）の Markdown |

建玉は、ランナーの稼働中は15秒ごとの値を出す。ランナーが止まると account.json が
消えるので、稼働中に見えた最後の値を `data/account_last.json` に残しておき、
「◯月◯日 ◯:◯ 時点の最終値」として出す（サーバが動いている間に一度もランナーが
動いていなければ、まだ値は無い）。自動売買が建てた建玉（ログの「建玉成立」で未決済）
には戦略名を付け、それ以外は「手動・その他」と表示する。

接続断を後から推計で埋めた仮想売買（ログの `[IF補完]`）は、成績では既定で除外する
（「推計を含める」で切り替え）。損益率の合計は1トレードの%を単純に足したもの。

## セットアップ（PC）

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
cp config.example.yaml config.yaml   # allowed_logins に自分の Tailscale ログイン名を書く
```

起動は `start_server.bat`（または `.venv/Scripts/python -m server`）。
MUEStrategy のコントロールパネルを開くと自動で起動する（MUEStrategy の
`ui/mobile_server.py`。すでに動いていれば何もしない。出力は `data/server.log`）。
初回は全ログの取り込みに数秒かかる。DBはログから作り直せるので、おかしくなったら
`data/` を消して起動し直せばよい。

## Tailscale の設定

1. PCとスマホに Tailscale を入れ、同じアカウントでログインする
2. 管理画面（login.tailscale.com）の DNS で **MagicDNS** と **HTTPS Certificates** を有効にする
3. PCで中継を設定する（設定は残るので一度だけ）:

   ```bash
   tailscale serve --bg --https=443 http://127.0.0.1:8765
   ```

   確認: `tailscale serve status`。**`tailscale funnel` は使わない**（インターネットに公開される）。
4. スマホのブラウザで `https://<PC名>.<tailnet名>.ts.net/` を開き、
   共有メニューから「ホーム画面に追加」

`allowed_logins` には `tailscale status` に出る自分のログイン名（メールアドレス）を書く。
タグ付きデバイスからのアクセスにはログイン名が付かないので、見られない。

## 守っていること

- 待ち受けは 127.0.0.1 のみ（設定でループバック以外を指定すると起動しない）
- Tailscale のログイン名を許可リストと照合（`tailscale serve` 経由でないと付かない）
- GET/HEAD 以外は 405。書き込み系のAPIは作っていない
- CSP で外部への通信・外部スクリプトを禁止。画面は外部のCDNやフォントを読まない
- 口座・売買データ（/api/*）は `Cache-Control: no-store`。Service Worker もキャッシュしない
- 本番の MUEStrategy フォルダには書き込まない

ローカルのPC上のプロセスはヘッダーを偽れるので、信頼の境界は「このPC」と「Tailscale のアカウント」。

## テスト

```bash
.venv/Scripts/python -m pytest
```

## メモリについて

稼働PCはメモリが少ない。中継サーバは常駐で数十MB程度。取り込みは差分だけを読む。
アイコンを作り直すときは `python tools/make_icons.py`。
