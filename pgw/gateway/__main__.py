"""`python -m pgw.gateway` runs the gateway under uvicorn."""
import os

import uvicorn

from .app import create_app
from .settings import Settings


def main() -> None:
    app = create_app(Settings.from_env())
    uvicorn.run(app, host=os.environ.get("PGW_HOST", "127.0.0.1"), port=int(os.environ.get("PGW_PORT", "8080")),
                log_level="warning")


if __name__ == "__main__":
    main()
