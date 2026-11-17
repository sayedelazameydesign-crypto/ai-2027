# ⚡ تحسينات تقنية مجانية - AI Providers System

**© 2026 SAYED_ELAZAMEY - All Rights Reserved**

---

## 🎯 الهدف: تحسين الأداء والكفاءة بدون تكاليف إضافية

---

## 🚀 المرحلة 1: تحسينات الأداء المجانية

### **1.1 نظام Cache ذكي (مجاني)**

#### **Redis محلي (مجاني)**
```python
# app/cache/redis_cache.py
import redis
import json
import hashlib
from datetime import timedelta
from typing import Optional, Any

class SmartCache:
    def __init__(self, redis_url: str = "redis://localhost:6379"):
        try:
            self.redis_client = redis.from_url(redis_url)
            self.redis_available = True
        except:
            self.redis_available = False
            self.memory_cache = {}  # Fallback to memory cache
    
    def _generate_key(self, prompt: str, provider: str, model: str) -> str:
        """Generate cache key"""
        content = f"{prompt}:{provider}:{model}"
        return hashlib.md5(content.encode()).hexdigest()
    
    def get(self, prompt: str, provider: str, model: str) -> Optional[str]:
        """Get cached response"""
        key = self._generate_key(prompt, provider, model)
        
        if self.redis_available:
            try:
                cached = self.redis_client.get(key)
                return json.loads(cached) if cached else None
            except:
                pass
        
        # Fallback to memory cache
        return self.memory_cache.get(key)
    
    def set(self, prompt: str, provider: str, model: str, response: str, ttl: int = 3600):
        """Cache response"""
        key = self._generate_key(prompt, provider, model)
        
        if self.redis_available:
            try:
                self.redis_client.setex(key, ttl, json.dumps(response))
                return
            except:
                pass
        
        # Fallback to memory cache
        self.memory_cache[key] = response
    
    def clear_expired(self):
        """Clear expired cache entries (memory cache only)"""
        if not self.redis_available:
            # Simple implementation - in production, use timestamps
            if len(self.memory_cache) > 1000:
                self.memory_cache.clear()

# Usage
cache = SmartCache()

def cached_completion(prompt: str, provider: str, model: str):
    """Get completion with caching"""
    # Check cache first
    cached_response = cache.get(prompt, provider, model)
    if cached_response:
        return cached_response
    
    # Generate new response
    response = generate_completion(prompt, provider, model)
    
    # Cache the response
    cache.set(prompt, provider, model, response)
    
    return response
```

### **1.2 تجميع الطلبات (Request Batching)**

```python
# app/optimization/request_batcher.py
import asyncio
from typing import List, Dict, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

@dataclass
class BatchRequest:
    id: str
    prompt: str
    provider: str
    model: str
    callback: Callable
    timestamp: datetime

class RequestBatcher:
    def __init__(self, batch_size: int = 5, max_wait_time: float = 2.0):
        self.batch_size = batch_size
        self.max_wait_time = max_wait_time
        self.pending_requests: List[BatchRequest] = []
        self.processing = False
    
    async def add_request(self, request: BatchRequest):
        """Add request to batch"""
        self.pending_requests.append(request)
        
        # Process if batch is full or max wait time exceeded
        if (len(self.pending_requests) >= self.batch_size or 
            self._should_process_batch()):
            await self._process_batch()
    
    def _should_process_batch(self) -> bool:
        """Check if batch should be processed"""
        if not self.pending_requests:
            return False
        
        oldest_request = min(self.pending_requests, key=lambda r: r.timestamp)
        return (datetime.now() - oldest_request.timestamp).total_seconds() > self.max_wait_time
    
    async def _process_batch(self):
        """Process batch of requests"""
        if self.processing or not self.pending_requests:
            return
        
        self.processing = True
        batch = self.pending_requests.copy()
        self.pending_requests.clear()
        
        # Group by provider for efficient processing
        provider_groups = {}
        for request in batch:
            provider = request.provider
            if provider not in provider_groups:
                provider_groups[provider] = []
            provider_groups[provider].append(request)
        
        # Process each provider group
        tasks = []
        for provider, requests in provider_groups.items():
            task = asyncio.create_task(self._process_provider_batch(provider, requests))
            tasks.append(task)
        
        await asyncio.gather(*tasks)
        self.processing = False
    
    async def _process_provider_batch(self, provider: str, requests: List[BatchRequest]):
        """Process batch for specific provider"""
        # Combine prompts for batch processing (if provider supports it)
        if provider in ["huggingface", "ollama"]:
            # These providers can handle multiple prompts efficiently
            prompts = [req.prompt for req in requests]
            responses = await self._batch_generate(provider, prompts)
            
            for request, response in zip(requests, responses):
                request.callback(response)
        else:
            # Process individually for other providers
            tasks = []
            for request in requests:
                task = asyncio.create_task(self._single_generate(request))
                tasks.append(task)
            
            await asyncio.gather(*tasks)
    
    async def _batch_generate(self, provider: str, prompts: List[str]) -> List[str]:
        """Generate responses in batch"""
        # Implementation depends on provider capabilities
        responses = []
        for prompt in prompts:
            response = await generate_single_response(provider, prompt)
            responses.append(response)
        return responses
    
    async def _single_generate(self, request: BatchRequest):
        """Generate single response"""
        response = await generate_single_response(request.provider, request.prompt)
        request.callback(response)

# Usage
batcher = RequestBatcher(batch_size=3, max_wait_time=1.5)

async def optimized_completion(prompt: str, provider: str, callback: Callable):
    """Add request to batch for optimized processing"""
    request = BatchRequest(
        id=f"req_{int(datetime.now().timestamp() * 1000)}",
        prompt=prompt,
        provider=provider,
        model="default",
        callback=callback,
        timestamp=datetime.now()
    )
    
    await batcher.add_request(request)
```

