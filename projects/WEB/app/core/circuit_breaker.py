"""
Circuit Breaker Pattern Implementation

This module implements the Circuit Breaker pattern to prevent cascading failures
and provide automatic recovery for AI providers.
"""

import asyncio
import time
from enum import Enum
from typing import Dict, Optional, Callable, Any
from datetime import datetime, timedelta
import logging
from dataclasses import dataclass

from ..providers.base import ProviderError


logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Circuit is open, requests fail fast
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""
    failure_threshold: int = 5          # Number of failures to open circuit
    recovery_timeout: int = 60          # Seconds to wait before trying half-open
    success_threshold: int = 3          # Successes needed to close from half-open
    timeout: float = 30.0               # Request timeout in seconds
    
    # Advanced settings
    failure_rate_threshold: float = 0.5  # 50% failure rate to open
    minimum_requests: int = 10           # Minimum requests before calculating failure rate
    sliding_window_size: int = 100       # Size of sliding window for failure rate


class CircuitBreakerStats:
    """Statistics tracking for circuit breaker."""
    
    def __init__(self, window_size: int = 100):
        self.window_size = window_size
        self.requests = []  # List of (timestamp, success) tuples
        self.total_requests = 0
        self.total_failures = 0
        self.consecutive_failures = 0
        self.consecutive_successes = 0
        self.last_failure_time: Optional[datetime] = None
        self.last_success_time: Optional[datetime] = None
    
    def record_success(self):
        """Record a successful request."""
        now = datetime.now()
        self.requests.append((now, True))
        self._cleanup_old_requests()
        
        self.total_requests += 1
        self.consecutive_successes += 1
        self.consecutive_failures = 0
        self.last_success_time = now
        
        logger.debug(f"Circuit breaker: Success recorded. Consecutive: {self.consecutive_successes}")
    
    def record_failure(self):
        """Record a failed request."""
        now = datetime.now()
        self.requests.append((now, False))
        self._cleanup_old_requests()
        
        self.total_requests += 1
        self.total_failures += 1
        self.consecutive_failures += 1
        self.consecutive_successes = 0
        self.last_failure_time = now
        
        logger.debug(f"Circuit breaker: Failure recorded. Consecutive: {self.consecutive_failures}")
    
    def get_failure_rate(self) -> float:
        """Calculate current failure rate in sliding window."""
        if not self.requests:
            return 0.0
        
        failures = sum(1 for _, success in self.requests if not success)
        return failures / len(self.requests)
    
    def get_request_count(self) -> int:
        """Get current request count in sliding window."""
        return len(self.requests)
    
    def _cleanup_old_requests(self):
        """Remove old requests outside sliding window."""
        # Keep only the most recent requests
        if len(self.requests) > self.window_size:
            self.requests = self.requests[-self.window_size:]


