#!/usr/bin/env python3
"""Re-encrypt all stored Meta tokens with the primary configured key."""

import asyncio
import logging
import sys

from core.meta_tokens import rotate_stored_meta_tokens
from database.db import async_session_maker


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("rotate_meta_tokens")


async def main() -> int:
    logger.info("Starting Meta token rotation")
    try:
        async with async_session_maker() as session:
            stats = await rotate_stored_meta_tokens(session)
        logger.info(
            "Token rotation complete: %d connection(s), %d account(s)",
            stats["connections_rotated"],
            stats["accounts_rotated"],
        )
        return 0
    except Exception as exc:
        logger.error("Token rotation failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
