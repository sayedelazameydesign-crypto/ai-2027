"""
Fallback Manager with Priority Matrix and Load Balancing

This module implements intelligent fallback management with weighted round-robin
load balancing and priority-based provider selection.
"""

import asyncio
import time
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum
import logging
import random

from .circuit_breaker import circuit_breaker_manager, CircuitBreakerConfig
from .config_manager import ConfigurationManager
from ..providers.factory import ProviderFactory, ProviderType
from ..providers.base import BaseProvider, ProviderConfig, ProviderError


logger = logging.getLogger(__name__)


class LoadBalancingStrategy(Enum):
    """Load balancing strategies."""
    PRIORITY_ONLY = "priority_only"
    WEIGHTED_ROUND_ROBIN = "weighted_round_robin"
    LEAST_CONNECTIONS = "least_connections"
    FASTEST_RESPONSE = "fastest_response"


@dataclass
class ProviderPriority:
    """Provider priority information."""
    provider_name: str
    priority: int  # Lower number = higher priority
    weight: float = 1.0  # For weighted round-robin
    cost_factor: float = 1.0  # Relative cost (1.0 = baseline)
    speed_factor: float = 1.0  # Relative speed (1.0 = baseline)
    reliability_factor: float = 1.0  # Reliability score (0.0-1.0)
    
    @property
    def composite_score(self) -> float:
        """Calculate composite score for ranking."""
        # Higher is better: reliability/cost * speed
        if self.cost_factor <= 0:
            return 0.0
        return (self.reliability_factor / self.cost_factor) * self.speed_factor


