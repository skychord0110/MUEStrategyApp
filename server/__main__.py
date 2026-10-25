"""起動: python -m server"""
import ipaddress
import logging
import sys

import uvicorn

from . import settings as settings_mod
from .app import create_app


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    s = settings_mod.load()
    # 外向きのアドレスで待ち受けると Tailscale を通らずに見えてしまうので拒否する
    host = "127.0.0.1" if s.host == "localhost" else s.host
    if not ipaddress.ip_address(host).is_loopback:
        sys.exit(f"host はループバック（127.0.0.1）にしてください: {s.host}")
    if not s.allowed_logins:
        logging.warning("config.yaml の allowed_logins が空です。このままでは誰も閲覧できません")
    uvicorn.run(create_app(s), host=host, port=s.port, log_level="info",
                proxy_headers=False, server_header=False)


if __name__ == "__main__":
    main()
