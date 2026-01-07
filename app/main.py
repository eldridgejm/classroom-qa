"""
Main FastAPI application for In-Class Q&A + Polling Tool
"""

from fastapi import FastAPI

from app.routes import admin, sse, student

app = FastAPI(
    title="Classroom Q&A",
    description="In-Class Q&A + Polling Tool for UCSD Data Science Lectures",
    version="0.1.0",
)

# Include routers
app.include_router(admin.router, tags=["admin"])
app.include_router(student.router, tags=["student"])
app.include_router(sse.router, tags=["sse"])


@app.get("/")
async def root() -> dict[str, str]:
    """Health check endpoint"""
    return {"status": "ok", "message": "Classroom Q&A API"}


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint for monitoring"""
    return {"status": "healthy"}
