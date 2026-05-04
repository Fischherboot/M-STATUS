"""Run M-STATUS via `python -m m_status`."""
from __future__ import annotations

import uvicorn

from . import config


def main() -> None:
    uvicorn.run(
        "m_status.main:app",
        host=config.HOST,
        port=config.PORT,
        access_log=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