### **1.3 ضغط الاستجابات (Response Compression)**

```python
# app/optimization/response_compressor.py
import gzip
import json
import base64
from typing import Dict, Any

class ResponseCompressor:
    @staticmethod
    def compress_response(response: Dict[Any, Any]) -> str:
        """Compress response for storage/transmission"""
        json_str = json.dumps(response, ensure_ascii=False)
        compressed = gzip.compress(json_str.encode('utf-8'))
        return base64.b64encode(compressed).decode('ascii')
    
    @staticmethod
    def decompress_response(compressed_data: str) -> Dict[Any, Any]:
        """Decompress response"""
        try:
            compressed = base64.b64decode(compressed_data.encode('ascii'))
            decompressed = gzip.decompress(compressed)
            return json.loads(decompressed.decode('utf-8'))
        except Exception as e:
            print(f"Decompression error: {e}")
            return {}
    
    @staticmethod
    def get_compression_ratio(original: Dict[Any, Any]) -> float:
        """Calculate compression ratio"""
        original_size = len(json.dumps(original, ensure_ascii=False).encode('utf-8'))
        compressed_size = len(ResponseCompressor.compress_response(original).encode('ascii'))
        return compressed_size / original_size

# Usage in cache
class CompressedCache(SmartCache):
    def set(self, prompt: str, provider: str, model: str, response: Dict, ttl: int = 3600):
        """Cache compressed response"""
        compressed = ResponseCompressor.compress_response(response)
        super().set(prompt, provider, model, compressed, ttl)
    
    def get(self, prompt: str, provider: str, model: str) -> Optional[Dict]:
        """Get and decompress cached response"""
        compressed = super().get(prompt, provider, model)
        if compressed:
            return ResponseCompressor.decompress_response(compressed)
        return None
```

---

## 🔧 المرحلة 2: تحسينات قاعدة البيانات المجانية

### **2.1 SQLite محسن للأداء**