class CircuitBreaker:
    """
    Circuit Breaker implementation for provider resilience.
    
    Implements the Circuit Breaker pattern with three states:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Circuit is open, requests fail fast
    - HALF_OPEN: Testing if service recovered
    """
    
    def __init__(self, name: str, config: CircuitBreakerConfig):
        """
        Initialize circuit breaker.
        
        Args:
            name: Name of the circuit (usually provider name)
            config: Circuit breaker configuration
        """
        self.name = name
        self.config = config
        self.state = CircuitState.CLOSED
        self.stats = CircuitBreakerStats(config.sliding_window_size)
        self.last_failure_time: Optional[datetime] = None
        self.state_change_time = datetime.now()
        
        logger.info(f"Circuit breaker initialized for {name}")
    
    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute function through circuit breaker.
        
        Args:
            func: Function to execute
            *args: Function arguments
            **kwargs: Function keyword arguments
            
        Returns:
            Function result
            
        Raises:
            ProviderError: If circuit is open or function fails
        """
        # Check if circuit should be opened
        self._update_state()
        
        if self.state == CircuitState.OPEN:
            raise ProviderError(
                f"Circuit breaker is OPEN for {self.name}. "
                f"Last failure: {self.last_failure_time}",
                self.name,
                retryable=False
            )
        
        try:
            # Execute function with timeout
            result = await asyncio.wait_for(
                func(*args, **kwargs),
                timeout=self.config.timeout
            )
            
            # Record success
            self.stats.record_success()
            
            # If we're in half-open state and got enough successes, close circuit
            if (self.state == CircuitState.HALF_OPEN and 
                self.stats.consecutive_successes >= self.config.success_threshold):
                self._close_circuit()
            
            return result
            
        except Exception as e:
            # Record failure
            self.stats.record_failure()
            self.last_failure_time = datetime.now()
            
            # Check if we should open the circuit
            self._update_state()
            
            # Re-raise the original exception
            if isinstance(e, ProviderError):
                raise
            else:
                raise ProviderError(f"Circuit breaker call failed: {e}", self.name)
    
    def _update_state(self):
        """Update circuit breaker state based on current conditions."""
        current_time = datetime.now()
        
        if self.state == CircuitState.CLOSED:
            # Check if we should open the circuit
            should_open = False
            
            # Check consecutive failures threshold
            if self.stats.consecutive_failures >= self.config.failure_threshold:
                should_open = True
                logger.warning(
                    f"Circuit breaker opening due to consecutive failures: "
                    f"{self.stats.consecutive_failures} >= {self.config.failure_threshold}"
                )
            
            # Check failure rate threshold (if we have enough requests)
            elif (self.stats.get_request_count() >= self.config.minimum_requests and
                  self.stats.get_failure_rate() >= self.config.failure_rate_threshold):
                should_open = True
                logger.warning(
                    f"Circuit breaker opening due to failure rate: "
                    f"{self.stats.get_failure_rate():.2%} >= {self.config.failure_rate_threshold:.2%}"
                )
            
            if should_open:
                self._open_circuit()
        
        elif self.state == CircuitState.OPEN:
            # Check if we should try half-open
            time_since_open = current_time - self.state_change_time
            if time_since_open.total_seconds() >= self.config.recovery_timeout:
                self._half_open_circuit()
    
    def _open_circuit(self):
        """Open the circuit breaker."""
        self.state = CircuitState.OPEN
        self.state_change_time = datetime.now()
        logger.warning(f"Circuit breaker OPENED for {self.name}")
    
    def _half_open_circuit(self):
        """Set circuit breaker to half-open state."""
        self.state = CircuitState.HALF_OPEN
        self.state_change_time = datetime.now()
        logger.info(f"Circuit breaker HALF-OPEN for {self.name}")
    
    def _close_circuit(self):
        """Close the circuit breaker."""
        self.state = CircuitState.CLOSED
        self.state_change_time = datetime.now()
        logger.info(f"Circuit breaker CLOSED for {self.name}")
    
    def force_open(self):
        """Manually force circuit breaker to open state."""
        self._open_circuit()
        logger.warning(f"Circuit breaker manually OPENED for {self.name}")
    
    def force_close(self):
        """Manually force circuit breaker to closed state."""
        self._close_circuit()
        # Reset stats
        self.stats.consecutive_failures = 0
        self.stats.consecutive_successes = 0
        logger.info(f"Circuit breaker manually CLOSED for {self.name}")
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get circuit breaker statistics.
        
        Returns:
            Dictionary with circuit breaker stats
        """
        return {
            'name': self.name,
            'state': self.state.value,
            'total_requests': self.stats.total_requests,
            'total_failures': self.stats.total_failures,
            'consecutive_failures': self.stats.consecutive_failures,
            'consecutive_successes': self.stats.consecutive_successes,
            'failure_rate': self.stats.get_failure_rate(),
            'request_count_window': self.stats.get_request_count(),
            'last_failure_time': self.last_failure_time.isoformat() if self.last_failure_time else None,
            'last_success_time': self.stats.last_success_time.isoformat() if self.stats.last_success_time else None,
            'state_change_time': self.state_change_time.isoformat(),
            'config': {
                'failure_threshold': self.config.failure_threshold,
                'recovery_timeout': self.config.recovery_timeout,
                'success_threshold': self.config.success_threshold,
                'failure_rate_threshold': self.config.failure_rate_threshold,
                'minimum_requests': self.config.minimum_requests,
            }
        }
    
    def is_available(self) -> bool:
        """
        Check if circuit breaker allows requests.
        
        Returns:
            True if requests are allowed, False otherwise
        """
        self._update_state()
        return self.state != CircuitState.OPEN
    
    def __str__(self) -> str:
        """String representation of circuit breaker."""
        return f"CircuitBreaker({self.name}, {self.state.value})"
    
    def __repr__(self) -> str:
        """Detailed string representation of circuit breaker."""
        return (
            f"CircuitBreaker("
            f"name={self.name}, "
            f"state={self.state.value}, "
            f"failures={self.stats.consecutive_failures}, "
            f"successes={self.stats.consecutive_successes}"
            f")"
        )


