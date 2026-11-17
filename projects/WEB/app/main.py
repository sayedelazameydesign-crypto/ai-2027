"""
FastAPI Main Application
Multi-Provider Web Development Agent

© 2026 SAYED_ELAZAMEY - All Rights Reserved
PROPRIETARY SOFTWARE - UNAUTHORIZED USE PROHIBITED

Developer: SAYED_ELAZAMEY
Email: sayedelazameydesign@gmail.com
Phone: 01126212452

This software is protected by copyright law. Unauthorized reproduction,
distribution, or modification is strictly prohibited and may result in
severe civil and criminal penalties.
"""

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from contextlib import asynccontextmanager
import time
from pathlib import Path

from .core.config import config
from .models.schemas import (
    CodeRequest, CodeResponse, ErrorAnalysis, ErrorResponse,
    CodeReviewRequest, CodeReviewResponse,
    OptimizationRequest, OptimizationResponse,
    SystemStatus, ProviderSwitchRequest, ProviderSwitchResponse
)

# Will be imported when other files are created
# from .agent.unified_agent import UnifiedWebDevAgent

# Lifespan for startup/shutdown
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("🚀 Starting Web Dev Agent...")
    print(f"📦 Available providers: {len(config.get_available_providers())}")
    yield
    # Shutdown
    print("👋 Shutting down Web Dev Agent...")

# Create FastAPI app
app = FastAPI(
    title="Web Dev Agent API",
    description="Multi-Provider AI Development Assistant",
    version="2.0.0",
    lifespan=lifespan
)

# CORS Configuration
if config.settings.cors_enabled:
    origins = config.settings.allowed_origins.split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Request tracking
start_time = time.time()
requests_count = 0

@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    global requests_count
    requests_count += 1
    
    start = time.time()
    response = await call_next(request)
    process_time = time.time() - start
    
    response.headers["X-Process-Time"] = str(process_time)
    return response

# Exception handlers
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "message": str(exc),
            "path": str(request.url)
        }
    )

# Root endpoint
@app.get("/")
async def root():
    """API Information"""
    return {
        "name": config.settings.app_name,
        "version": config.settings.app_version,
        "status": "operational",
        "copyright": "© 2026 SAYED_ELAZAMEY - All Rights Reserved",
        "license": "PROPRIETARY - UNAUTHORIZED USE PROHIBITED",
        "developer": {
            "name": "SAYED_ELAZAMEY",
            "email": "sayedelazameydesign@gmail.com",
            "phone": "01126212452"
        },
        "providers_available": len(config.get_available_providers()),
        "primary_provider": config.settings.primary_provider,
        "endpoints": {
            "generate": "/api/generate",
            "analyze_error": "/api/analyze-error",
            "code_review": "/api/code-review",
            "optimize": "/api/optimize",
            "providers": "/providers",
            "health": "/health"
        }
    }

# Health check
@app.get("/health")
async def health_check():
    """System health check"""
    return SystemStatus(
        status="healthy",
        version=config.settings.app_version,
        uptime=time.time() - start_time,
        providers_available=len(config.get_available_providers()),
        current_provider=config.settings.primary_provider,
        cache_enabled=config.settings.cache_enabled,
        requests_processed=requests_count,
        average_response_time=0.0  # Calculate from metrics
    )

# Provider management
@app.get("/providers")
async def list_providers():
    """List all available providers"""
    providers = {}
    for provider_type in config.get_available_providers():
        provider_config = config.get_provider_config(provider_type)
        providers[provider_type.value] = {
            "name": provider_type.value,
            "model": provider_config.model_name,
            "available": True
        }
    return {
        "providers": providers,
        "primary": config.settings.primary_provider,
        "fallbacks": config.settings.fallback_providers.split(",")
    }

@app.post("/providers/switch", response_model=ProviderSwitchResponse)
async def switch_provider(request: ProviderSwitchRequest):
    """Switch to a different provider"""
    # This would be implemented when agent is created
    return ProviderSwitchResponse(
        success=True,
        message=f"Switched to {request.provider}",
        new_provider=request.provider,
        new_model=request.model or "default"
    )

# Code generation endpoint
@app.post("/api/generate", response_model=CodeResponse)
async def generate_code(request: CodeRequest):
    """
    Generate code based on requirements
    
    Supports all major programming languages and frameworks
    """
    try:
        # This would use the unified agent when implemented
        return CodeResponse(
            code="# Generated code will appear here",
            language=request.language,
            security_report={
                "score": 100,
                "critical_issues": [],
                "warnings": [],
                "info": [],
                "passed": True
            },
            performance_metrics={
                "size_kb": 1.0,
                "lines": 10,
                "complexity": 1,
                "functions_count": 1,
                "optimization_suggestions": []
            },
            suggestions=[],
            provider_used="anthropic",
            generation_time=1.5,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Error analysis endpoint
@app.post("/api/analyze-error", response_model=ErrorResponse)
async def analyze_error(request: ErrorAnalysis):
    """Analyze and fix code errors"""
    try:
        # Implementation would use unified agent
        return ErrorResponse(
            analysis="Error analysis will appear here",
            root_cause="Root cause explanation",
            fixes=[],
            prevention_tips=[],
            provider_used="anthropic"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Code review endpoint
@app.post("/api/code-review", response_model=CodeReviewResponse)
async def review_code(request: CodeReviewRequest):
    """Comprehensive code review"""
    try:
        return CodeReviewResponse(
            overall_score=85,
            issues=[],
            strengths=[],
            recommendations=[]
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Optimization endpoint
@app.post("/api/optimize", response_model=OptimizationResponse)
async def optimize_code(request: OptimizationRequest):
    """Optimize code for performance"""
    try:
        return OptimizationResponse(
            original_metrics={
                "size_kb": 10.0,
                "lines": 100,
                "complexity": 15,
                "functions_count": 5,
                "optimization_suggestions": []
            },
            optimized_code="# Optimized code",
            optimized_metrics={
                "size_kb": 8.0,
                "lines": 80,
                "complexity": 10,
                "functions_count": 5,
                "optimization_suggestions": []
            },
            improvements=["Reduced size by 20%"],
            trade_offs=[]
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Preview endpoint
@app.get("/preview/{preview_id}")
async def get_preview(preview_id: str):
    """Get code preview"""
    preview_path = Path(config.settings.preview_dir) / f"{preview_id}.html"
    
    if not preview_path.exists():
        raise HTTPException(status_code=404, detail="Preview not found")
    
    return FileResponse(preview_path, media_type="text/html")

# Metrics endpoint (for Prometheus)
@app.get("/metrics")
async def metrics():
    """Prometheus metrics"""
    return {
        "requests_total": requests_count,
        "uptime_seconds": time.time() - start_time,
        "providers_available": len(config.get_available_providers())
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=config.settings.host,
        port=config.settings.port,
        reload=config.settings.debug
    )
