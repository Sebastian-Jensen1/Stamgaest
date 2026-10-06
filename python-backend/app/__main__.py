"""Startknappen: `uv run python -m app` starter serveren.

Python kører automatisk denne fil, når man skriver `python -m app`.
Selve appen ligger i main.py.
"""

import uvicorn

from app.core.config import load_settings


def main() -> None:
    """Starter serveren på den port der står i .env (standard 3000)."""
    settings = load_settings()
    print(f"Stamgæst kører på http://localhost:{settings.port}")
    # 127.0.0.1 betyder "kun denne maskine". Der er intet login, så serveren må ikke
    # kunne nås fra andre computere på netværket.
    uvicorn.run("app.main:app", host="127.0.0.1", port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
