from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

from app.database import engine, Base
from app.routers import auth, catalog, dashboard, shops

app = FastAPI(
    title="Marketplace Analytics API",
    description="Multi-marketplace analytics dashboard backend",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(catalog.router, prefix="/api/v1/catalog", tags=["catalog"])
app.include_router(shops.router, prefix="/api/v1/shops", tags=["shops"])
app.include_router(dashboard.router, prefix="/api/v1/dashboard", tags=["dashboard"])

# Serve frontend static files
static_path = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_path):
    app.mount("/static", StaticFiles(directory=static_path), name="static")

@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

@app.get("/health")
async def health_check():
    return {"status": "ok", "version": "0.1.0"}

@app.get("/")
async def root():
    """Serve the dashboard HTML."""
    possible_paths = [
        os.path.join(os.path.dirname(__file__), "static", "dashboard.html"),
        os.path.join(os.path.dirname(__file__), "..", "..", "dashboard.html"),
        "/app/dashboard.html",
        "/dashboard.html",
    ]
    for path in possible_paths:
        if os.path.exists(path):
            return FileResponse(path)
    return {"message": "API is running. Dashboard not found. Check /health"}