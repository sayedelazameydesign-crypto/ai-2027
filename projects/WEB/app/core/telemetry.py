"""
Telemetry and Metrics Collection System

This module implements comprehensive telemetry collection using Prometheus
metrics for monitoring AI provider performance and system health.
"""

import time
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from collections import defaultdict, deque
import threading
import logging

try:
    from prometheus_client import Counter, Histogram, Gauge, Info, CollectorRegistry, generate_latest
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    logging.warning("Prometheus client not available. Metrics will be collected but not exported.")


logger = logging.getLogger(__name__)


@dataclass
class RequestMetrics:
    """Metrics for individual requests."""
    request_id: str
    start_time: datetime
    user_id: Optional[str] = None
    request_type: str = "completion"  # completion, streaming, health_check
    provider_used: Optional[str] = None
    response_time: Optional[float] = None
    tokens_used: int = 0
    success: bool = False
    error_message: Optional[str] = None
    model_used: Optional[str] = None
    cost_estimate: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert metrics to dictionary."""
        return {
            'request_id': self.request_id,
            'start_time': self.start_time.isoformat(),
            'user_id': self.user_id,
            'request_type': self.request_type,
            'provider_used': self.provider_used,
            'response_time': self.response_time,
            'tokens_used': self.tokens_used,
            'success': self.success,
            'error_message': self.error_message,
            'model_used': self.model_used,
            'cost_estimate': self.cost_estimate
        }


@dataclass
class ProviderMetrics:
    """Aggregated metrics for a provider."""
    provider_name: str
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    total_tokens: int = 0
    total_response_time: float = 0.0
    total_cost: float = 0.0
    last_success: Optional[datetime] = None
    last_failure: Optional[datetime] = None
    recent_response_times: deque = field(default_factory=lambda: deque(maxlen=100))
    recent_errors: deque = field(default_factory=lambda: deque(maxlen=50))
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate."""
        if self.total_requests == 0:
            return 0.0
        return self.successful_requests / self.total_requests
    
    @property
    def failure_rate(self) -> float:
        """Calculate failure rate."""
        return 1.0 - self.success_rate
    
    @property
    def average_response_time(self) -> float:
        """Calculate average response time."""
        if self.successful_requests == 0:
            return 0.0
        return self.total_response_time / self.successful_requests
    
    @property
    def recent_average_response_time(self) -> float:
        """Calculate recent average response time."""
        if not self.recent_response_times:
            return 0.0
        return sum(self.recent_response_times) / len(self.recent_response_times)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert metrics to dictionary."""
        return {
            'provider_name': self.provider_name,
            'total_requests': self.total_requests,
            'successful_requests': self.successful_requests,
            'failed_requests': self.failed_requests,
            'success_rate': self.success_rate,
            'failure_rate': self.failure_rate,
            'total_tokens': self.total_tokens,
            'total_response_time': self.total_response_time,
            'average_response_time': self.average_response_time,
            'recent_average_response_time': self.recent_average_response_time,
            'total_cost': self.total_cost,
            'last_success': self.last_success.isoformat() if self.last_success else None,
            'last_failure': self.last_failure.isoformat() if self.last_failure else None,
            'recent_errors': list(self.recent_errors)
        }


class PrometheusMetrics:
    """Prometheus metrics collector."""
    
    def __init__(self, registry: Optional[CollectorRegistry] = None):
        """
        Initialize Prometheus metrics.
        
        Args:
            registry: Optional custom registry
        """
        self.registry = registry or CollectorRegistry()
        
        # Request counters
        self.requests_total = Counter(
            'ai_provider_requests_total',
            'Total number of requests',
            ['provider', 'request_type', 'status'],
            registry=self.registry
        )
        
        # Token consumption
        self.tokens_consumed_total = Counter(
            'ai_provider_tokens_consumed_total',
            'Total tokens consumed',
            ['provider', 'model'],
            registry=self.registry
        )
        
        # Response time histogram
        self.request_latency_seconds = Histogram(
            'ai_provider_request_latency_seconds',
            'Request latency in seconds',
            ['provider', 'request_type'],
            buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
            registry=self.registry
        )
        
        # Provider failure ratio
        self.provider_failure_ratio = Gauge(
            'ai_provider_failure_ratio',
            'Provider failure ratio (0-1)',
            ['provider'],
            registry=self.registry
        )
        
        # Active requests
        self.active_requests = Gauge(
            'ai_provider_active_requests',
            'Number of active requests',
            ['provider'],
            registry=self.registry
        )
        
        # Cost tracking
        self.cost_total = Counter(
            'ai_provider_cost_total',
            'Total estimated cost',
            ['provider', 'model'],
            registry=self.registry
        )
        
        # System info
        self.system_info = Info(
            'ai_provider_system_info',
            'System information',
            registry=self.registry
        )
        
        logger.info("Prometheus metrics initialized")
    
    def record_request(self, metrics: RequestMetrics):
        """Record request metrics."""
        if not metrics.provider_used:
            return
        
        # Record request
        status = 'success' if metrics.success else 'failure'
        self.requests_total.labels(
            provider=metrics.provider_used,
            request_type=metrics.request_type,
            status=status
        ).inc()
        
        # Record tokens
        if metrics.tokens_used > 0:
            self.tokens_consumed_total.labels(
                provider=metrics.provider_used,
                model=metrics.model_used or 'unknown'
            ).inc(metrics.tokens_used)
        
        # Record latency
        if metrics.response_time is not None:
            self.request_latency_seconds.labels(
                provider=metrics.provider_used,
                request_type=metrics.request_type
            ).observe(metrics.response_time)
        
        # Record cost
        if metrics.cost_estimate is not None:
            self.cost_total.labels(
                provider=metrics.provider_used,
                model=metrics.model_used or 'unknown'
            ).inc(metrics.cost_estimate)
    
    def update_provider_failure_ratio(self, provider: str, ratio: float):
        """Update provider failure ratio."""
        self.provider_failure_ratio.labels(provider=provider).set(ratio)
    
    def update_active_requests(self, provider: str, count: int):
        """Update active requests count."""
        self.active_requests.labels(provider=provider).set(count)
    
    def get_metrics(self) -> str:
        """Get metrics in Prometheus format."""
        return generate_latest(self.registry).decode('utf-8')


class TelemetryManager:
    """
    Comprehensive telemetry and metrics management.
    
    Collects, aggregates, and exports metrics for AI provider performance
    monitoring and system observability.
    """
    
    def __init__(self, enable_prometheus: bool = True):
        """
        Initialize telemetry manager.
        
        Args:
            enable_prometheus: Whether to enable Prometheus metrics
        """
        self.enable_prometheus = enable_prometheus and PROMETHEUS_AVAILABLE
        
        # Metrics storage
        self.provider_metrics: Dict[str, ProviderMetrics] = defaultdict(
            lambda: ProviderMetrics(provider_name="")
        )
        self.recent_requests: deque = deque(maxlen=1000)
        
        # Thread safety
        self._lock = threading.RLock()
        
        # Prometheus metrics
        if self.enable_prometheus:
            self.prometheus = PrometheusMetrics()
        else:
            self.prometheus = None
        
        logger.info(f"Telemetry manager initialized (Prometheus: {self.enable_prometheus})")
    
    def record_request(self, metrics: RequestMetrics):
        """
        Record request metrics.
        
        Args:
            metrics: Request metrics to record
        """
        with self._lock:
            # Store recent request
            self.recent_requests.append(metrics)
            
            # Update provider metrics
            if metrics.provider_used:
                provider_metrics = self.provider_metrics[metrics.provider_used]
                provider_metrics.provider_name = metrics.provider_used
                provider_metrics.total_requests += 1
                
                if metrics.success:
                    provider_metrics.successful_requests += 1
                    provider_metrics.last_success = datetime.now()
                    
                    if metrics.response_time is not None:
                        provider_metrics.total_response_time += metrics.response_time
                        provider_metrics.recent_response_times.append(metrics.response_time)
                else:
                    provider_metrics.failed_requests += 1
                    provider_metrics.last_failure = datetime.now()
                    
                    if metrics.error_message:
                        provider_metrics.recent_errors.append({
                            'timestamp': datetime.now().isoformat(),
                            'error': metrics.error_message
                        })
                
                # Update tokens and cost
                provider_metrics.total_tokens += metrics.tokens_used
                if metrics.cost_estimate:
                    provider_metrics.total_cost += metrics.cost_estimate
                
                # Update Prometheus metrics
                if self.prometheus:
                    self.prometheus.record_request(metrics)
                    self.prometheus.update_provider_failure_ratio(
                        metrics.provider_used,
                        provider_metrics.failure_rate
                    )
        
        logger.debug(f"Recorded metrics for request {metrics.request_id}")
    
    def get_provider_stats(self) -> Dict[str, Dict[str, Any]]:
        """
        Get statistics for all providers.
        
        Returns:
            Dictionary mapping provider names to their statistics
        """
        with self._lock:
            return {
                name: metrics.to_dict()
                for name, metrics in self.provider_metrics.items()
            }
    
    def get_provider_metrics(self, provider_name: str) -> Optional[ProviderMetrics]:
        """
        Get metrics for specific provider.
        
        Args:
            provider_name: Name of the provider
            
        Returns:
            Provider metrics or None if not found
        """
        with self._lock:
            return self.provider_metrics.get(provider_name)
    
    def get_system_stats(self) -> Dict[str, Any]:
        """
        Get overall system statistics.
        
        Returns:
            Dictionary with system-wide statistics
        """
        with self._lock:
            total_requests = sum(m.total_requests for m in self.provider_metrics.values())
            total_successful = sum(m.successful_requests for m in self.provider_metrics.values())
            total_tokens = sum(m.total_tokens for m in self.provider_metrics.values())
            total_cost = sum(m.total_cost for m in self.provider_metrics.values())
            
            return {
                'total_requests': total_requests,
                'total_successful_requests': total_successful,
                'overall_success_rate': total_successful / total_requests if total_requests > 0 else 0.0,
                'total_tokens_consumed': total_tokens,
                'total_estimated_cost': total_cost,
                'active_providers': len(self.provider_metrics),
                'recent_requests_count': len(self.recent_requests),
                'uptime_seconds': (datetime.now() - self._start_time).total_seconds() if hasattr(self, '_start_time') else 0
            }
    
    def get_recent_requests(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get recent requests.
        
        Args:
            limit: Maximum number of requests to return
            
        Returns:
            List of recent request metrics
        """
        with self._lock:
            recent = list(self.recent_requests)[-limit:]
            return [req.to_dict() for req in recent]
    
    def get_total_requests(self) -> int:
        """Get total number of requests processed."""
        with self._lock:
            return sum(m.total_requests for m in self.provider_metrics.values())
    
    def get_success_rate(self) -> float:
        """Get overall success rate."""
        with self._lock:
            total_requests = sum(m.total_requests for m in self.provider_metrics.values())
            total_successful = sum(m.successful_requests for m in self.provider_metrics.values())
            
            if total_requests == 0:
                return 0.0
            return total_successful / total_requests
    
    def get_average_response_time(self) -> float:
        """Get overall average response time."""
        with self._lock:
            total_time = sum(m.total_response_time for m in self.provider_metrics.values())
            total_successful = sum(m.successful_requests for m in self.provider_metrics.values())
            
            if total_successful == 0:
                return 0.0
            return total_time / total_successful
    
    def get_prometheus_metrics(self) -> Optional[str]:
        """
        Get Prometheus metrics.
        
        Returns:
            Prometheus metrics string or None if not enabled
        """
        if self.prometheus:
            return self.prometheus.get_metrics()
        return None
    
    def reset_metrics(self):
        """Reset all metrics."""
        with self._lock:
            self.provider_metrics.clear()
            self.recent_requests.clear()
            logger.info("All metrics reset")
    
    def export_metrics(self, format: str = 'json') -> str:
        """
        Export metrics in specified format.
        
        Args:
            format: Export format ('json' or 'prometheus')
            
        Returns:
            Formatted metrics string
        """
        if format == 'prometheus' and self.prometheus:
            return self.prometheus.get_metrics()
        elif format == 'json':
            import json
            return json.dumps({
                'system_stats': self.get_system_stats(),
                'provider_stats': self.get_provider_stats(),
                'recent_requests': self.get_recent_requests(50)
            }, indent=2)
        else:
            raise ValueError(f"Unsupported export format: {format}")


# Global telemetry manager instance
telemetry_manager = TelemetryManager()

# Set start time
telemetry_manager._start_time = datetime.now()