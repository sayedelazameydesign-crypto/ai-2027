"""
Speculative Decoding and Parallel Requesting Engine

This module implements advanced performance optimization techniques including
speculative decoding and parallel requesting for ultra-fast response times.
"""

import asyncio
import time
from typing import Dict, List, Optional, Any, Tuple, AsyncGenerator
from dataclasses import dataclass
from enum import Enum
import logging
from datetime import datetime, timedelta

from ..providers.base import CompletionRequest, CompletionResponse, BaseProvider
from ..core.fallback_manager import FallbackManager


logger = logging.getLogger(__name__)


class SpeculativeStrategy(Enum):
    """Speculative execution strategies."""
    NONE = "none"                    # No speculation
    PARALLEL_BACKUP = "parallel_backup"      # Start backup after delay
    PARALLEL_RACING = "parallel_racing"     # Race multiple providers
    ADAPTIVE_SPECULATION = "adaptive_speculation"  # Smart speculation based on history


@dataclass
class SpeculativeConfig:
    """Configuration for speculative execution."""
    strategy: SpeculativeStrategy = SpeculativeStrategy.PARALLEL_BACKUP
    backup_delay_ms: int = 2000      # Delay before starting backup request
    max_parallel_requests: int = 3    # Maximum parallel requests
    cost_multiplier_limit: float = 2.0  # Max cost increase allowed
    enable_racing: bool = True        # Enable provider racing
    adaptive_threshold_ms: int = 1500  # Threshold for adaptive speculation
    
    # Provider-specific settings
    fast_providers: List[str] = None  # Providers considered "fast"
    reliable_providers: List[str] = None  # Providers considered "reliable"
    
    def __post_init__(self):
        if self.fast_providers is None:
            self.fast_providers = ['groq', 'deepseek', 'google']
        if self.reliable_providers is None:
            self.reliable_providers = ['openai', 'anthropic']


@dataclass
class SpeculativeRequest:
    """Represents a speculative request in progress."""
    request_id: str
    provider_name: str
    task: asyncio.Task
    start_time: datetime
    is_primary: bool
    estimated_cost: float
    
    @property
    def elapsed_ms(self) -> float:
        """Get elapsed time in milliseconds."""
        return (datetime.now() - self.start_time).total_seconds() * 1000


@dataclass
class SpeculativeResult:
    """Result from speculative execution."""
    response: CompletionResponse
    provider_used: str
    total_time_ms: float
    requests_made: int
    cost_incurred: float
    was_speculative: bool
    cancelled_requests: List[str]


