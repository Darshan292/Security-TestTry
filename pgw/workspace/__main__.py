"""`python -m pgw.workspace` runs the HR Workspace under uvicorn. Settings come from WS_* variables
(docs/WORKSPACE.md); a missing or unsafe production setting stops startup with a message naming it."""
from __future__ import annotations

import logging
import sys

import uvicorn

from .app import create_app
from .settings import SettingsError, WorkspaceSettings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        s = WorkspaceSettings.from_env()
    except SettingsError as e:
        sys.exit(f"hr-workspace: {e}")
    # proxy_headers=False: the trusted-proxy check needs the real peer address, never X-Forwarded-For.
    uvicorn.run(create_app(s), host=s.host, port=s.port, proxy_headers=False, server_header=False,
                log_level="info")


if __name__ == "__main__":
    main()
