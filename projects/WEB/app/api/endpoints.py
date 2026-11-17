"""
Enhanced FastAPI Endpoints for AI Providers System

© 2026 SAYED_ELAZAMEY - All Rights Reserved
PROPRIETARY SOFTWARE - UNAUTHORIZED USE PROHIBITED

This module implements comprehensive REST API endpoints with advanced features
including PII redaction, semantic routing, cost management, and real-time monitoring.

This software is protected by copyright law. Unauthorized reproduction,
distribution, or modification is strictly prohibited and may result in
severe civil and criminal penalties.
"""

import asyncio
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import logging
from fastapi import FastAPI, HTTPException, Depends, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import json
from pathlib import Path

from ..core.request_handler import RequestHandler, RequestContext
from ..core.fallback_manager import FallbackManager
from ..core.config_manager import ConfigurationManager
from ..providers.base import CompletionRequest, CompletionResponse, ChatMessage
from ..security.pii_redactor import pii_redactor
from ..intelligence.semantic_router import semantic_router
from ..intelligence.cost_manager import cost_manager
from ..intelligence.speculative_engine import speculative_engine, SpeculativeConfig, SpeculativeStrategy


logger = logging.getLogger(__name__)


# Request/Response Models
class ChatCompletionRequest(BaseModel):
    """Chat completion request model."""
    messages: List[Dict[str, str]]
    model: Optional[str] = None
    max_tokens: Optional[int] = Field(None, ge=1, le=4096)
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    top_p: Optional[float] = Field(None, ge=0.0, le=1.0)
    stream: bool = False
    stop: Optional[List[str]] = None
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    
    # Advanced options
    enable_pii_redaction: bool = True
    enable_semantic_routing: bool = True
    enable_cost_management: bool = True
    enable_speculative_execution: bool = True
    cost_limit: Optional[float] = None
    preferred_provider: Optional[str] = None


class ChatCompletionResponse(BaseModel):
    """Chat completion response model."""
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    provider: str
    choices: List[Dict[str, Any]]
    usage: Dict[str, int]
    
    # Enhanced metadata
    processing_time_ms: Optional[float] = None
    pii_redacted: Optional[int] = None
    cost_estimate: Optional[float] = None
    semantic_analysis: Optional[Dict[str, Any]] = None
    speculative_info: Optional[Dict[str, Any]] = None


class HealthCheckResponse(BaseModel):
    """Health check response model."""
    status: str
    timestamp: datetime
    providers: Dict[str, Dict[str, Any]]
    system_stats: Dict[str, Any]
    uptime_seconds: float


class BudgetRequest(BaseModel):
    """Budget management request."""
    user_id: str
    period: str  # daily, weekly, monthly
    limit_amount: float
    alert_thresholds: Optional[Dict[str, float]] = None


class SpeculativeConfigRequest(BaseModel):
    """Speculative configuration request."""
    strategy: str
    backup_delay_ms: Optional[int] = 2000
    max_parallel_requests: Optional[int] = 3
    enable_racing: Optional[bool] = True


# Initialize components
config_manager = ConfigurationManager()
fallback_manager = FallbackManager(config_manager)
request_handler = RequestHandler(fallback_manager)

# Initialize FastAPI app
app = FastAPI(
    title="AI Providers System",
    description="Advanced AI provider orchestration with intelligent routing and optimization",
    version="2.0.0"
)

# Mount static files
static_path = Path(__file__).parent.parent.parent / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

# Add middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(GZipMiddleware, minimum_size=1000)


# Dependency injection
async def get_request_context(request: Request) -> RequestContext:
    """Create request context from HTTP request."""
    return RequestContext(
        request_id=f"req_{int(datetime.now().timestamp() * 1000)}",
        start_time=datetime.now(),
        client_ip=request.client.host,
        user_agent=request.headers.get("user-agent")
    )