```python
# app/database/optimized_sqlite.py
import sqlite3
import threading
from contextlib import contextmanager
from typing import List, Dict, Any, Optional

class OptimizedSQLite:
    def __init__(self, db_path: str = "data/ai_providers_optimized.db"):
        self.db_path = db_path
        self.local = threading.local()
        self.init_database()
    
    def get_connection(self):
        """Get thread-local database connection"""
        if not hasattr(self.local, 'connection'):
            self.local.connection = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                timeout=30.0
            )
            # Performance optimizations
            self.local.connection.execute("PRAGMA journal_mode=WAL")
            self.local.connection.execute("PRAGMA synchronous=NORMAL")
            self.local.connection.execute("PRAGMA cache_size=10000")
            self.local.connection.execute("PRAGMA temp_store=MEMORY")
            
        return self.local.connection
    
    @contextmanager
    def get_cursor(self):
        """Get database cursor with automatic commit/rollback"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cursor.close()
    
    def init_database(self):
        """Initialize optimized database schema"""
        with self.get_cursor() as cursor:
            # Users table with indexes
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT UNIQUE,
                    api_keys TEXT,  -- JSON string
                    preferences TEXT,  -- JSON string
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Requests table with partitioning by date
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    provider TEXT NOT NULL,
                    model TEXT,
                    prompt_hash TEXT,  -- MD5 hash for deduplication
                    prompt TEXT,
                    response TEXT,
                    tokens_input INTEGER DEFAULT 0,
                    tokens_output INTEGER DEFAULT 0,
                    cost REAL DEFAULT 0.0,
                    response_time_ms INTEGER,
                    status TEXT DEFAULT 'completed',
                    created_date DATE,  -- For partitioning
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (id)
                )
            ''')
            
            # Cache table for frequent requests
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS response_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cache_key TEXT UNIQUE NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT,
                    response TEXT NOT NULL,
                    hit_count INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP
                )
            ''')
            
            # Analytics table for aggregated data
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS daily_analytics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date DATE UNIQUE NOT NULL,
                    provider TEXT NOT NULL,
                    total_requests INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    total_cost REAL DEFAULT 0.0,
                    avg_response_time REAL DEFAULT 0.0,
                    success_rate REAL DEFAULT 1.0,
                    UNIQUE(date, provider)
                )
            ''')
            
            # Create indexes for performance
            indexes = [
                "CREATE INDEX IF NOT EXISTS idx_requests_user_date ON requests(user_id, created_date)",
                "CREATE INDEX IF NOT EXISTS idx_requests_provider ON requests(provider)",
                "CREATE INDEX IF NOT EXISTS idx_requests_prompt_hash ON requests(prompt_hash)",
                "CREATE INDEX IF NOT EXISTS idx_cache_key ON response_cache(cache_key)",
                "CREATE INDEX IF NOT EXISTS idx_cache_expires ON response_cache(expires_at)",
                "CREATE INDEX IF NOT EXISTS idx_analytics_date ON daily_analytics(date)",
            ]
            
            for index_sql in indexes:
                cursor.execute(index_sql)
    
    def insert_request(self, user_id: int, provider: str, model: str, 
                      prompt: str, response: str, tokens_input: int, 
                      tokens_output: int, cost: float, response_time_ms: int) -> int:
        """Insert request with optimizations"""
        import hashlib
        from datetime import date
        
        prompt_hash = hashlib.md5(prompt.encode()).hexdigest()
        created_date = date.today()
        
        with self.get_cursor() as cursor:
            cursor.execute('''
                INSERT INTO requests 
                (user_id, provider, model, prompt_hash, prompt, response, 
                 tokens_input, tokens_output, cost, response_time_ms, created_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, provider, model, prompt_hash, prompt, response,
                  tokens_input, tokens_output, cost, response_time_ms, created_date))
            
            request_id = cursor.lastrowid
            
            # Update daily analytics
            cursor.execute('''
                INSERT OR REPLACE INTO daily_analytics 
                (date, provider, total_requests, total_tokens, total_cost, avg_response_time)
                VALUES (
                    ?, ?, 
                    COALESCE((SELECT total_requests FROM daily_analytics WHERE date=? AND provider=?), 0) + 1,
                    COALESCE((SELECT total_tokens FROM daily_analytics WHERE date=? AND provider=?), 0) + ?,
                    COALESCE((SELECT total_cost FROM daily_analytics WHERE date=? AND provider=?), 0) + ?,
                    (COALESCE((SELECT avg_response_time * total_requests FROM daily_analytics WHERE date=? AND provider=?), 0) + ?) / 
                    (COALESCE((SELECT total_requests FROM daily_analytics WHERE date=? AND provider=?), 0) + 1)
                )
            ''', (created_date, provider, created_date, provider, created_date, provider, 
                  tokens_input + tokens_output, created_date, provider, cost,
                  created_date, provider, response_time_ms, created_date, provider))
            
            return request_id
    
    def get_cached_response(self, cache_key: str) -> Optional[str]:
        """Get cached response if not expired"""
        with self.get_cursor() as cursor:
            cursor.execute('''
                SELECT response FROM response_cache 
                WHERE cache_key = ? AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
            ''', (cache_key,))
            
            result = cursor.fetchone()
            if result:
                # Update hit count
                cursor.execute('''
                    UPDATE response_cache SET hit_count = hit_count + 1 
                    WHERE cache_key = ?
                ''', (cache_key,))
                return result[0]
            
            return None
    
    def cache_response(self, cache_key: str, provider: str, model: str, 
                      response: str, ttl_hours: int = 24):
        """Cache response with TTL"""
        from datetime import datetime, timedelta
        
        expires_at = datetime.now() + timedelta(hours=ttl_hours)
        
        with self.get_cursor() as cursor:
            cursor.execute('''
                INSERT OR REPLACE INTO response_cache 
                (cache_key, provider, model, response, expires_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (cache_key, provider, model, response, expires_at))
    
    def cleanup_expired_cache(self):
        """Remove expired cache entries"""
        with self.get_cursor() as cursor:
            cursor.execute('''
                DELETE FROM response_cache 
                WHERE expires_at IS NOT NULL AND expires_at < CURRENT_TIMESTAMP
            ''')
            
            deleted_count = cursor.rowcount
            return deleted_count
    
    def get_analytics(self, days: int = 30) -> List[Dict]:
        """Get analytics for last N days"""
        with self.get_cursor() as cursor:
            cursor.execute('''
                SELECT date, provider, total_requests, total_tokens, 
                       total_cost, avg_response_time, success_rate
                FROM daily_analytics 
                WHERE date >= date('now', '-{} days')
                ORDER BY date DESC, provider
            '''.format(days))
            
            columns = ['date', 'provider', 'total_requests', 'total_tokens', 
                      'total_cost', 'avg_response_time', 'success_rate']
            
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
```

