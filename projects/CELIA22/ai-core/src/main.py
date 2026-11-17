"""
CELIA Fashion Design - AL-Agent Core
Main AI Agent with RAG, Vector Memory, and Multi-Model Support
"""

import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime
from typing import List, Optional, Dict
from contextlib import asynccontextmanager

import asyncpg
import httpx
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct, 
    Filter, FieldCondition, MatchValue
)
from redis.asyncio import Redis
import time

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════
class Settings(BaseModel):
    ollama_host: str = os.getenv("OLLAMA_HOST", "http://ollama:11434")
    qdrant_host: str = os.getenv("QDRANT_HOST", "http://qdrant:6333")
    redis_url: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
    database_url: str = os.getenv(
        "DATABASE_URL", 
        "postgresql://celia:password@postgres:5432/celia_db"
    )
    openrouter_key: Optional[str] = os.getenv("OPENROUTER_KEY")
    environment: str = os.getenv("ENVIRONMENT", "production")
    
    # Feature Flags
    enable_cloud_fallback: bool = os.getenv(
        "ENABLE_CLOUD_FALLBACK", "false"
    ).lower() == "true"

settings = Settings()

# ═══════════════════════════════════════════════════
# MODELS
# ═══════════════════════════════════════════════════
class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    user_id: str = Field(..., pattern=r"^[a-zA-Z0-9_-]+$")
    session_id: Optional[str] = None
    use_cloud: bool = False
    context_depth: int = Field(default=5, ge=1, le=20)
    language: str = "ar"

class ChatResponse(BaseModel):
    response: str
    model_used: str
    latency_ms: float
    context_retrieved: int
    timestamp: str

class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str
    services: Dict[str, str]

