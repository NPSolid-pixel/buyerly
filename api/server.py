import os
import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from api.routes import router as api_router

logger = logging.getLogger(__name__)

def create_app() -> FastAPI:
    app = FastAPI(
        title="Buyerly Web App & API",
        version="1.0.0",
        description="FastAPI Backend & Telegram Mini App for Buyerly AI Media Buyer"
    )

    # Disable caching for static assets in Telegram WebViews
    @app.middleware("http")
    async def add_no_cache_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/") or request.url.path == "/":
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    # CORS support
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API routes
    app.include_router(api_router)

    # Static Web App files
    webapp_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "webapp")
    os.makedirs(webapp_dir, exist_ok=True)

    if os.path.exists(webapp_dir):
        app.mount("/static", StaticFiles(directory=webapp_dir), name="static")

        @app.get("/")
        async def serve_index():
            index_path = os.path.join(webapp_dir, "index.html")
            if os.path.exists(index_path):
                return FileResponse(index_path, headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0"
                })
            return {"status": "Buyerly API is running", "webapp": "index.html not found"}

    return app

app = create_app()