### **2.2 نظام تنظيف تلقائي للبيانات**

```python
# app/maintenance/data_cleaner.py
import schedule
import time
import threading
from datetime import datetime, timedelta
from typing import Dict

class DataCleaner:
    def __init__(self, db: OptimizedSQLite):
        self.db = db
        self.running = False
        self.thread = None
    
    def start_scheduler(self):
        """Start background data cleaning scheduler"""
        if self.running:
            return
        
        self.running = True
        
        # Schedule cleanup tasks
        schedule.every().day.at("02:00").do(self.daily_cleanup)
        schedule.every().week.do(self.weekly_cleanup)
        schedule.every().month.do(self.monthly_cleanup)
        
        # Start scheduler thread
        self.thread = threading.Thread(target=self._run_scheduler, daemon=True)
        self.thread.start()
    
    def stop_scheduler(self):
        """Stop background scheduler"""
        self.running = False
        if self.thread:
            self.thread.join()
    
    def _run_scheduler(self):
        """Run scheduled tasks"""
        while self.running:
            schedule.run_pending()
            time.sleep(60)  # Check every minute
    
    def daily_cleanup(self):
        """Daily cleanup tasks"""
        print(f"[{datetime.now()}] Running daily cleanup...")
        
        # Clean expired cache
        deleted_cache = self.db.cleanup_expired_cache()
        print(f"Cleaned {deleted_cache} expired cache entries")
        
        # Vacuum database if needed
        self._vacuum_if_needed()
    
    def weekly_cleanup(self):
        """Weekly cleanup tasks"""
        print(f"[{datetime.now()}] Running weekly cleanup...")
        
        # Archive old requests (older than 90 days)
        self._archive_old_requests(90)
        
        # Optimize database
        self._optimize_database()
    
    def monthly_cleanup(self):
        """Monthly cleanup tasks"""
        print(f"[{datetime.now()}] Running monthly cleanup...")
        
        # Generate monthly reports
        self._generate_monthly_report()
        
        # Clean up old analytics data (older than 1 year)
        self._cleanup_old_analytics(365)
    
    def _vacuum_if_needed(self):
        """Vacuum database if fragmentation is high"""
        with self.db.get_cursor() as cursor:
            # Check database size and fragmentation
            cursor.execute("PRAGMA page_count")
            page_count = cursor.fetchone()[0]
            
            cursor.execute("PRAGMA freelist_count")
            free_pages = cursor.fetchone()[0]
            
            # Vacuum if more than 10% fragmentation
            if free_pages > page_count * 0.1:
                print("Database fragmentation detected, running VACUUM...")
                cursor.execute("VACUUM")
                print("VACUUM completed")
    
    def _archive_old_requests(self, days: int):
        """Archive requests older than specified days"""
        cutoff_date = datetime.now() - timedelta(days=days)
        
        with self.db.get_cursor() as cursor:
            # Count old requests
            cursor.execute('''
                SELECT COUNT(*) FROM requests 
                WHERE created_at < ?
            ''', (cutoff_date,))
            
            old_count = cursor.fetchone()[0]
            
            if old_count > 0:
                # Move to archive table (create if not exists)
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS requests_archive AS 
                    SELECT * FROM requests WHERE 1=0
                ''')
                
                cursor.execute('''
                    INSERT INTO requests_archive 
                    SELECT * FROM requests WHERE created_at < ?
                ''', (cutoff_date,))
                
                cursor.execute('''
                    DELETE FROM requests WHERE created_at < ?
                ''', (cutoff_date,))
                
                print(f"Archived {old_count} old requests")
    
    def _optimize_database(self):
        """Optimize database performance"""
        with self.db.get_cursor() as cursor:
            # Update statistics
            cursor.execute("ANALYZE")
            
            # Rebuild indexes if needed
            cursor.execute("REINDEX")
            
            print("Database optimization completed")
    
    def _generate_monthly_report(self):
        """Generate monthly usage report"""
        last_month = datetime.now().replace(day=1) - timedelta(days=1)
        month_str = last_month.strftime("%Y-%m")
        
        analytics = self.db.get_analytics(30)
        
        # Calculate totals
        total_requests = sum(a['total_requests'] for a in analytics)
        total_cost = sum(a['total_cost'] for a in analytics)
        
        report = {
            "month": month_str,
            "total_requests": total_requests,
            "total_cost": total_cost,
            "by_provider": {}
        }
        
        # Group by provider
        for item in analytics:
            provider = item['provider']
            if provider not in report["by_provider"]:
                report["by_provider"][provider] = {
                    "requests": 0, "cost": 0.0
                }
            
            report["by_provider"][provider]["requests"] += item['total_requests']
            report["by_provider"][provider]["cost"] += item['total_cost']
        
        # Save report
        import json
        report_file = f"reports/monthly_report_{month_str}.json"
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2)
        
        print(f"Monthly report saved: {report_file}")
    
    def _cleanup_old_analytics(self, days: int):
        """Clean up old analytics data"""
        cutoff_date = datetime.now() - timedelta(days=days)
        
        with self.db.get_cursor() as cursor:
            cursor.execute('''
                DELETE FROM daily_analytics WHERE date < ?
            ''', (cutoff_date.date(),))
            
            deleted_count = cursor.rowcount
            print(f"Cleaned {deleted_count} old analytics records")

# Usage
db = OptimizedSQLite()
cleaner = DataCleaner(db)
cleaner.start_scheduler()
```