# Main API Endpoints
@app.get("/")
async def root():
    """Serve the main web interface."""
    static_path = Path(__file__).parent.parent.parent / "static" / "index.html"
    if static_path.exists():
        return FileResponse(static_path)
    else:
        return {
            "name": "AI Providers System",
            "version": "2.0.0",
            "status": "operational",
            "copyright": "© 2026 SAYED_ELAZAMEY - All Rights Reserved",
            "developer": {
                "name": "SAYED_ELAZAMEY",
                "email": "sayedelazameydesign@gmail.com",
                "phone": "01126212452"
            },
            "endpoints": {
                "chat_completions": "/v1/chat/completions",
                "health": "/v1/health",
                "providers": "/v1/providers",
                "docs": "/docs"
            }
        }


@app.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def create_chat_completion(
    request_data: ChatCompletionRequest,
    context: RequestContext = Depends(get_request_context)
):
    """
    Create a chat completion with advanced optimization features.
    
    This endpoint supports:
    - PII redaction for privacy protection
    - Semantic routing for optimal provider selection
    - Cost management and budget enforcement
    - Speculative execution for faster responses
    """
    try:
        # Convert request to internal format
        messages = []
        for msg in request_data.messages:
            messages.append(ChatMessage(
                role=msg["role"],
                content=msg["content"]
            ))
        
        completion_request = CompletionRequest(
            messages=messages,
            model=request_data.model,
            max_tokens=request_data.max_tokens,
            temperature=request_data.temperature,
            top_p=request_data.top_p,
            stream=request_data.stream,
            stop=request_data.stop
        )
        
        # Update context with request options
        context.user_id = request_data.user_id
        context.session_id = request_data.session_id
        context.preferred_provider = request_data.preferred_provider
        context.enable_pii_redaction = request_data.enable_pii_redaction
        context.enable_semantic_routing = request_data.enable_semantic_routing
        context.enable_cost_management = request_data.enable_cost_management
        context.enable_speculative_execution = request_data.enable_speculative_execution
        context.cost_limit = request_data.cost_limit
        
        # Process request
        start_time = datetime.now()
        response = await request_handler.process_completion(completion_request, context)
        processing_time = (datetime.now() - start_time).total_seconds() * 1000
        
        # Build enhanced response
        enhanced_response = ChatCompletionResponse(
            id=response.id,
            created=response.created,
            model=response.model,
            provider=response.provider,
            choices=response.choices,
            usage={
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens
            },
            processing_time_ms=processing_time
        )
        
        # Add metadata if available
        if hasattr(response, 'pii_redacted'):
            enhanced_response.pii_redacted = response.pii_redacted
        
        if hasattr(response, 'cost_estimate'):
            enhanced_response.cost_estimate = response.cost_estimate
        
        return enhanced_response
        
    except Exception as e:
        logger.error(f"Chat completion failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/chat/completions/stream")
