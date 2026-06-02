"""Run the brnrd backend: ``python -m brnrd``."""

from __future__ import annotations

import os

import uvicorn

from .app import create_app


def main() -> None:
    app = create_app()
    uvicorn.run(
        app,
        host=os.environ.get("BRNRD_HOST", "127.0.0.1"),
        port=int(os.environ.get("BRNRD_PORT", "8000")),
    )


if __name__ == "__main__":
    main()