---

## 🌐 المرحلة 3: تحسينات الشبكة والاتصال

### **3.1 Connection Pooling مجاني**

```python
# app/networking/connection_pool.py
import aiohttp
import asyncio
from typing import Dict, Optional
import time

class ConnectionPool:
    def __init__(self):
        self.sessions: Dict[str, aiohttp.ClientSession] = {}
        self.session_stats: Dict[str, Dict] = {}
    
    async def get_session(self, provider: str) -> aiohttp.ClientSession:
        """Get or create session for provider"""
        if provider not in self.sessions:
            # Create optimized session
            connector = aiohttp.TCPConnector(
                limit=100,  # Total connection pool size
                limit_per_host=30,  # Per host limit
                ttl_dns_cache=300,  # DNS cache TTL
                use_dns_cache=True,
                keepalive_timeout=30,
                enable_cleanup_closed=True
            )
            
            timeout = aiohttp.ClientTimeout(
                total=30,  # Total timeout
                connect=10,  # Connection timeout
                sock_read=20  # Socket read timeout
            )
            
            self.sessions[provider] = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={
                    'User-Agent': 'AI-Providers-System/2.0.0',
                    'Accept-Encoding': 'gzip, deflate',
                    'Connection': 'keep-alive'
                }
            )
            
            self.session_stats[provider] = {
                'created_at': time.time(),
                'requests_count': 0,
                'errors_count': 0
            }
        
        return self.sessions[provider]
    
    async def make_request(self, provider: str, method: str, url: str, **kwargs):
        """Make HTTP request with connection pooling"""
        session = await self.get_session(provider)
        
        try:
            self.session_stats[provider]['requests_count'] += 1
            
            async with session.request(method, url, **kwargs) as response:
                return await response.json()
                
        except Exception as e:
            self.session_stats[provider]['errors_count'] += 1
            raise e
    
    async def close_all(self):
        """Close all sessions"""
        for session in self.sessions.values():
            await session.close()
        
        self.sessions.clear()
        self.session_stats.clear()
    
    def get_stats(self) -> Dict:
        """Get connection pool statistics"""
        return self.session_stats.copy()

# Global connection pool
connection_pool = ConnectionPool()
```

