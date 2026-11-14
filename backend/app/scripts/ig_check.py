"""Cek konfigurasi Instagram (token Meta & ID akun bisnis) tanpa membaca postingan dengan AI.

    python -m app.scripts.ig_check                 # cek token & akun bisnis sendiri
    python -m app.scripts.ig_check namaakunloker   # + cek akun info loker bisa dibaca
"""

import asyncio
import sys
from datetime import UTC, datetime, timedelta

from app.connectors.base import new_http_client
from app.connectors.instagram import GraphError, InstagramConnector, _ts
from app.core.config import get_settings


async def main(usernames: list[str]) -> None:
    s = get_settings()
    if not (s.meta_access_token and s.instagram_business_id):
        sys.exit("META_ACCESS_TOKEN / INSTAGRAM_BUSINESS_ID belum diisi di .env")
    async with new_http_client(30) as client:
        r = await client.get(f"https://graph.facebook.com/{s.meta_graph_version}/{s.instagram_business_id}",
                             params={"fields": "username,name", "access_token": s.meta_access_token})
        data = r.json()
        if "error" in data:
            sys.exit(f"Token/ID ditolak: {data['error'].get('message')}")
        print(f"OK: akun bisnis @{data.get('username')} ({data.get('name')})")
        conn = InstagramConnector()
        since = datetime.now(UTC) - timedelta(days=s.instagram_max_age_days)
        for u in usernames:
            try:
                media = await conn._recent_media(client, u.strip("@").lower())
            except GraphError as exc:
                print(f"@{u}: GAGAL — {exc}")
                continue
            fresh = [m for m in media if (ts := _ts(m.get("timestamp"))) and ts >= since]
            print(f"@{u}: {len(media)} postingan terbaca, {len(fresh)} dalam {s.instagram_max_age_days} hari terakhir")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
