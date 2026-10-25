"""中継サーバの設定。リポジトリ直下の config.yaml（gitignore対象）から読む。

雛形は config.example.yaml。無ければ既定値で動く。
"""
import os
from dataclasses import dataclass, field

import yaml

APP_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
CONFIG_PATH = os.path.join(APP_ROOT, "config.yaml")


@dataclass
class Settings:
    # 本番の MUEStrategy フォルダ。**読むだけ**で、何も書き込まない
    mue_root: str = os.path.normpath(os.path.join(APP_ROOT, "..", "MUEStrategy"))
    db_path: str = os.path.join(APP_ROOT, "data", "app.db")
    # 待ち受けは自分のPCの中だけ。外からは tailscale serve が中継する
    host: str = "127.0.0.1"
    port: int = 8765
    # 閲覧を許す Tailscale のログイン名（例: you@example.com）。
    # tailscale serve が付ける Tailscale-User-Login ヘッダーと照合する。空だと誰も見られない
    allowed_logins: list = field(default_factory=list)
    # ログ・イベントを取り込み直す間隔（秒）
    ingest_interval_seconds: int = 60
    # account.json がこの秒数より古ければ「ランナー停止中」とみなす
    # （ランナーは15秒ごとに書き、終了時に消す）
    account_stale_seconds: int = 90

    @property
    def runner_dir(self) -> str:
        return os.path.join(self.mue_root, "strategies", "runner")

    @property
    def logs_dir(self) -> str:
        return os.path.join(self.runner_dir, "logs")

    @property
    def events_dir(self) -> str:
        return os.path.join(self.runner_dir, "state", "events")

    @property
    def account_path(self) -> str:
        return os.path.join(self.runner_dir, "state", "account.json")

    @property
    def runner_config_path(self) -> str:
        return os.path.join(self.runner_dir, "config.yaml")

    @property
    def autotrade_config_path(self) -> str:
        return os.path.join(self.mue_root, "strategies", "autotrade", "config.yaml")

    @property
    def analysis_output_dir(self) -> str:
        return os.path.join(self.mue_root, "analysis", "output")


def load(path: str = CONFIG_PATH) -> Settings:
    s = Settings()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for k, v in data.items():
            if not hasattr(s, k):
                raise ValueError(f"config.yaml: 知らない設定です: {k}")
            setattr(s, k, v)
    s.allowed_logins = [str(x).strip().lower() for x in (s.allowed_logins or []) if str(x).strip()]
    return s