class ProviderPerformanceTracker:
    """Tracks provider performance for adaptive speculation."""
    
    def __init__(self, history_size: int = 100):
        """Initialize performance tracker."""
        self.history_size = history_size
        self.response_times: Dict[str, List[float]] = {}
        self.success_rates: Dict[str, List[bool]] = {}
        self.failure_patterns: Dict[str, List[datetime]] = {}
        
    def record_response(self, provider: str, response_time_ms: float, success: bool):
        """Record provider response."""
        # Initialize lists if needed
        if provider not in self.response_times:
            self.response_times[provider] = []
            self.success_rates[provider] = []
            self.failure_patterns[provider] = []
        
        # Record response time
        self.response_times[provider].append(response_time_ms)
        if len(self.response_times[provider]) > self.history_size:
            self.response_times[provider] = self.response_times[provider][-self.history_size:]
        
        # Record success/failure
        self.success_rates[provider].append(success)
        if len(self.success_rates[provider]) > self.history_size:
            self.success_rates[provider] = self.success_rates[provider][-self.history_size:]
        
        # Record failure timestamp if applicable
        if not success:
            self.failure_patterns[provider].append(datetime.now())
            # Keep only recent failures (last hour)
            cutoff = datetime.now() - timedelta(hours=1)
            self.failure_patterns[provider] = [
                ts for ts in self.failure_patterns[provider] if ts > cutoff
            ]
    
    def get_average_response_time(self, provider: str) -> float:
        """Get average response time for provider."""
        times = self.response_times.get(provider, [])
        return sum(times) / len(times) if times else 0.0
    
    def get_success_rate(self, provider: str) -> float:
        """Get success rate for provider."""
        successes = self.success_rates.get(provider, [])
        return sum(successes) / len(successes) if successes else 0.0
    
    def get_recent_failure_count(self, provider: str) -> int:
        """Get recent failure count (last hour)."""
        return len(self.failure_patterns.get(provider, []))
    
    def is_provider_fast(self, provider: str, threshold_ms: float = 2000) -> bool:
        """Check if provider is considered fast."""
        avg_time = self.get_average_response_time(provider)
        return avg_time > 0 and avg_time < threshold_ms
    
    def is_provider_reliable(self, provider: str, min_success_rate: float = 0.9) -> bool:
        """Check if provider is considered reliable."""
        success_rate = self.get_success_rate(provider)
        recent_failures = self.get_recent_failure_count(provider)
        
        return success_rate >= min_success_rate and recent_failures < 3
    
    def should_speculate_against(self, primary_provider: str, backup_provider: str) -> bool:
        """Determine if speculation is worthwhile."""
        primary_avg = self.get_average_response_time(primary_provider)
        backup_avg = self.get_average_response_time(backup_provider)
        
        primary_reliable = self.is_provider_reliable(primary_provider)
        backup_fast = self.is_provider_fast(backup_provider)
        
        # Speculate if primary is slow/unreliable and backup is fast
        return (not primary_reliable or primary_avg > 3000) and backup_fast


