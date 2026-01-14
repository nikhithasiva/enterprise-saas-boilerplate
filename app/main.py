from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import structlog

from app.core.config import settings
from app.core.database import init_db, close_db
from app.core.middleware import RateLimitMiddleware, SecurityHeadersMiddleware
from app.api.routes import auth, users

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

app = FastAPI(
    title="Enterprise SaaS Boilerplate",
    description="A production-ready SaaS foundation with multi-tenancy, billing, and enterprise security",
    version="1.0.0",
    docs_url="/docs" if settings.ENVIRONMENT == "development" else None,
    redoc_url="/redoc" if settings.ENVIRONMENT == "development" else None,
)

# Security middleware
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware, calls=100, period=60)  # 100 requests per minute

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_HOSTS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routers
app.include_router(auth.router, prefix="/api/auth", tags=["Authentication"])
app.include_router(users.router, prefix="/api/users", tags=["Users"])

@app.on_event("startup")
async def startup_event():
    logger.info("Starting Enterprise SaaS Boilerplate", environment=settings.ENVIRONMENT)
    # Initialize database tables
    try:
        await init_db()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error("Failed to initialize database", error=str(e))

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down Enterprise SaaS Boilerplate")
    await close_db()

@app.get("/")
async def root():
    return {
        "message": "Enterprise SaaS Boilerplate API",
        "version": "1.0.0",
        "status": "operational",
        "environment": settings.ENVIRONMENT
    }

@app.get("/health")
async def health_check():
    from sqlalchemy import text
    from app.core.database import engine
    
    # Check database connection
    db_status = "healthy"
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as e:
        db_status = "unhealthy"
        logger.error("Database health check failed", error=str(e))
    
    status_code = 200 if db_status == "healthy" else 503
    
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "healthy" if db_status == "healthy" else "degraded",
            "database": db_status,
            "environment": settings.ENVIRONMENT,
            "version": "1.0.0"
        }
    )