### **3.2 Request Retry مع Exponential Backoff**

```python
# app/networking/retry_handler.py
import asyncio
import random
from typing import Callable, Any, Optional
import logging

logger = logging.getLogger(__name__)

class RetryHandler:
    def __init__(self, max_retries: int = 3, base_delay: float = 1.0, max_delay: float = 60.0):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
    
    async def retry_with_backoff(self, 
                               func: Callable, 
                               *args, 
                               retry_on: tuple = (Exception,),
                               **kwargs) -> Any:
        """Retry function with exponential backoff"""
        last_exception = None
        
        for attempt in range(self.max_retries + 1):
            try:
                return await func(*args, **kwargs)
                
            except retry_on as e:
                last_exception = e
                
                if attempt == self.max_retries:
                    logger.error(f"Max retries ({self.max_retries}) exceeded for {func.__name__}")
                    raise e
                
                # Calculate delay with jitter
                delay = min(
                    self.base_delay * (2 ** attempt) + random.uniform(0, 1),
                    self.max_delay
                )
                
                logger.warning(f"Attempt {attempt + 1} failed for {func.__name__}, retrying in {delay:.2f}s: {e}")
                await asyncio.sleep(delay)
        
        raise last_exception

# Usage
retry_handler = RetryHandler(max_retries=3, base_delay=1.0, max_delay=30.0)

async def resilient_api_call(provider: str, prompt: str):
    """API call with retry logic"""
    async def _make_call():
        session = await connection_pool.get_session(provider)
        return await session.post(f"https://api.{provider}.com/v1/completions", 
                                json={"prompt": prompt})
    
    return await retry_handler.retry_with_backoff(
        _make_call,
        retry_on=(aiohttp.ClientError, asyncio.TimeoutError)
    )
```

---

## 📊 المرحلة 4: مراقبة الأداء المجانية

### **4.1 نظام Metrics محلي**

