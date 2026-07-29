from __future__ import annotations

import uvicorn

from app.config.models import Settings


def main() -> None:
    settings = Settings()
    uvicorn.run(
        "app.main:app",
        host=settings.bind_host,
        port=settings.bind_port,
    )


if __name__ == "__main__":
    main()
