"""
Network diagnostic — run this when the bot can't connect.
It tests exactly the two things the bot needs:
  1) HTTPS reachability of the Discord gateway
  2) The raw WebSocket handshake (wss://gateway.discord.gg)

Run:  python netcheck.py
"""

import asyncio

import aiohttp

GATEWAY_HTTP = "https://gateway.discord.gg/"
GATEWAY_WS = "wss://gateway.discord.gg/?v=10&encoding=json"

TIMEOUT = aiohttp.ClientTimeout(total=20)


async def test_https() -> None:
    print("\n1) HTTPS GET https://gateway.discord.gg/ ...")
    async with aiohttp.ClientSession() as s:
        try:
            async with s.get(GATEWAY_HTTP, timeout=TIMEOUT) as r:
                print(f"   -> status {r.status} | {await r.text()}")
        except Exception as exc:  # noqa: BLE001
            print(f"   -> FAILED: {exc!r}")


async def test_ws() -> None:
    print("\n2) WebSocket handshake wss://gateway.discord.gg ...")
    async with aiohttp.ClientSession() as s:
        try:
            async with s.ws_connect(GATEWAY_WS, timeout=20) as ws:
                print("   -> handshake OK, waiting for HELLO ...")
                msg = await ws.receive(timeout=20)
                print(f"   -> got gateway HELLO (type={msg.type})")
                if msg.data:
                    print(f"   -> data: {str(msg.data)[:160]}")
                print("   => network path to Discord gateway is HEALTHY")
        except Exception as exc:  # noqa: BLE001
            print(f"   -> FAILED: {exc!r}")
            print("   => the gateway path is being blocked/broken")
            print("      (Cloudflare rate-limit on your IP, ISP, firewall,")
            print("      antivirus TLS-inspection, or a Discord incident)")


async def main() -> None:
    await test_https()
    await test_ws()
    print("\nDone. Paste this output if you need help.\n")


if __name__ == "__main__":
    asyncio.run(main())