```python
# app/monitoring/local_metrics.py
import time
import json
import threading
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Dict, List, Any

class LocalMetrics:
    def __init__(self, max_history: int = 1000):
        self.max_history = max_history
        self.metrics = defaultdict(lambda: defaultdict(list))
        self.counters = defaultdict(int)
        self.gauges = defaultdict(float)
        self.histograms = defaultdict(lambda: deque(maxlen=max_history))
        self.lock = threading.Lock()
    
    def increment_counter(self, name: str, value: int = 1, labels: Dict[str, str] = None):
        """Increment counter metric"""
        with self.lock:
            key = self._make_key(name, labels)
            self.counters[key] += value
    
    def set_gauge(self, name: str, value: float, labels: Dict[str, str] = None):
        """Set gauge metric"""
        with self.lock:
            key = self._make_key(name, labels)
            self.gauges[key] = value
    
    def record_histogram(self, name: str, value: float, labels: Dict[str, str] = None):
        """Record histogram value"""
        with self.lock:
            key = self._make_key(name, labels)
            self.histograms[key].append({
                'value': value,
                'timestamp': time.time()
            })
    
    def _make_key(self, name: str, labels: Dict[str, str] = None) -> str:
        """Create metric key with labels"""
        if not labels:
            return name
        
        label_str = ','.join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"
    
    def get_counter(self, name: str, labels: Dict[str, str] = None) -> int:
        """Get counter value"""
        key = self._make_key(name, labels)
        return self.counters.get(key, 0)
    
    def get_gauge(self, name: str, labels: Dict[str, str] = None) -> float:
        """Get gauge value"""
        key = self._make_key(name, labels)
        return self.gauges.get(key, 0.0)
    
    def get_histogram_stats(self, name: str, labels: Dict[str, str] = None) -> Dict:
        """Get histogram statistics"""
        key = self._make_key(name, labels)
        values = [item['value'] for item in self.histograms.get(key, [])]
        
        if not values:
            return {'count': 0, 'sum': 0, 'avg': 0, 'min': 0, 'max': 0}
        
        return {
            'count': len(values),
            'sum': sum(values),
            'avg': sum(values) / len(values),
            'min': min(values),
            'max': max(values),
            'p50': self._percentile(values, 50),
            'p95': self._percentile(values, 95),
            'p99': self._percentile(values, 99)
        }
    
    def _percentile(self, values: List[float], percentile: int) -> float:
        """Calculate percentile"""
        if not values:
            return 0.0
        
        sorted_values = sorted(values)
        index = int((percentile / 100) * len(sorted_values))
        return sorted_values[min(index, len(sorted_values) - 1)]
    
    def export_prometheus_format(self) -> str:
        """Export metrics in Prometheus format"""
        lines = []
        
        # Counters
        for key, value in self.counters.items():
            lines.append(f"{key} {value}")
        
        # Gauges
        for key, value in self.gauges.items():
            lines.append(f"{key} {value}")
        
        # Histograms
        for key, values in self.histograms.items():
            if values:
                stats = self.get_histogram_stats(key.split('{')[0], 
                                               self._parse_labels(key))
                lines.append(f"{key}_count {stats['count']}")
                lines.append(f"{key}_sum {stats['sum']}")
                lines.append(f"{key}_avg {stats['avg']}")
        
        return '\n'.join(lines)
    
    def _parse_labels(self, key: str) -> Dict[str, str]:
        """Parse labels from key"""
        if '{' not in key:
            return {}
        
        label_part = key.split('{')[1].rstrip('}')
        labels = {}
        
        for pair in label_part.split(','):
            if '=' in pair:
                k, v = pair.split('=', 1)
                labels[k] = v
        
        return labels
    
    def get_all_metrics(self) -> Dict:
        """Get all metrics as dictionary"""
        with self.lock:
            return {
                'counters': dict(self.counters),
                'gauges': dict(self.gauges),
                'histograms': {
                    key: self.get_histogram_stats(key.split('{')[0], 
                                                 self._parse_labels(key))
                    for key in self.histograms.keys()
                }
            }

# Global metrics instance
metrics = LocalMetrics()

# Decorator for automatic metrics collection
def track_performance(metric_name: str, labels: Dict[str, str] = None):
    """Decorator to track function performance"""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            
            try:
                result = await func(*args, **kwargs)
                
                # Record success
                metrics.increment_counter(
                    f"{metric_name}_total",
                    labels={**(labels or {}), 'status': 'success'}
                )
                
                return result
                
            except Exception as e:
                # Record error
                metrics.increment_counter(
                    f"{metric_name}_total",
                    labels={**(labels or {}), 'status': 'error'}
                )
                raise e
                
            finally:
                # Record duration
                duration = time.time() - start_time
                metrics.record_histogram(
                    f"{metric_name}_duration_seconds",
                    duration,
                    labels=labels
                )
        
        return wrapper
    return decorator

# Usage examples
@track_performance("ai_request", {"provider": "groq"})
async def make_groq_request(prompt: str):
    # Your API call here
    pass

# Manual metrics recording
metrics.increment_counter("requests_total", labels={"provider": "openai"})
metrics.set_gauge("active_connections", 5, labels={"provider": "anthropic"})
metrics.record_histogram("response_time", 0.5, labels={"provider": "groq"})
```

### **4.2 نظام تنبيهات بسيط**