class CircuitBreakerManager:
    """
    Manager for multiple circuit breakers.
    
    Provides centralized management and monitoring of circuit breakers
    for different providers.
    """
    
    def __init__(self):
        """Initialize circuit breaker manager."""
        self.circuit_breakers: Dict[str, CircuitBreaker] = {}
        self.default_config = CircuitBreakerConfig()
        
        logger.info("Circuit breaker manager initialized")
    
    def get_circuit_breaker(self, name: str, config: Optional[CircuitBreakerConfig] = None) -> CircuitBreaker:
        """
        Get or create circuit breaker for a provider.
        
        Args:
            name: Provider name
            config: Optional custom configuration
            
        Returns:
            Circuit breaker instance
        """
        if name not in self.circuit_breakers:
            breaker_config = config or self.default_config
            self.circuit_breakers[name] = CircuitBreaker(name, breaker_config)
            logger.info(f"Created circuit breaker for {name}")
        
        return self.circuit_breakers[name]
    
    def remove_circuit_breaker(self, name: str):
        """
        Remove circuit breaker for a provider.
        
        Args:
            name: Provider name
        """
        if name in self.circuit_breakers:
            del self.circuit_breakers[name]
            logger.info(f"Removed circuit breaker for {name}")
    
    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """
        Get statistics for all circuit breakers.
        
        Returns:
            Dictionary mapping provider names to their stats
        """
        return {
            name: breaker.get_stats()
            for name, breaker in self.circuit_breakers.items()
        }
    
    def get_available_providers(self) -> list[str]:
        """
        Get list of providers with available (non-open) circuit breakers.
        
        Returns:
            List of available provider names
        """
        return [
            name for name, breaker in self.circuit_breakers.items()
            if breaker.is_available()
        ]
    
    def force_open_all(self):
        """Force all circuit breakers to open state."""
        for breaker in self.circuit_breakers.values():
            breaker.force_open()
        logger.warning("All circuit breakers forced OPEN")
    
    def force_close_all(self):
        """Force all circuit breakers to closed state."""
        for breaker in self.circuit_breakers.values():
            breaker.force_close()
        logger.info("All circuit breakers forced CLOSED")
    
    def update_config(self, name: str, config: CircuitBreakerConfig):
        """
        Update configuration for a specific circuit breaker.
        
        Args:
            name: Provider name
            config: New configuration
        """
        if name in self.circuit_breakers:
            # Remove old breaker and create new one with updated config
            self.remove_circuit_breaker(name)
            self.get_circuit_breaker(name, config)
            logger.info(f"Updated circuit breaker configuration for {name}")
    
    def __len__(self) -> int:
        """Return number of managed circuit breakers."""
        return len(self.circuit_breakers)
    
    def __contains__(self, name: str) -> bool:
        """Check if circuit breaker exists for provider."""
        return name in self.circuit_breakers
    
    def __iter__(self):
        """Iterate over circuit breaker names."""
        return iter(self.circuit_breakers.keys())


# Global circuit breaker manager instance
circuit_breaker_manager = CircuitBreakerManager()