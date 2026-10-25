# MUEStrategyApp

MUEStrategy（株式の自動売買・分析）の状態を見るためのスマホアプリを開発するリポジトリ。
アプリ本体はこのリポジトリに置き、MUEStrategy 側に必要な変更（データの中継など）は
MUEStrategy の作業用worktreeで行う。2つのリポジトリを横断して実装する。

## 技術スタック（2026-10-25 にユーザーが決定）

| 項目 | 決定 |
|---|---|
| 画面 | PWA（素のHTML/CSS/JS。ビルドなし。`web/`） |
| サーバ | FastAPI + uvicorn（`server/`）。127.0.0.1 でのみ待ち受け |
| データ | SQLite（`data/app.db`）。ログ・イベントから作り直せるキャッシュ |
| 公開・認証 | `tailscale serve` で tailnet 内にHTTPS公開し、`Tailscale-User-Login` を許可リストと照合。Funnel は使わない |
| 操作範囲 | **閲覧専用**。発注・取消・ON/OFF・設定変更は作らない（GET/HEAD以外は拒否） |
| 取り込み | 過去分はログをパース。仮想売買はランナーが書くイベントJSONL（MUEStrategy の `runner/src/events.py`）も読む |

これ以外の新しい依存パッケージ・外部サービス（CDNを含む）を足すときは、ユーザーに確認する。
書き込み操作を足すかどうかも、引き続き必ず確認を取ること。

## リポジトリとworktreeの構成（この前提で作業する）

| パス | ブランチ | 役割 | 編集 |
|---|---|---|---|
| `C:/Users/skych/OneDrive/デスクトップ/MUEStrategyApp` | `main` | このリポジトリ。アプリ本体 | する |
| `C:/Users/skych/OneDrive/デスクトップ/MUEStrategy-mobile` | `feature/mobile-app` | MUEStrategy の作業用worktree。アプリのための変更はここで行う | する |
| `C:/Users/skych/OneDrive/デスクトップ/MUEStrategy` | `master` | **本番**。このPCのランナー（自動売買を含む）はここから起動している | **しない** |

```
git worktree list（MUEStrategy 側, 2026-10-24 時点）
C:/Users/skych/OneDrive/デスクトップ/MUEStrategy          4c8ed81 [master]
C:/Users/skych/OneDrive/デスクトップ/MUEStrategy-mobile   4c8ed81 [feature/mobile-app]
```

### 守ること

- **本番の `MUEStrategy`（master）のフォルダは読むだけにする。** 編集・ブランチ切り替え・
  stash・reset などをしない。ランナーは毎朝このフォルダのコードで起動するので、
  作業途中の変更があると未完成のコードで自動売買が動く。
- MUEStrategy 側の変更は必ず `MUEStrategy-mobile`（`feature/mobile-app`）で行う。
  master へのマージはユーザーが確認してから行う。Claude から master にマージ・pushしない。
- MUEStrategy 側を参照するときも、原則 `MUEStrategy-mobile` を見る（両者の差分が作業内容）。
  本番の稼働状態（ログ・`strategies/runner/state/` など、gitの管理外のファイル）を
  確認したいときだけ本番フォルダを読む。
- このリポジトリの `main` は最初のコミット（雛形・CLAUDE.md）まで直接コミットしてよい。
  それ以降の機能は `feature/〜` ブランチで進める。MUEStrategy 側と対応する作業は
  ブランチ名を揃える（例: 両方 `feature/mobile-app`）。
- MUEStrategy 側で作業するときは、その中の `CLAUDE.md` にも従う（API仕様への準拠、
  発注・資金管理まわりは必ず確認を取る、など）。

## kabuステーションAPIとの関係（最重要）

**アプリから kabuステーションAPI を直接呼ばない。** トークンを発行しない。

公式リファレンス POST /token の説明どおり、新しいトークンが発行されると既存のトークンは
無効になる。アプリ（や中継サーバ）が独自に認証すると、稼働中のランナーのトークンが失効し、
参照系だけでなく**自動売買の発注・取消まで失敗する**。
（詳細: MUEStrategy の `strategies/runner/src/account_snapshot.py` の冒頭）

アプリに見せるデータは、ランナーが書き出すファイルを中継する形にする。
コントロールパネル（`ui/control_panel.pyw`）と同じ方式。

| データ | 出どころ（MUEStrategy 内） |
|---|---|
| 資産残高・買付余力・建玉・評価損益・当日の注文 | `strategies/runner/state/account.json`（ランナーが15秒ごとに書き出す。停止中は無い。`orders` は feature/mobile-app で追加） |
| 仮想売買のエントリー/決済 | `strategies/runner/state/events/events_YYYY-MM-DD.jsonl`（feature/mobile-app で追加。master にマージされるまで本番には無い） |
| 仮想売買・シグナルのログ | `strategies/runner/logs/runner_YYYY-MM-DD.log` |
| 週次の集計 | `analysis/output/<日付>/metrics_<日付>.md`・`trades_<日付>.csv` |
| ストラテジーのON/OFF | `strategies/runner/config.yaml` |

ランナーに新しい書き出しを足す必要があれば、`MUEStrategy-mobile` で実装する。

## 確認を取ってから進めること

- アプリからの**発注・注文取消・自動売買のON/OFFや設定変更**など、売買に影響する操作を
  実装するかどうか。実装する場合の確認手順・認証も含めて、必ず事前にユーザーに確認する。
  （閲覧専用で作っている。書き込み操作は意図しない発注や損失につながる）
- PCの外（インターネット）にデータを出す方法。口座残高・建玉は個人の資産情報なので、
  公開・中継の方式はユーザーが決める。
- 技術スタック、外部サービスの利用、新しい依存パッケージの追加。

## 環境の注意

- Windows 11。稼働PCはメモリが少なく、重い処理でページングファイル不足のエラーが出ることがある。
  ランナーの稼働中（平日 7時台〜15時半）に重いビルドや常駐プロセスを走らせない。
- kabuステーションは自動ログインできない（ユーザーが手動で起動・ログインする）。