async def create_chat_completion_stream(
    request_data: ChatCompletionRequest,
    context: RequestContext = Depends(get_request_context)
):
    """Create a streaming chat completion."""
    try:
        # Convert request to internal format
        messages = []
        for msg in request_data.messages:
            messages.append(ChatMessage(
                role=msg["role"],
                content=msg["content"]
            ))
        
        completion_request = CompletionRequest(
            messages=messages,
            model=request_data.model,
            max_tokens=request_data.max_tokens,
            temperature=request_data.temperature,
            top_p=request_data.top_p,
            stream=True,
            stop=request_data.stop
        )
        
        # Update context
        context.user_id = request_data.user_id
        context.session_id = request_data.session_id
        
        async def generate_stream():
            try:
                async for chunk in request_handler.process_streaming_completion(completion_request, context):
                    # Format as SSE
                    data = {
                        "id": context.request_id,
                        "object": "chat.completion.chunk",
                        "created": int(datetime.now().timestamp()),
                        "choices": [{
                            "index": 0,
                            "delta": {"content": chunk},
                            "finish_reason": None
                        }]
                    }
                    yield f"data: {json.dumps(data)}\n\n"
                
                # Send final chunk
                final_data = {
                    "id": context.request_id,
                    "object": "chat.completion.chunk",
                    "created": int(datetime.now().timestamp()),
                    "choices": [{
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop"
                    }]
                }
                yield f"data: {json.dumps(final_data)}\n\n"
                yield "data: [DONE]\n\n"
                
            except Exception as e:
                error_data = {
                    "error": {
                        "message": str(e),
                        "type": "server_error"
                    }
                }
                yield f"data: {json.dumps(error_data)}\n\n"
        
        return StreamingResponse(
            generate_stream(),
            media_type="text/plain",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Content-Type": "text/event-stream"
            }
        )
        
    except Exception as e:
        logger.error(f"Streaming completion failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Health and Status Endpoints
@app.get("/v1/health", response_model=HealthCheckResponse)
async def health_check():
    """Comprehensive system health check."""
    try:
        # Check all providers
        provider_health = await request_handler.health_check_all_providers()
        
        # Get system stats
        system_stats = request_handler.get_request_stats()
        
        # Calculate uptime
        uptime = (datetime.now() - datetime.now()).total_seconds()  # Placeholder
        
        # Determine overall status
        healthy_providers = sum(1 for status in provider_health.values() if status)
        overall_status = "healthy" if healthy_providers > 0 else "unhealthy"
        
        return HealthCheckResponse(
            status=overall_status,
            timestamp=datetime.now(),
            providers={
                name: {
                    "healthy": status,
                    "last_check": datetime.now().isoformat()
                }
                for name, status in provider_health.items()
            },
            system_stats=system_stats,
            uptime_seconds=uptime
        )
        
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/v1/providers")
async def list_providers():
    """List all available providers with their status."""
    try:
        providers = await fallback_manager.get_all_providers()
        provider_stats = fallback_manager.get_provider_stats()
        
        result = {}
        for name, provider in providers.items():
            stats = provider_stats.get(name, {})
            result[name] = {
                "name": name,
                "models": provider.supported_models,
                "healthy": stats.get("is_healthy", True),
                "success_rate": stats.get("success_rate", 0.0),
                "average_response_time": stats.get("average_response_time", 0.0),
                "current_connections": stats.get("current_connections", 0)
            }
        
        return result
        
    except Exception as e:
        logger.error(f"List providers failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Cost Management Endpoints
@app.post("/v1/budget")
async def set_user_budget(budget_request: BudgetRequest):
    """Set budget limit for a user."""
    try:
        from ..intelligence.cost_manager import BudgetPeriod
        
        period_map = {
            "daily": BudgetPeriod.DAILY,
            "weekly": BudgetPeriod.WEEKLY,
            "monthly": BudgetPeriod.MONTHLY
        }
        
        period = period_map.get(budget_request.period)
        if not period:
            raise HTTPException(status_code=400, detail="Invalid period")
        
        cost_manager.set_user_budget(
            budget_request.user_id,
            period,
            budget_request.limit_amount
        )
        
        return {"message": f"Budget set for user {budget_request.user_id}"}
        
    except Exception as e:
        logger.error(f"Set budget failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/v1/budget/{user_id}")
async def get_user_budget(user_id: str):
    """Get budget status for a user."""
    try:
        status = cost_manager.get_user_status(user_id)
        return status
        
    except Exception as e:
        logger.error(f"Get budget failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/v1/analytics")
async def get_usage_analytics(user_id: Optional[str] = None, days: int = 30):
    """Get usage analytics."""
    try:
        analytics = cost_manager.get_analytics(user_id, days)
        return analytics
        
    except Exception as e:
        logger.error(f"Get analytics failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Configuration Endpoints
@app.post("/v1/config/speculative")
async def update_speculative_config(config_request: SpeculativeConfigRequest):
    """Update speculative execution configuration."""
    try:
        if not speculative_engine:
            raise HTTPException(status_code=400, detail="Speculative engine not available")
        
        strategy_map = {
            "none": SpeculativeStrategy.NONE,
            "parallel_backup": SpeculativeStrategy.PARALLEL_BACKUP,
            "parallel_racing": SpeculativeStrategy.PARALLEL_RACING,
            "adaptive": SpeculativeStrategy.ADAPTIVE_SPECULATION
        }
        
        strategy = strategy_map.get(config_request.strategy)
        if not strategy:
            raise HTTPException(status_code=400, detail="Invalid strategy")
        
        config = SpeculativeConfig(
            strategy=strategy,
            backup_delay_ms=config_request.backup_delay_ms or 2000,
            max_parallel_requests=config_request.max_parallel_requests or 3,
            enable_racing=config_request.enable_racing or True
        )
        
        speculative_engine.update_config(config)
        
        return {"message": "Speculative configuration updated"}
        
    except Exception as e:
        logger.error(f"Update speculative config failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/v1/config/speculative")
async def get_speculative_config():
    """Get current speculative execution configuration."""
    try:
        if not speculative_engine:
            return {"enabled": False}
        
        stats = speculative_engine.get_performance_stats()
        return {
            "enabled": True,
            "strategy": stats.get("strategy"),
            "performance_stats": stats
        }
        
    except Exception as e:
        logger.error(f"Get speculative config failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Security Endpoints
@app.post("/v1/security/pii/scan")
async def scan_text_for_pii(text: str):
    """Scan text for PII and return detected items."""
    try:
        pii_matches = pii_redactor.detect_pii(text)
        
        return {
            "pii_detected": len(pii_matches) > 0,
            "pii_count": len(pii_matches),
            "pii_types": list(set(match.pii_type.value for match in pii_matches)),
            "matches": [
                {
                    "type": match.pii_type.value,
                    "text": match.original_text[:10] + "..." if len(match.original_text) > 10 else match.original_text,
                    "confidence": match.confidence,
                    "position": [match.start_pos, match.end_pos]
                }
                for match in pii_matches
            ]
        }
        
    except Exception as e:
        logger.error(f"PII scan failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/v1/security/pii/stats")
async def get_pii_redaction_stats():
    """Get PII redaction statistics."""
    try:
        stats = pii_redactor.get_redaction_stats()
        return {
            "total_redactions": sum(stats.values()),
            "by_type": stats
        }
        
    except Exception as e:
        logger.error(f"Get PII stats failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Monitoring Endpoints
@app.get("/v1/metrics")
async def get_metrics():
    """Get system metrics in Prometheus format."""
    try:
        from ..core.telemetry import telemetry_manager
        
        metrics = telemetry_manager.get_prometheus_metrics()
        if metrics:
            return Response(content=metrics, media_type="text/plain")
        else:
            return {"message": "Prometheus metrics not available"}
        
    except Exception as e:
        logger.error(f"Get metrics failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/v1/stats")
async def get_system_stats():
    """Get comprehensive system statistics."""
    try:
        stats = request_handler.get_request_stats()
        
        # Add additional stats
        if semantic_router:
            stats["semantic_routing"] = semantic_router.get_analysis_stats()
        
        if speculative_engine:
            stats["speculative_execution"] = speculative_engine.get_performance_stats()
        
        return stats
        
    except Exception as e:
        logger.error(f"Get system stats failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Startup event
@app.on_event("startup")
async def startup_event():
    """Initialize system on startup."""
    logger.info("AI Providers System starting up...")
    
    # Initialize speculative engine
    from ..intelligence.speculative_engine import initialize_speculative_engine
    initialize_speculative_engine(fallback_manager)
    
    logger.info("AI Providers System ready!")


# Shutdown event
@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown."""
    logger.info("AI Providers System shutting down...")
    
    # Cleanup active requests
    active_count = len(request_handler.active_requests)
    if active_count > 0:
        logger.warning(f"Shutting down with {active_count} active requests")
    
    logger.info("AI Providers System shutdown complete!")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)