class SpeculativeEngine:
    """
    Advanced speculative execution engine.
    
    Implements multiple strategies for optimizing response times through
    parallel requests, provider racing, and adaptive speculation.
    """
    
    def __init__(self, fallback_manager: FallbackManager, config: Optional[SpeculativeConfig] = None):
        """
        Initialize speculative engine.
        
        Args:
            fallback_manager: Fallback manager instance
            config: Speculative configuration
        """
        self.fallback_manager = fallback_manager
        self.config = config or SpeculativeConfig()
        self.performance_tracker = ProviderPerformanceTracker()
        
        # Active speculative requests
        self.active_requests: Dict[str, List[SpeculativeRequest]] = {}
        
        logger.info(f"Speculative engine initialized with strategy: {self.config.strategy.value}")
    
    async def execute_speculative_request(self, 
                                        request: CompletionRequest,
                                        request_id: str,
                                        user_id: Optional[str] = None) -> SpeculativeResult:
        """
        Execute request with speculative optimization.
        
        Args:
            request: Completion request
            request_id: Unique request identifier
            user_id: Optional user identifier for cost tracking
            
        Returns:
            Speculative execution result
        """
        start_time = datetime.now()
        
        if self.config.strategy == SpeculativeStrategy.NONE:
            return await self._execute_single_request(request, request_id)
        
        elif self.config.strategy == SpeculativeStrategy.PARALLEL_BACKUP:
            return await self._execute_parallel_backup(request, request_id, user_id)
        
        elif self.config.strategy == SpeculativeStrategy.PARALLEL_RACING:
            return await self._execute_parallel_racing(request, request_id, user_id)
        
        elif self.config.strategy == SpeculativeStrategy.ADAPTIVE_SPECULATION:
            return await self._execute_adaptive_speculation(request, request_id, user_id)
        
        else:
            # Fallback to single request
            return await self._execute_single_request(request, request_id)
    
    async def _execute_single_request(self, request: CompletionRequest, request_id: str) -> SpeculativeResult:
        """Execute single request without speculation."""
        start_time = datetime.now()
        
        # Get primary provider
        provider = await self.fallback_manager.select_optimal_provider()
        if not provider:
            raise Exception("No providers available")
        
        try:
            response = await provider.generate_completion(request)
            
            elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000
            
            # Record performance
            self.performance_tracker.record_response(provider.name, elapsed_ms, True)
            
            return SpeculativeResult(
                response=response,
                provider_used=provider.name,
                total_time_ms=elapsed_ms,
                requests_made=1,
                cost_incurred=0.0,  # Would need cost calculation
                was_speculative=False,
                cancelled_requests=[]
            )
            
        except Exception as e:
            elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000
            self.performance_tracker.record_response(provider.name, elapsed_ms, False)
            raise
    
    async def _execute_parallel_backup(self, request: CompletionRequest, 
                                     request_id: str, user_id: Optional[str]) -> SpeculativeResult:
        """Execute with parallel backup strategy."""
        start_time = datetime.now()
        
        # Get provider priority list
        providers = await self.fallback_manager.get_provider_priority_list()
        if not providers:
            raise Exception("No providers available")
        
        # Start primary request
        primary_provider_info = providers[0]
        primary_provider = await self.fallback_manager.get_provider(primary_provider_info.provider_name)
        
        if not primary_provider:
            raise Exception("Primary provider not available")
        
        # Create primary request
        primary_task = asyncio.create_task(
            self._execute_provider_request(primary_provider, request, request_id, True)
        )
        
        active_requests = [
            SpeculativeRequest(
                request_id=f"{request_id}_primary",
                provider_name=primary_provider.name,
                task=primary_task,
                start_time=datetime.now(),
                is_primary=True,
                estimated_cost=0.0
            )
        ]
        
        # Wait for backup delay or primary completion
        try:
            response = await asyncio.wait_for(
                primary_task, 
                timeout=self.config.backup_delay_ms / 1000.0
            )
            
            # Primary completed within delay - return result
            elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000
            self.performance_tracker.record_response(primary_provider.name, elapsed_ms, True)
            
            return SpeculativeResult(
                response=response,
                provider_used=primary_provider.name,
                total_time_ms=elapsed_ms,
                requests_made=1,
                cost_incurred=0.0,
                was_speculative=False,
                cancelled_requests=[]
            )
            
        except asyncio.TimeoutError:
            # Primary didn't complete in time - start backup
            logger.debug(f"Primary provider delayed, starting backup for request {request_id}")
            
            # Find fast backup provider
            backup_provider = await self._select_backup_provider(providers[1:])
            
            if backup_provider:
                backup_task = asyncio.create_task(
                    self._execute_provider_request(backup_provider, request, request_id, False)
                )
                
                active_requests.append(
                    SpeculativeRequest(
                        request_id=f"{request_id}_backup",
                        provider_name=backup_provider.name,
                        task=backup_task,
                        start_time=datetime.now(),
                        is_primary=False,
                        estimated_cost=0.0
                    )
                )
                
                # Wait for first completion
                return await self._wait_for_first_completion(active_requests, start_time)
            else:
                # No backup available - wait for primary
                try:
                    response = await primary_task
                    elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000
                    self.performance_tracker.record_response(primary_provider.name, elapsed_ms, True)
                    
                    return SpeculativeResult(
                        response=response,
                        provider_used=primary_provider.name,
                        total_time_ms=elapsed_ms,
                        requests_made=1,
                        cost_incurred=0.0,
                        was_speculative=False,
                        cancelled_requests=[]
                    )
                except Exception as e:
                    elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000
                    self.performance_tracker.record_response(primary_provider.name, elapsed_ms, False)
                    raise
    
    async def _execute_parallel_racing(self, request: CompletionRequest, 
                                     request_id: str, user_id: Optional[str]) -> SpeculativeResult:
        """Execute with parallel racing strategy."""
        start_time = datetime.now()
        
        # Get top providers for racing
        providers = await self.fallback_manager.get_provider_priority_list()
        racing_providers = providers[:min(self.config.max_parallel_requests, len(providers))]
        
        if not racing_providers:
            raise Exception("No providers available for racing")
        
        # Start all requests in parallel
        active_requests = []
        
        for i, provider_info in enumerate(racing_providers):
            provider = await self.fallback_manager.get_provider(provider_info.provider_name)
            if provider:
                task = asyncio.create_task(
                    self._execute_provider_request(provider, request, request_id, i == 0)
                )
                
                active_requests.append(
                    SpeculativeRequest(
                        request_id=f"{request_id}_race_{i}",
                        provider_name=provider.name,
                        task=task,
                        start_time=datetime.now(),
                        is_primary=i == 0,
                        estimated_cost=0.0
                    )
                )
        
        logger.debug(f"Started {len(active_requests)} racing requests for {request_id}")
        
        # Wait for first completion
        return await self._wait_for_first_completion(active_requests, start_time)
    
    async def _execute_adaptive_speculation(self, request: CompletionRequest, 
                                          request_id: str, user_id: Optional[str]) -> SpeculativeResult:
        """Execute with adaptive speculation based on performance history."""
        start_time = datetime.now()
        
        # Get provider priority list
        providers = await self.fallback_manager.get_provider_priority_list()
        if not providers:
            raise Exception("No providers available")
        
        primary_provider_info = providers[0]
        primary_provider = await self.fallback_manager.get_provider(primary_provider_info.provider_name)
        
        if not primary_provider:
            raise Exception("Primary provider not available")
        
        # Check if we should speculate based on history
        should_speculate = False
        backup_provider = None
        
        for backup_info in providers[1:]:
            if self.performance_tracker.should_speculate_against(
                primary_provider.name, backup_info.provider_name
            ):
                backup_provider = await self.fallback_manager.get_provider(backup_info.provider_name)
                if backup_provider:
                    should_speculate = True
                    break
        
        if not should_speculate:
            # No speculation needed - execute single request
            return await self._execute_single_request(request, request_id)
        
        # Execute with speculation
        logger.debug(f"Adaptive speculation: {primary_provider.name} vs {backup_provider.name}")
        
        # Start primary request
        primary_task = asyncio.create_task(
            self._execute_provider_request(primary_provider, request, request_id, True)
        )
        
        active_requests = [
            SpeculativeRequest(
                request_id=f"{request_id}_primary",
                provider_name=primary_provider.name,
                task=primary_task,
                start_time=datetime.now(),
                is_primary=True,
                estimated_cost=0.0
            )
        ]
        
        # Wait for adaptive threshold or completion
        try:
            response = await asyncio.wait_for(
                primary_task,
                timeout=self.config.adaptive_threshold_ms / 1000.0
            )
            
            # Primary completed quickly
            elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000
            self.performance_tracker.record_response(primary_provider.name, elapsed_ms, True)
            
            return SpeculativeResult(
                response=response,
                provider_used=primary_provider.name,
                total_time_ms=elapsed_ms,
                requests_made=1,
                cost_incurred=0.0,
                was_speculative=False,
                cancelled_requests=[]
            )
            
        except asyncio.TimeoutError:
            # Start backup request
            backup_task = asyncio.create_task(
                self._execute_provider_request(backup_provider, request, request_id, False)
            )
            
            active_requests.append(
                SpeculativeRequest(
                    request_id=f"{request_id}_backup",
                    provider_name=backup_provider.name,
                    task=backup_task,
                    start_time=datetime.now(),
                    is_primary=False,
                    estimated_cost=0.0
                )
            )
            
            return await self._wait_for_first_completion(active_requests, start_time)
    
    async def _execute_provider_request(self, provider: BaseProvider, 
                                      request: CompletionRequest, 
                                      request_id: str, is_primary: bool) -> CompletionResponse:
        """Execute request with specific provider."""
        try:
            response = await provider.generate_completion(request)
            logger.debug(f"Provider {provider.name} completed request {request_id} ({'primary' if is_primary else 'backup'})")
            return response
        except Exception as e:
            logger.warning(f"Provider {provider.name} failed for request {request_id}: {e}")
            raise
    
    async def _wait_for_first_completion(self, active_requests: List[SpeculativeRequest], 
                                       start_time: datetime) -> SpeculativeResult:
        """Wait for first request completion and cancel others."""
        tasks = [req.task for req in active_requests]
        
        try:
            # Wait for first completion
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            
            # Get the completed task
            completed_task = list(done)[0]
            completed_request = next(req for req in active_requests if req.task == completed_task)
            
            # Cancel pending tasks
            cancelled_requests = []
            for task in pending:
                task.cancel()
                cancelled_req = next(req for req in active_requests if req.task == task)
                cancelled_requests.append(cancelled_req.provider_name)
            
            # Get result
            response = await completed_task
            
            elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000
            
            # Record performance for completed provider
            self.performance_tracker.record_response(
                completed_request.provider_name, elapsed_ms, True
            )
            
            # Record failures for cancelled providers (they were slower)
            for cancelled_name in cancelled_requests:
                self.performance_tracker.record_response(cancelled_name, elapsed_ms * 2, False)
            
            logger.info(
                f"Speculative request completed: {completed_request.provider_name} "
                f"won in {elapsed_ms:.0f}ms, cancelled: {cancelled_requests}"
            )
            
            return SpeculativeResult(
                response=response,
                provider_used=completed_request.provider_name,
                total_time_ms=elapsed_ms,
                requests_made=len(active_requests),
                cost_incurred=0.0,  # Would need cost calculation
                was_speculative=len(active_requests) > 1,
                cancelled_requests=cancelled_requests
            )
            
        except Exception as e:
            # Cancel all pending tasks
            for task in tasks:
                if not task.done():
                    task.cancel()
            
            elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000
            
            # Record failures for all providers
            for req in active_requests:
                self.performance_tracker.record_response(req.provider_name, elapsed_ms, False)
            
            raise
    
    async def _select_backup_provider(self, provider_infos: List) -> Optional[BaseProvider]:
        """Select best backup provider from available options."""
        for provider_info in provider_infos:
            if provider_info.provider_name in self.config.fast_providers:
                provider = await self.fallback_manager.get_provider(provider_info.provider_name)
                if provider and self.performance_tracker.is_provider_fast(provider.name):
                    return provider
        
        # Fallback to first available
        for provider_info in provider_infos:
            provider = await self.fallback_manager.get_provider(provider_info.provider_name)
            if provider:
                return provider
        
        return None
    
    def get_performance_stats(self) -> Dict[str, Any]:
        """Get performance statistics."""
        stats = {}
        
        for provider in self.performance_tracker.response_times.keys():
            stats[provider] = {
                'average_response_time_ms': self.performance_tracker.get_average_response_time(provider),
                'success_rate': self.performance_tracker.get_success_rate(provider),
                'recent_failures': self.performance_tracker.get_recent_failure_count(provider),
                'is_fast': self.performance_tracker.is_provider_fast(provider),
                'is_reliable': self.performance_tracker.is_provider_reliable(provider)
            }
        
        return {
            'strategy': self.config.strategy.value,
            'provider_stats': stats,
            'active_requests': len(self.active_requests)
        }
    
    def update_config(self, config: SpeculativeConfig):
        """Update speculative configuration."""
        self.config = config
        logger.info(f"Updated speculative config: strategy={config.strategy.value}")


# Global speculative engine instance (will be initialized with fallback manager)
speculative_engine: Optional[SpeculativeEngine] = None


def initialize_speculative_engine(fallback_manager: FallbackManager, 
                                config: Optional[SpeculativeConfig] = None):
    """Initialize global speculative engine."""
    global speculative_engine
    speculative_engine = SpeculativeEngine(fallback_manager, config)
    logger.info("Global speculative engine initialized")