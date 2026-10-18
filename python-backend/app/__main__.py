"""Startknappen: `uv run python -m app` starter serveren.

Python kører automatisk denne fil, når man skriver `python -m app`.
Selve appen ligger i main.py.
"""

import uvicorn

from app.core.config import load_settings


def main() -> None:
    """Starter serveren på den adresse og port der står i .env (standard 127.0.0.1:3000)."""
    settings = load_settings()
    if settings.host == "127.0.0.1":
        print(f"Stamgæst kører på http://localhost:{settings.port}")
    else:
        print(f"Stamgæst lytter på {settings.host}:{settings.port} ({settings.app_base_url})")
    # Som standard lytter serveren kun på 127.0.0.1 ("kun denne maskine"). Hos en host sættes
    # HOST=0.0.0.0, så hostens proxy kan nå den. Det kræver HTTPS foran (se config.py).
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level="info",
        # Kundens rigtige IP læses i appen selv (ProxyHeadersMiddleware i main.py), kun fra de
        # proxyer der står i TRUSTED_PROXIES. Uvicorn skal ikke også gøre det med sine egne regler.
        proxy_headers=False,
        # Fortæl ikke omverdenen hvilken server vi kører. Det hjælper kun en angriber.
        server_header=False,
    )


if __name__ == "__main__":
    main()
