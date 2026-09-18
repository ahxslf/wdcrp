"""
Tiny HTTP health server for hosts that require an open port (Render, Railway,
Heroku-style dynos, Docker health checks...).

Behavior:
  * If the PORT env var is set (Render injects it automatically), the bot
    serves HTTP on 0.0.0.0:PORT:
        GET /        -> plain text status line
        GET /health  -> JSON status (bot online, guild count, user)
  * If PORT is NOT set (normal local run), the server is skipped silently.

Runs inside the bot's own asyncio loop — aiohttp is already a dependency of
discord.py, so no extra packages are needed.
"""

from __future__ import annotations

import os

from aiohttp import web


async def start_health_server(bot, bot_name: str) -> bool:
    raw = (os.environ.get("PORT") or "").strip()
    if not raw.isdigit():
        print("[DCRP] No PORT env var — health server skipped (local run)")
        return False
    port = int(raw)

    async def health(_request: web.Request) -> web.Response:
        try:
            ready = bot.is_ready()
        except Exception:  # noqa: BLE001
            ready = False
        return web.json_response({
            "status": "ok",
            "service": bot_name,
            "bot_online": bool(ready),
            "user": str(bot.user) if bot.user else "starting",
            "guilds": len(getattr(bot, "guilds", []) or []),
        })

    async def root(_request: web.Request) -> web.Response:
        return web.Response(text=f"{bot_name} is running. Use /health for status.\n")

    app = web.Application()
    app.router.add_get("/", root)
    app.router.add_get("/health", health)
    app.router.add_get("/healthz", health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"[DCRP] Health server listening on 0.0.0.0:{port} "
          "(GET / and /health)")
    return True
