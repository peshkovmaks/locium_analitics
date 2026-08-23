from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

from app.database import Base, get_engine
from app.routers import auth, dashboard, shops, products, balances

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

app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(shops.router, prefix="/api/v1/shops", tags=["shops"])
app.include_router(balances.router, prefix="/api/v1/balances", tags=["balances"])
app.include_router(dashboard.router, prefix="/api/v1/dashboard", tags=["dashboard"])
app.include_router(products.router, prefix="/api/v1", tags=["products"])


@app.on_event("startup")
async def startup():
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@app.get("/health")
async def health_check():
    return {"status": "ok", "version": "0.1.0"}


# Serve frontend static files
static_path = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_path):
    app.mount("/static", StaticFiles(directory=static_path), name="static")


@app.get("/")
async def root():
    index_path = os.path.join(static_path, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "API is running. Frontend not built yet. Check /health"}


@app.get("/{full_path:path}")
async def catch_all(full_path: str):
    """Serve React Router routes."""
    index_path = os.path.join(static_path, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "API is running. Frontend not built yet."}