@dataclass
class ProviderHealth:
    """Provider health tracking."""
    provider_name: str
    is_healthy: bool = True
    last_check: datetime = None
    failure_count: int = 0
    success_count: int = 0
    last_failure: Optional[datetime] = None
    last_success: Optional[datetime] = None
    response_times: List[float] = None
    current_connections: int = 0
    
    def __post_init__(self):
        if self.last_check is None:
            self.last_check = datetime.now()
        if self.response_times is None:
            self.response_times = []
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate."""
        total = self.success_count + self.failure_count
        if total == 0:
            return 1.0
        return self.success_count / total
    
    @property
    def average_response_time(self) -> float:
        """Calculate average response time."""
        if not self.response_times:
            return 0.0
        return sum(self.response_times) / len(self.response_times)


class PriorityMatrix:
    """
    Priority matrix for intelligent provider selection.
    
    Considers cost, speed, and reliability factors to determine
    optimal provider ordering.
    """
    
    def __init__(self):
        """Initialize priority matrix."""
        self.providers: Dict[str, ProviderPriority] = {}
        self.health_data: Dict[str, ProviderHealth] = {}
        
        # Default priority configuration
        self._default_priorities = {
            'openai': ProviderPriority('openai', 10, 1.0, 1.0, 0.9, 0.95),
            'anthropic': ProviderPriority('anthropic', 20, 1.0, 1.2, 0.8, 0.92),
            'google': ProviderPriority('google', 30, 1.0, 0.8, 1.1, 0.88),
            'groq': ProviderPriority('groq', 40, 1.0, 0.3, 1.5, 0.85),
            'deepseek': ProviderPriority('deepseek', 50, 1.0, 0.5, 1.2, 0.80),
            'huggingface': ProviderPriority('huggingface', 60, 1.0, 0.2, 0.7, 0.75),
            'openrouter': ProviderPriority('openrouter', 70, 1.0, 0.9, 0.8, 0.82),
        }
        
        self.providers.update(self._default_priorities)
        logger.info("Priority matrix initialized with default priorities")
    
    def update_provider_priority(self, provider_name: str, priority: ProviderPriority):
        """Update priority for a provider."""
        self.providers[provider_name] = priority
        logger.info(f"Updated priority for {provider_name}: {priority}")
    
    def get_sorted_providers(self, strategy: LoadBalancingStrategy = LoadBalancingStrategy.PRIORITY_ONLY) -> List[ProviderPriority]:
        """
        Get providers sorted by strategy.
        
        Args:
            strategy: Load balancing strategy to use
            
        Returns:
            List of providers sorted by priority/strategy
        """
        available_providers = [
            p for p in self.providers.values()
            if self._is_provider_available(p.provider_name)
        ]
        
        if strategy == LoadBalancingStrategy.PRIORITY_ONLY:
            return sorted(available_providers, key=lambda p: p.priority)
        
        elif strategy == LoadBalancingStrategy.WEIGHTED_ROUND_ROBIN:
            return sorted(available_providers, key=lambda p: (p.priority, -p.weight))
        
        elif strategy == LoadBalancingStrategy.LEAST_CONNECTIONS:
            return sorted(available_providers, key=lambda p: (
                p.priority,
                self.health_data.get(p.provider_name, ProviderHealth(p.provider_name)).current_connections
            ))
        
        elif strategy == LoadBalancingStrategy.FASTEST_RESPONSE:
            return sorted(available_providers, key=lambda p: (
                p.priority,
                self.health_data.get(p.provider_name, ProviderHealth(p.provider_name)).average_response_time
            ))
        
        else:
            return sorted(available_providers, key=lambda p: -p.composite_score)
    
    def _is_provider_available(self, provider_name: str) -> bool:
        """Check if provider is available."""
        health = self.health_data.get(provider_name)
        if not health:
            return True  # Assume available if no health data
        
        # Check circuit breaker
        if provider_name in circuit_breaker_manager:
            circuit_breaker = circuit_breaker_manager.get_circuit_breaker(provider_name)
            if not circuit_breaker.is_available():
                return False
        
        return health.is_healthy
    
    def update_health_data(self, provider_name: str, health: ProviderHealth):
        """Update health data for provider."""
        self.health_data[provider_name] = health


class WeightedRoundRobin:
    """Weighted round-robin load balancer."""
    
    def __init__(self):
        """Initialize weighted round-robin."""
        self.current_weights: Dict[str, float] = {}
        self.total_weight = 0.0
    
    def select_provider(self, providers: List[ProviderPriority]) -> Optional[ProviderPriority]:
        """
        Select provider using weighted round-robin algorithm.
        
        Args:
            providers: List of available providers
            
        Returns:
            Selected provider or None if no providers available
        """
        if not providers:
            return None
        
        # Initialize weights if needed
        for provider in providers:
            if provider.provider_name not in self.current_weights:
                self.current_weights[provider.provider_name] = 0.0
        
        # Calculate total weight
        total_weight = sum(p.weight for p in providers)
        if total_weight <= 0:
            # Fallback to first provider if no weights
            return providers[0]
        
        # Update current weights
        for provider in providers:
            self.current_weights[provider.provider_name] += provider.weight
        
        # Find provider with highest current weight
        selected = max(providers, key=lambda p: self.current_weights[p.provider_name])
        
        # Reduce selected provider's current weight
        self.current_weights[selected.provider_name] -= total_weight
        
        return selected


class FallbackManager:
    """
    Intelligent fallback manager with priority matrix and load balancing.
    
    Provides automatic failover, load balancing, and provider health monitoring
    for high availability AI provider access.
    """
    
    def __init__(self, config_manager: ConfigurationManager):
        """
        Initialize fallback manager.
        
        Args:
            config_manager: Configuration manager instance
        """
        self.config_manager = config_manager
        self.priority_matrix = PriorityMatrix()
        self.round_robin = WeightedRoundRobin()
        
        # Provider instances cache
        self.provider_instances: Dict[str, BaseProvider] = {}
        
        # Load balancing strategy
        self.load_balancing_strategy = LoadBalancingStrategy.PRIORITY_ONLY
        
        # Health monitoring
        self.health_check_interval = 300  # 5 minutes
        self.last_health_check = datetime.now()
        
        logger.info("Fallback manager initialized")
    
    async def get_provider_priority_list(self) -> List[ProviderPriority]:
        """
        Get providers in priority order.
        
        Returns:
            List of providers sorted by current strategy
        """
        return self.priority_matrix.get_sorted_providers(self.load_balancing_strategy)
    
    async def get_provider(self, provider_name: str) -> Optional[BaseProvider]:
        """
        Get provider instance.
        
        Args:
            provider_name: Name of the provider
            
        Returns:
            Provider instance or None if not available
        """
        try:
            # Check if already cached
            if provider_name in self.provider_instances:
                return self.provider_instances[provider_name]
            
            # Get configuration
            config = self.config_manager.get_provider_config(provider_name)
            
            # Create provider instance
            provider_type = ProviderType(provider_name)
            provider = ProviderFactory.create_provider(provider_type, config)
            
            # Cache instance
            self.provider_instances[provider_name] = provider
            
            logger.debug(f"Created provider instance for {provider_name}")
            return provider
            
        except Exception as e:
            logger.error(f"Failed to get provider {provider_name}: {e}")
            return None
    
    async def get_all_providers(self) -> Dict[str, BaseProvider]:
        """
        Get all available provider instances.
        
        Returns:
            Dictionary mapping provider names to instances
        """
        providers = {}
        enabled_providers = self.config_manager.get_enabled_providers()
        
        for provider_name in enabled_providers:
            provider = await self.get_provider(provider_name)
            if provider:
                providers[provider_name] = provider
        
        return providers
    
    async def select_optimal_provider(self) -> Optional[BaseProvider]:
        """
        Select optimal provider based on current strategy.
        
        Returns:
            Selected provider instance or None if no providers available
        """
        providers = await self.get_provider_priority_list()
        
        if not providers:
            logger.warning("No providers available for selection")
            return None
        
        if self.load_balancing_strategy == LoadBalancingStrategy.WEIGHTED_ROUND_ROBIN:
            selected_priority = self.round_robin.select_provider(providers)
        else:
            # Use first provider (highest priority)
            selected_priority = providers[0]
        
        if selected_priority:
            provider = await self.get_provider(selected_priority.provider_name)
            if provider:
                # Update connection count
                health = self.priority_matrix.health_data.get(
                    selected_priority.provider_name,
                    ProviderHealth(selected_priority.provider_name)
                )
                health.current_connections += 1
                self.priority_matrix.update_health_data(selected_priority.provider_name, health)
                
                logger.debug(f"Selected provider: {selected_priority.provider_name}")
                return provider
        
        return None
    
    async def record_provider_success(self, provider_name: str, response_time: float):
        """
        Record successful provider interaction.
        
        Args:
            provider_name: Name of the provider
            response_time: Response time in seconds
        """
        health = self.priority_matrix.health_data.get(
            provider_name,
            ProviderHealth(provider_name)
        )
        
        health.success_count += 1
        health.last_success = datetime.now()
        health.is_healthy = True
        health.current_connections = max(0, health.current_connections - 1)
        
        # Update response times (keep last 100)
        health.response_times.append(response_time)
        if len(health.response_times) > 100:
            health.response_times = health.response_times[-100:]
        
        self.priority_matrix.update_health_data(provider_name, health)
        
        # Update priority based on performance
        await self._update_dynamic_priority(provider_name, health)
        
        logger.debug(f"Recorded success for {provider_name}: {response_time:.2f}s")
    
    async def record_provider_failure(self, provider_name: str, error_message: str):
        """
        Record provider failure.
        
        Args:
            provider_name: Name of the provider
            error_message: Error message
        """
        health = self.priority_matrix.health_data.get(
            provider_name,
            ProviderHealth(provider_name)
        )
        
        health.failure_count += 1
        health.last_failure = datetime.now()
        health.current_connections = max(0, health.current_connections - 1)
        
        # Mark as unhealthy if too many consecutive failures
        if health.failure_count >= 5:
            health.is_healthy = False
        
        self.priority_matrix.update_health_data(provider_name, health)
        
        logger.warning(f"Recorded failure for {provider_name}: {error_message}")
    
    async def _update_dynamic_priority(self, provider_name: str, health: ProviderHealth):
        """
        Update provider priority based on performance.
        
        Args:
            provider_name: Name of the provider
            health: Current health data
        """
        if provider_name not in self.priority_matrix.providers:
            return
        
        priority = self.priority_matrix.providers[provider_name]
        
        # Update reliability factor based on success rate
        priority.reliability_factor = health.success_rate
        
        # Update speed factor based on response time
        avg_response_time = health.average_response_time
        if avg_response_time > 0:
            # Normalize speed factor (faster = higher factor)
            baseline_time = 2.0  # 2 seconds baseline
            priority.speed_factor = min(2.0, baseline_time / avg_response_time)
        
        self.priority_matrix.update_provider_priority(provider_name, priority)
    
    async def health_check_all_providers(self) -> Dict[str, bool]:
        """
        Perform health check on all providers.
        
        Returns:
            Dictionary mapping provider names to health status
        """
        if (datetime.now() - self.last_health_check).total_seconds() < self.health_check_interval:
            # Use cached health data
            return {
                name: health.is_healthy
                for name, health in self.priority_matrix.health_data.items()
            }
        
        providers = await self.get_all_providers()
        health_results = {}
        
        for provider_name, provider in providers.items():
            try:
                is_healthy = await provider.health_check()
                health_results[provider_name] = is_healthy
                
                # Update health data
                health = self.priority_matrix.health_data.get(
                    provider_name,
                    ProviderHealth(provider_name)
                )
                health.is_healthy = is_healthy
                health.last_check = datetime.now()
                
                if is_healthy:
                    await self.record_provider_success(provider_name, 0.1)
                else:
                    await self.record_provider_failure(provider_name, "Health check failed")
                
            except Exception as e:
                health_results[provider_name] = False
                await self.record_provider_failure(provider_name, f"Health check error: {e}")
        
        self.last_health_check = datetime.now()
        return health_results
    
    def set_load_balancing_strategy(self, strategy: LoadBalancingStrategy):
        """
        Set load balancing strategy.
        
        Args:
            strategy: Load balancing strategy to use
        """
        self.load_balancing_strategy = strategy
        logger.info(f"Load balancing strategy set to: {strategy.value}")
    
    def get_provider_stats(self) -> Dict[str, Dict[str, Any]]:
        """
        Get statistics for all providers.
        
        Returns:
            Dictionary with provider statistics
        """
        stats = {}
        
        for provider_name, health in self.priority_matrix.health_data.items():
            priority = self.priority_matrix.providers.get(provider_name)
            
            stats[provider_name] = {
                'is_healthy': health.is_healthy,
                'success_rate': health.success_rate,
                'failure_count': health.failure_count,
                'success_count': health.success_count,
                'average_response_time': health.average_response_time,
                'current_connections': health.current_connections,
                'last_success': health.last_success.isoformat() if health.last_success else None,
                'last_failure': health.last_failure.isoformat() if health.last_failure else None,
                'priority': priority.priority if priority else None,
                'composite_score': priority.composite_score if priority else None,
            }
        
        return stats
    
    def clear_provider_cache(self):
        """Clear provider instance cache."""
        self.provider_instances.clear()
        logger.info("Provider cache cleared")