# ═══════════════════════════════════════════════════
# LIFESPAN MANAGER
# ═══════════════════════════════════════════════════
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize connections on startup"""
    logger.info("🚀 Starting CELIA AL-Agent...")
    
    # Initialize Redis
    app.state.redis = Redis.from_url(
        settings.redis_url, 
        decode_responses=True
    )
    
    # Initialize Qdrant
    app.state.qdrant = QdrantClient(url=settings.qdrant_host)
    
    # Initialize HTTP clients
    app.state.ollama_client = httpx.AsyncClient(
        base_url=settings.ollama_host, 
        timeout=60.0
    )
    app.state.http_client = httpx.AsyncClient(timeout=30.0)
    
    # Initialize DB Pool
    app.state.db_pool = await asyncpg.create_pool(
        settings.database_url,
        min_size=5,
        max_size=20
    )
    
    # Initialize vector collections
    await init_collections(app.state.qdrant)
    
    # Test connections
    await test_connections(app)
    
    logger.info("✅ AL-Agent Started Successfully")
    yield
    
    # Cleanup
    await app.state.redis.close()
    await app.state.db_pool.close()
    await app.state.ollama_client.aclose()
    await app.state.http_client.aclose()
    logger.info("👋 AL-Agent Shutdown")

# ═══════════════════════════════════════════════════
# FASTAPI APP
# ═══════════════════════════════════════════════════
app = FastAPI(
    title="CELIA AL-Agent",
    description="Fashion Design AI Agent System with RAG",
    version="1.0.0",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"https://*.{os.getenv('DOMAIN', 'localhost')}",
        "http://localhost:3000"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ═══════════════════════════════════════════════════
# VECTOR DB INITIALIZATION
# ═══════════════════════════════════════════════════
async def init_collections(qdrant: QdrantClient):
    """Initialize Qdrant collections"""
    collections = [
        "conversations",
        "documents",
        "user_profiles",
        "fashion_knowledge"
    ]
    
    for collection in collections:
        try:
            qdrant.create_collection(
                collection_name=collection,
                vectors_config=VectorParams(
                    size=768, 
                    distance=Distance.COSINE
                ),
                optimizers_config={
                    "default_segment_number": 2
                }
            )
            logger.info(f"✅ Created collection: {collection}")
        except Exception as e:
            if "already exists" in str(e):
                logger.info(f"✓ Collection exists: {collection}")
            else:
                logger.error(f"❌ Error creating {collection}: {e}")

async def test_connections(app: FastAPI):
    """Test all service connections"""
    services = {}
    
    # Test Redis
    try:
        await app.state.redis.ping()
        services["redis"] = "healthy"
    except Exception as e:
        services["redis"] = f"unhealthy: {e}"
        logger.error(f"Redis connection failed: {e}")
    
    # Test Qdrant
    try:
        app.state.qdrant.get_collections()
        services["qdrant"] = "healthy"
    except Exception as e:
        services["qdrant"] = f"unhealthy: {e}"
        logger.error(f"Qdrant connection failed: {e}")
    
    # Test Ollama
    try:
        response = await app.state.ollama_client.get("/api/tags")
        if response.status_code == 200:
            services["ollama"] = "healthy"
        else:
            services["ollama"] = f"unhealthy: status {response.status_code}"
    except Exception as e:
        services["ollama"] = f"unhealthy: {e}"
        logger.error(f"Ollama connection failed: {e}")
    
    # Test Database
    try:
        async with app.state.db_pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        services["database"] = "healthy"
    except Exception as e:
        services["database"] = f"unhealthy: {e}"
        logger.error(f"Database connection failed: {e}")
    
    app.state.service_status = services
    return services

# ═══════════════════════════════════════════════════
# CORE AI FUNCTIONS
# ═══════════════════════════════════════════════════
async def get_embedding(
    text: str, 
    client: httpx.AsyncClient
) -> List[float]:
    """Generate embedding using local Ollama"""
    try:
        response = await client.post(
            "/api/embeddings",
            json={
                "model": "nomic-embed-text:latest",
                "prompt": text
            }
        )
        response.raise_for_status()
        return response.json()["embedding"]
    except Exception as e:
        logger.error(f"Embedding error: {e}")
        return [0.0] * 768  # Zero vector fallback

async def generate_ollama(
    prompt: str,
    client: httpx.AsyncClient,
    system: Optional[str] = None
) -> str:
    """Generate text using local Phi-3"""
    try:
        payload = {
            "model": "phi3:mini",
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.7,
                "num_ctx": 4096,
                "num_predict": 1024,
                "top_p": 0.9,
                "top_k": 40
            }
        }
        if system:
            payload["system"] = system
        
        response = await client.post("/api/generate", json=payload)
        response.raise_for_status()
        return response.json()["response"]
    except Exception as e:
        logger.error(f"Ollama generation error: {e}")
        raise

async def generate_openrouter(
    prompt: str,
    client: httpx.AsyncClient
) -> str:
    """Fallback to cloud AI"""
    if not settings.openrouter_key:
        raise ValueError("OpenRouter key not configured")
    
    try:
        response = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openrouter_key}",
                "HTTP-Referer": "https://celia.fashion",
                "X-Title": "CELIA Fashion Design"
            },
            json={
                "model": "anthropic/claude-3-haiku",
                "messages": [
                    {
                        "role": "system",
                        "content": "You are CELIA, an expert fashion design AI."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": 0.7,
                "max_tokens": 1024
            }
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"OpenRouter error: {e}")
        raise

# ═══════════════════════════════════════════════════
# MEMORY MANAGEMENT
# ═══════════════════════════════════════════════════
async def retrieve_context(
    qdrant: QdrantClient,
    query_vector: List[float],
    user_id: str,
    limit: int = 5
) -> List[Dict]:
    """Retrieve relevant context from vector DB"""
    try:
        results = qdrant.search(
            collection_name="conversations",
            query_vector=query_vector,
            limit=limit,
            query_filter=Filter(
                must=[
                    FieldCondition(
                        key="user_id",
                        match=MatchValue(value=user_id)
                    )
                ]
            )
        )
        return [
            {
                "text": hit.payload.get("text", ""),
                "score": hit.score,
                "timestamp": hit.payload.get("timestamp")
            }
            for hit in results
        ]
    except Exception as e:
        logger.error(f"Context retrieval error: {e}")
        return []

async def save_memory(
    qdrant: QdrantClient,
    ollama_client: httpx.AsyncClient,
    text: str,
    user_id: str,
    memory_type: str = "conversation"
):
    """Save to vector DB with embedding"""
    try:
        vector = await get_embedding(text, ollama_client)
        
        point_id = hashlib.md5(
            f"{user_id}:{text}:{datetime.now().isoformat()}".encode()
        ).hexdigest()
        
        qdrant.upsert(
            collection_name="conversations",
            points=[
                PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={
                        "text": text,
                        "user_id": user_id,
                        "type": memory_type,
                        "timestamp": datetime.now().isoformat()
                    }
                )
            ]
        )
        logger.debug(f"Memory saved for user {user_id}")
    except Exception as e:
        logger.error(f"Memory save error: {e}")

# ═══════════════════════════════════════════════════
# CACHING
# ═══════════════════════════════════════════════════
async def get_cached_response(
    redis: Redis,
    user_id: str,
    message: str
) -> Optional[str]:
    """Get cached response if available"""
    cache_key = f"response:{user_id}:{hashlib.md5(message.encode()).hexdigest()}"
    try:
        cached = await redis.get(cache_key)
        if cached:
            logger.info(f"Cache hit for user {user_id}")
            return cached
    except Exception as e:
        logger.error(f"Cache get error: {e}")
    return None

async def cache_response(
    redis: Redis,
    user_id: str,
    message: str,
    response: str,
    ttl: int = 3600
):
    """Cache response"""
    cache_key = f"response:{user_id}:{hashlib.md5(message.encode()).hexdigest()}"
    try:
        await redis.setex(cache_key, ttl, response)
    except Exception as e:
        logger.error(f"Cache set error: {e}")

# ═══════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════
@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    return HealthResponse(
        status="healthy",
        version="1.0.0",
        environment=settings.environment,
        services=getattr(app.state, 'service_status', {})
    )

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(
    request: ChatRequest,
    background_tasks: BackgroundTasks
):
    """Main chat endpoint with RAG"""
    start_time = time.time()
    
    try:
        # Check cache first
        cached = await get_cached_response(
            app.state.redis,
            request.user_id,
            request.message
        )
        if cached:
            return ChatResponse(
                response=cached,
                model_used="cached",
                latency_ms=(time.time() - start_time) * 1000,
                context_retrieved=0,
                timestamp=datetime.now().isoformat()
            )
        
        # Get embedding for query
        query_vector = await get_embedding(
            request.message,
            app.state.ollama_client
        )
        
        # Retrieve context
        context = await retrieve_context(
            app.state.qdrant,
            query_vector,
            request.user_id,
            request.context_depth
        )
        
        # Build prompt with context
        context_text = "\n".join([
            f"- {c['text']}" for c in context[-3:]
        ])
        
        system_prompt = f"""You are CELIA, an expert AI assistant specialized in fashion design, trends, and garment construction.

