import asyncio

import uvicorn

from api.server import app
from core.config import settings
from core.runtime import configure_logging


async def main() -> None:
    logger = configure_logging("api")
    logger.info("Starting Buyerly API on %s:%s", settings.API_HOST, settings.API_PORT)
    server = uvicorn.Server(
        uvicorn.Config(
            app=app,
            host=settings.API_HOST,
            port=settings.API_PORT,
            log_level="warning",
            loop="asyncio",
        )
    )
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