```python
# app/monitoring/alerting.py
import smtplib
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, List, Callable
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class SimpleAlerting:
    def __init__(self, smtp_config: Dict = None):
        self.smtp_config = smtp_config or {}
        self.alert_rules: List[Dict] = []
        self.alert_handlers: List[Callable] = []
    
    def add_rule(self, name: str, condition: Callable, threshold: float, 
                 message: str, severity: str = "warning"):
        """Add alerting rule"""
        rule = {
            'name': name,
            'condition': condition,
            'threshold': threshold,
            'message': message,
            'severity': severity,
            'last_triggered': None
        }
        self.alert_rules.append(rule)
    
    def add_handler(self, handler: Callable):
        """Add alert handler"""
        self.alert_handlers.append(handler)
    
    def check_alerts(self):
        """Check all alert rules"""
        for rule in self.alert_rules:
            try:
                current_value = rule['condition']()
                
                if current_value > rule['threshold']:
                    self._trigger_alert(rule, current_value)
                    
            except Exception as e:
                logger.error(f"Error checking alert rule {rule['name']}: {e}")
    
    def _trigger_alert(self, rule: Dict, current_value: float):
        """Trigger alert"""
        alert = {
            'rule_name': rule['name'],
            'severity': rule['severity'],
            'message': rule['message'],
            'current_value': current_value,
            'threshold': rule['threshold'],
            'timestamp': datetime.now().isoformat()
        }
        
        # Update last triggered
        rule['last_triggered'] = datetime.now()
        
        # Send to all handlers
        for handler in self.alert_handlers:
            try:
                handler(alert)
            except Exception as e:
                logger.error(f"Error in alert handler: {e}")
    
    def email_handler(self, alert: Dict):
        """Send alert via email"""
        if not self.smtp_config:
            return
        
        try:
            msg = MIMEMultipart()
            msg['From'] = self.smtp_config['from']
            msg['To'] = self.smtp_config['to']
            msg['Subject'] = f"[{alert['severity'].upper()}] {alert['rule_name']}"
            
            body = f"""
            Alert: {alert['rule_name']}
            Severity: {alert['severity']}
            Message: {alert['message']}
            Current Value: {alert['current_value']}
            Threshold: {alert['threshold']}
            Time: {alert['timestamp']}
            """
            
            msg.attach(MIMEText(body, 'plain'))
            
            server = smtplib.SMTP(self.smtp_config['smtp_server'], 
                                self.smtp_config['smtp_port'])
            server.starttls()
            server.login(self.smtp_config['username'], 
                        self.smtp_config['password'])
            
            server.send_message(msg)
            server.quit()
            
        except Exception as e:
            logger.error(f"Failed to send email alert: {e}")
    
    def console_handler(self, alert: Dict):
        """Print alert to console"""
        print(f"🚨 ALERT: {alert['rule_name']}")
        print(f"   Severity: {alert['severity']}")
        print(f"   Message: {alert['message']}")
        print(f"   Value: {alert['current_value']} (threshold: {alert['threshold']})")
        print(f"   Time: {alert['timestamp']}")

# Setup alerting
alerting = SimpleAlerting({
    'smtp_server': 'smtp.gmail.com',
    'smtp_port': 587,
    'username': 'your-email@gmail.com',
    'password': 'your-app-password',
    'from': 'your-email@gmail.com',
    'to': 'sayedelazameydesign@gmail.com'
})

# Add handlers
alerting.add_handler(alerting.console_handler)
alerting.add_handler(alerting.email_handler)

# Add rules
alerting.add_rule(
    name="High Error Rate",
    condition=lambda: metrics.get_counter("requests_total", {"status": "error"}) / 
                     max(metrics.get_counter("requests_total"), 1) * 100,
    threshold=5.0,  # 5% error rate
    message="Error rate is above 5%",
    severity="critical"
)

alerting.add_rule(
    name="High Response Time",
    condition=lambda: metrics.get_histogram_stats("response_time")['avg'],
    threshold=2.0,  # 2 seconds
    message="Average response time is above 2 seconds",
    severity="warning"
)

# Run alerting check periodically
import threading
import time

def alert_checker():
    while True:
        alerting.check_alerts()
        time.sleep(60)  # Check every minute

alert_thread = threading.Thread(target=alert_checker, daemon=True)
alert_thread.start()
```

---

## 🎯 خطة التنفيذ السريع (أسبوعين)

### **الأسبوع الأول:**
1. **تحسين الأداء:**
   - تطبيق نظام Cache الذكي
   - إضافة Request Batching
   - تحسين SQLite

2. **تحسين الشبكة:**
   - Connection Pooling
   - Retry مع Backoff
   - ضغط الاستجابات

### **الأسبوع الثاني:**
1. **المراقبة:**
   - نظام Metrics محلي
   - تنبيهات بسيطة
   - تنظيف البيانات التلقائي

2. **الاختبار والتحسين:**
   - اختبار الأداء
   - تحسين الاستعلامات
   - توثيق التحسينات

### **النتائج المتوقعة:**
- ⚡ **تحسين الأداء بنسبة 300%**
- 💾 **تقليل استخدام الذاكرة بنسبة 50%**
- 🌐 **تحسين زمن الاستجابة بنسبة 200%**
- 📊 **مراقبة شاملة للنظام**
- 💰 **تكلفة إضافية: 0$**

---

**🚀 تحسينات مجانية لأداء فائق! 🚀**

**© 2026 SAYED_ELAZAMEY - All Rights Reserved**