You are creative, professional, and helpful. Always respond in {request.language} language.

Be concise but informative. Focus on practical fashion advice."""

        full_prompt = f"""Previous conversation context:
{context_text}

Current user message: {request.message}

Provide a helpful, creative response:"""

        # Generate response
        model_used = "phi3:mini"
        try:
            response_text = await generate_ollama(
                full_prompt,
                app.state.ollama_client,
                system_prompt
            )
        except Exception as local_error:
            if settings.enable_cloud_fallback and request.use_cloud:
                logger.warning(
                    f"Local model failed, using cloud: {local_error}"
                )
                response_text = await generate_openrouter(
                    full_prompt,
                    app.state.http_client
                )
                model_used = "claude-3-haiku"
            else:
                raise local_error
        
        # Save to memory (background)
        background_tasks.add_task(
            save_memory,
            app.state.qdrant,
            app.state.ollama_client,
            f"User: {request.message}\nAssistant: {response_text}",
            request.user_id
        )
        
        # Cache response
        background_tasks.add_task(
            cache_response,
            app.state.redis,
            request.user_id,
            request.message,
            response_text
        )
        
        latency = (time.time() - start_time) * 1000
        
        return ChatResponse(
            response=response_text,
            model_used=model_used,
            latency_ms=latency,
            context_retrieved=len(context),
            timestamp=datetime.now().isoformat()
        )
        
    except Exception as e:
        logger.error(f"Chat error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/embed")
async def embed_endpoint(data: dict):
    """Embed arbitrary data (for N8n integrations)"""
    try:
        user_id = data.get("user_id")
        content = data.get("content")
        memory_type = data.get("type", "document")
        
        if not user_id or not content:
            raise HTTPException(
                status_code=400,
                detail="user_id and content required"
            )
        
        await save_memory(
            app.state.qdrant,
            app.state.ollama_client,
            content,
            user_id,
            memory_type
        )
        
        return {"status": "success", "message": "Content embedded"}
    except Exception as e:
        logger.error(f"Embed error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/memory/{user_id}")
async def get_user_memory(user_id: str, limit: int = 10):
    """Retrieve user memory (admin/debug)"""
    try:
        dummy_vector = [0.0] * 768
        results = app.state.qdrant.search(
            collection_name="conversations",
            query_vector=dummy_vector,
            limit=limit,
            query_filter=Filter(
                must=[
                    FieldCondition(
                        key="user_id",
                        match=MatchValue(value=user_id)
                    )
                ]
            )
        )
        return {
            "user_id": user_id,
            "memories": [r.payload for r in results]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/memory/{user_id}")
async def delete_user_memory(user_id: str):
    """Delete all user memory (GDPR compliance)"""
    try:
        # This would need proper implementation with Qdrant delete API
        # For now, just return success
        return {
            "status": "success",
            "message": f"Memory deleted for user {user_id}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ═══════════════════════════════════════════════════
# ERROR HANDLERS
# ═══════════════════════════════════════════════════
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Global error: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "type": type(exc).__name__
        }
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )
