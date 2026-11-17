"""
Dynamic Cost Capping and Budget Management

This module implements intelligent cost management with per-user budgets,
dynamic cost capping, and usage analytics to prevent API key abuse.
"""

import asyncio
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import logging
import json

logger = logging.getLogger(__name__)


class BudgetPeriod(Enum):
    """Budget period types."""
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"


class AlertLevel(Enum):
    """Alert levels for budget notifications."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


@dataclass
class CostEstimate:
    """Cost estimation for a request."""
    provider: str
    model: str
    input_tokens: int
    estimated_output_tokens: int
    input_cost: float
    output_cost: float
    total_cost: float
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'provider': self.provider,
            'model': self.model,
            'input_tokens': self.input_tokens,
            'estimated_output_tokens': self.estimated_output_tokens,
            'input_cost': self.input_cost,
            'output_cost': self.output_cost,
            'total_cost': self.total_cost
        }


@dataclass
class BudgetLimit:
    """Budget limit configuration."""
    user_id: str
    period: BudgetPeriod
    limit_amount: float
    current_usage: float = 0.0
    period_start: datetime = field(default_factory=datetime.now)
    alert_thresholds: Dict[AlertLevel, float] = field(default_factory=lambda: {
        AlertLevel.WARNING: 0.75,
        AlertLevel.CRITICAL: 0.90,
        AlertLevel.EMERGENCY: 0.95
    })
    enabled: bool = True
    
    @property
    def usage_percentage(self) -> float:
        """Calculate usage percentage."""
        if self.limit_amount <= 0:
            return 0.0
        return self.current_usage / self.limit_amount
    
    @property
    def remaining_budget(self) -> float:
        """Calculate remaining budget."""
        return max(0.0, self.limit_amount - self.current_usage)
    
    def is_exceeded(self) -> bool:
        """Check if budget is exceeded."""
        return self.current_usage >= self.limit_amount
    
    def get_alert_level(self) -> Optional[AlertLevel]:
        """Get current alert level based on usage."""
        usage_pct = self.usage_percentage
        
        for level in [AlertLevel.EMERGENCY, AlertLevel.CRITICAL, AlertLevel.WARNING]:
            if usage_pct >= self.alert_thresholds.get(level, 1.0):
                return level
        
        return None
    
    def should_reset(self) -> bool:
        """Check if budget period should reset."""
        now = datetime.now()
        
        if self.period == BudgetPeriod.HOURLY:
            return now - self.period_start >= timedelta(hours=1)
        elif self.period == BudgetPeriod.DAILY:
            return now - self.period_start >= timedelta(days=1)
        elif self.period == BudgetPeriod.WEEKLY:
            return now - self.period_start >= timedelta(weeks=1)
        elif self.period == BudgetPeriod.MONTHLY:
            return now - self.period_start >= timedelta(days=30)
        elif self.period == BudgetPeriod.YEARLY:
            return now - self.period_start >= timedelta(days=365)
        
        return False
    
    def reset_period(self):
        """Reset budget period."""
        self.current_usage = 0.0
        self.period_start = datetime.now()


@dataclass
class UsageRecord:
    """Record of API usage."""
    timestamp: datetime
    user_id: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost: float
    request_id: str
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'timestamp': self.timestamp.isoformat(),
            'user_id': self.user_id,
            'provider': self.provider,
            'model': self.model,
            'input_tokens': self.input_tokens,
            'output_tokens': self.output_tokens,
            'cost': self.cost,
            'request_id': self.request_id
        }


class CostCalculator:
    """Cost calculation engine for different providers."""
    
    def __init__(self):
        """Initialize cost calculator."""
        # Cost per 1K tokens (input, output) for different providers/models
        self.pricing_table = {
            'openai': {
                'gpt-4': (0.03, 0.06),
                'gpt-4-turbo-preview': (0.01, 0.03),
                'gpt-3.5-turbo': (0.0015, 0.002),
                'gpt-3.5-turbo-16k': (0.003, 0.004)
            },
            'anthropic': {
                'claude-3-5-sonnet-20241022': (0.003, 0.015),
                'claude-3-sonnet-20240229': (0.003, 0.015),
                'claude-3-haiku-20240307': (0.00025, 0.00125),
                'claude-3-opus-20240229': (0.015, 0.075)
            },
            'google': {
                'gemini-pro': (0.0005, 0.0015),
                'gemini-pro-vision': (0.0005, 0.0015)
            },
            'groq': {
                'mixtral-8x7b-32768': (0.00027, 0.00027),
                'llama2-70b-4096': (0.0007, 0.0008)
            },
            'deepseek': {
                'deepseek-chat': (0.00014, 0.00028),
                'deepseek-coder': (0.00014, 0.00028)
            },
            'huggingface': {
                'default': (0.0001, 0.0002)  # Estimated
            },
            'openrouter': {
                'openai/gpt-3.5-turbo': (0.0015, 0.002),
                'anthropic/claude-2': (0.008, 0.024)
            }
        }
        
        logger.info("Cost calculator initialized with pricing for multiple providers")
    
    def estimate_cost(self, provider: str, model: str, input_tokens: int, 
                     estimated_output_tokens: int) -> CostEstimate:
        """
        Estimate cost for a request.
        
        Args:
            provider: Provider name
            model: Model name
            input_tokens: Number of input tokens
            estimated_output_tokens: Estimated output tokens
            
        Returns:
            Cost estimate
        """
        # Get pricing for provider/model
        provider_pricing = self.pricing_table.get(provider, {})
        
        if model in provider_pricing:
            input_price_per_1k, output_price_per_1k = provider_pricing[model]
        else:
            # Use default pricing if model not found
            default_pricing = provider_pricing.get('default', (0.001, 0.002))
            input_price_per_1k, output_price_per_1k = default_pricing
        
        # Calculate costs
        input_cost = (input_tokens / 1000) * input_price_per_1k
        output_cost = (estimated_output_tokens / 1000) * output_price_per_1k
        total_cost = input_cost + output_cost
        
        return CostEstimate(
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            estimated_output_tokens=estimated_output_tokens,
            input_cost=input_cost,
            output_cost=output_cost,
            total_cost=total_cost
        )
    
    def calculate_actual_cost(self, provider: str, model: str, 
                            input_tokens: int, output_tokens: int) -> float:
        """Calculate actual cost after request completion."""
        estimate = self.estimate_cost(provider, model, input_tokens, output_tokens)
        return estimate.input_cost + (output_tokens / 1000) * self.pricing_table.get(provider, {}).get(model, (0, 0.002))[1]
    
    def update_pricing(self, provider: str, model: str, input_price: float, output_price: float):
        """Update pricing for a provider/model."""
        if provider not in self.pricing_table:
            self.pricing_table[provider] = {}
        
        self.pricing_table[provider][model] = (input_price, output_price)
        logger.info(f"Updated pricing for {provider}/{model}: ${input_price:.6f}/${output_price:.6f} per 1K tokens")


class BudgetManager:
    """Budget management and enforcement."""
    
    def __init__(self):
        """Initialize budget manager."""
        self.user_budgets: Dict[str, List[BudgetLimit]] = {}
        self.usage_history: List[UsageRecord] = []
        self.cost_calculator = CostCalculator()
        
        # Global emergency limits
        self.global_limits = {
            BudgetPeriod.HOURLY: 100.0,   # $100/hour
            BudgetPeriod.DAILY: 1000.0,   # $1000/day
            BudgetPeriod.MONTHLY: 10000.0  # $10000/month
        }
        
        logger.info("Budget manager initialized")
    
    def set_user_budget(self, user_id: str, period: BudgetPeriod, 
                       limit_amount: float, alert_thresholds: Optional[Dict[AlertLevel, float]] = None):
        """
        Set budget limit for a user.
        
        Args:
            user_id: User identifier
            period: Budget period
            limit_amount: Budget limit amount
            alert_thresholds: Custom alert thresholds
        """
        if user_id not in self.user_budgets:
            self.user_budgets[user_id] = []
        
        # Remove existing budget for same period
        self.user_budgets[user_id] = [
            budget for budget in self.user_budgets[user_id]
            if budget.period != period
        ]
        
        # Create new budget
        budget = BudgetLimit(
            user_id=user_id,
            period=period,
            limit_amount=limit_amount,
            alert_thresholds=alert_thresholds or {}
        )
        
        self.user_budgets[user_id].append(budget)
        
        logger.info(f"Set {period.value} budget for user {user_id}: ${limit_amount:.2f}")
    
    def check_budget_availability(self, user_id: str, estimated_cost: float) -> Tuple[bool, List[str]]:
        """
        Check if user has budget available for request.
        
        Args:
            user_id: User identifier
            estimated_cost: Estimated cost of request
            
        Returns:
            Tuple of (can_proceed, warning_messages)
        """
        warnings = []
        can_proceed = True
        
        # Check user budgets
        user_budgets = self.user_budgets.get(user_id, [])
        
        for budget in user_budgets:
            if not budget.enabled:
                continue
            
            # Reset budget if period expired
            if budget.should_reset():
                budget.reset_period()
                logger.info(f"Reset {budget.period.value} budget for user {user_id}")
            
            # Check if request would exceed budget
            projected_usage = budget.current_usage + estimated_cost
            
            if projected_usage > budget.limit_amount:
                can_proceed = False
                warnings.append(
                    f"{budget.period.value.title()} budget exceeded: "
                    f"${projected_usage:.4f} > ${budget.limit_amount:.2f}"
                )
            else:
                # Check alert thresholds
                projected_percentage = projected_usage / budget.limit_amount
                
                for level, threshold in budget.alert_thresholds.items():
                    if projected_percentage >= threshold and budget.usage_percentage < threshold:
                        warnings.append(
                            f"{level.value.title()} alert: {budget.period.value} budget "
                            f"{projected_percentage:.1%} used (${projected_usage:.4f}/${budget.limit_amount:.2f})"
                        )
        
        # Check global emergency limits
        for period, limit in self.global_limits.items():
            current_usage = self._get_usage_for_period(user_id, period)
            if current_usage + estimated_cost > limit:
                can_proceed = False
                warnings.append(
                    f"Global {period.value} limit exceeded: "
                    f"${current_usage + estimated_cost:.4f} > ${limit:.2f}"
                )
        
        return can_proceed, warnings
    
    def record_usage(self, user_id: str, provider: str, model: str,
                    input_tokens: int, output_tokens: int, request_id: str) -> float:
        """
        Record actual usage and update budgets.
        
        Args:
            user_id: User identifier
            provider: Provider name
            model: Model name
            input_tokens: Actual input tokens
            output_tokens: Actual output tokens
            request_id: Request identifier
            
        Returns:
            Actual cost
        """
        # Calculate actual cost
        actual_cost = self.cost_calculator.calculate_actual_cost(
            provider, model, input_tokens, output_tokens
        )
        
        # Create usage record
        usage_record = UsageRecord(
            timestamp=datetime.now(),
            user_id=user_id,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=actual_cost,
            request_id=request_id
        )
        
        # Store usage record
        self.usage_history.append(usage_record)
        
        # Update user budgets
        user_budgets = self.user_budgets.get(user_id, [])
        for budget in user_budgets:
            if budget.enabled:
                budget.current_usage += actual_cost
        
        logger.debug(f"Recorded usage for user {user_id}: ${actual_cost:.6f}")
        
        return actual_cost
    
    def _get_usage_for_period(self, user_id: str, period: BudgetPeriod) -> float:
        """Get usage for a specific period."""
        now = datetime.now()
        
        if period == BudgetPeriod.HOURLY:
            cutoff = now - timedelta(hours=1)
        elif period == BudgetPeriod.DAILY:
            cutoff = now - timedelta(days=1)
        elif period == BudgetPeriod.WEEKLY:
            cutoff = now - timedelta(weeks=1)
        elif period == BudgetPeriod.MONTHLY:
            cutoff = now - timedelta(days=30)
        else:
            cutoff = now - timedelta(days=365)
        
        total_usage = 0.0
        for record in self.usage_history:
            if (record.user_id == user_id and 
                record.timestamp >= cutoff):
                total_usage += record.cost
        
        return total_usage
    
    def get_user_budget_status(self, user_id: str) -> Dict[str, Any]:
        """Get budget status for user."""
        user_budgets = self.user_budgets.get(user_id, [])
        budget_status = []
        
        for budget in user_budgets:
            if budget.should_reset():
                budget.reset_period()
            
            status = {
                'period': budget.period.value,
                'limit': budget.limit_amount,
                'used': budget.current_usage,
                'remaining': budget.remaining_budget,
                'percentage': budget.usage_percentage,
                'alert_level': budget.get_alert_level().value if budget.get_alert_level() else None,
                'enabled': budget.enabled
            }
            budget_status.append(status)
        
        return {
            'user_id': user_id,
            'budgets': budget_status,
            'total_requests': len([r for r in self.usage_history if r.user_id == user_id])
        }
    
    def get_usage_analytics(self, user_id: Optional[str] = None, 
                          days: int = 30) -> Dict[str, Any]:
        """Get usage analytics."""
        cutoff = datetime.now() - timedelta(days=days)
        
        # Filter records
        records = [
            r for r in self.usage_history
            if r.timestamp >= cutoff and (user_id is None or r.user_id == user_id)
        ]
        
        if not records:
            return {'total_cost': 0.0, 'total_requests': 0, 'providers': {}}
        
        # Calculate analytics
        total_cost = sum(r.cost for r in records)
        total_requests = len(records)
        
        # Provider breakdown
        provider_stats = {}
        for record in records:
            if record.provider not in provider_stats:
                provider_stats[record.provider] = {
                    'requests': 0,
                    'cost': 0.0,
                    'tokens': 0
                }
            
            provider_stats[record.provider]['requests'] += 1
            provider_stats[record.provider]['cost'] += record.cost
            provider_stats[record.provider]['tokens'] += record.input_tokens + record.output_tokens
        
        # Daily breakdown
        daily_stats = {}
        for record in records:
            day = record.timestamp.date().isoformat()
            if day not in daily_stats:
                daily_stats[day] = {'requests': 0, 'cost': 0.0}
            
            daily_stats[day]['requests'] += 1
            daily_stats[day]['cost'] += record.cost
        
        return {
            'period_days': days,
            'total_cost': total_cost,
            'total_requests': total_requests,
            'average_cost_per_request': total_cost / total_requests if total_requests > 0 else 0.0,
            'providers': provider_stats,
            'daily_breakdown': daily_stats
        }
    
    def cleanup_old_records(self, days_to_keep: int = 90):
        """Clean up old usage records."""
        cutoff = datetime.now() - timedelta(days=days_to_keep)
        
        old_count = len(self.usage_history)
        self.usage_history = [
            record for record in self.usage_history
            if record.timestamp >= cutoff
        ]
        
        removed_count = old_count - len(self.usage_history)
        if removed_count > 0:
            logger.info(f"Cleaned up {removed_count} old usage records")


class CostManager:
    """
    Main cost management system.
    
    Integrates cost calculation, budget management, and usage tracking
    for comprehensive cost control.
    """
    
    def __init__(self):
        """Initialize cost manager."""
        self.budget_manager = BudgetManager()
        self.cost_calculator = CostCalculator()
        
        # Default user budgets (can be overridden)
        self.default_budgets = {
            BudgetPeriod.DAILY: 10.0,    # $10/day default
            BudgetPeriod.MONTHLY: 100.0  # $100/month default
        }
        
        logger.info("Cost manager initialized")
    
    async def check_request_cost(self, user_id: str, provider: str, model: str,
                               input_tokens: int, estimated_output_tokens: int) -> Tuple[bool, CostEstimate, List[str]]:
        """
        Check if request is within budget limits.
        
        Args:
            user_id: User identifier
            provider: Provider name
            model: Model name
            input_tokens: Input token count
            estimated_output_tokens: Estimated output tokens
            
        Returns:
            Tuple of (can_proceed, cost_estimate, warnings)
        """
        # Ensure user has default budgets
        self._ensure_user_budgets(user_id)
        
        # Estimate cost
        cost_estimate = self.cost_calculator.estimate_cost(
            provider, model, input_tokens, estimated_output_tokens
        )
        
        # Check budget availability
        can_proceed, warnings = self.budget_manager.check_budget_availability(
            user_id, cost_estimate.total_cost
        )
        
        return can_proceed, cost_estimate, warnings
    
    async def record_request_completion(self, user_id: str, provider: str, model: str,
                                      input_tokens: int, output_tokens: int, request_id: str) -> float:
        """Record completed request and update budgets."""
        return self.budget_manager.record_usage(
            user_id, provider, model, input_tokens, output_tokens, request_id
        )
    
    def _ensure_user_budgets(self, user_id: str):
        """Ensure user has default budgets set."""
        if user_id not in self.budget_manager.user_budgets:
            for period, amount in self.default_budgets.items():
                self.budget_manager.set_user_budget(user_id, period, amount)
    
    def set_user_budget(self, user_id: str, period: BudgetPeriod, amount: float):
        """Set custom budget for user."""
        self.budget_manager.set_user_budget(user_id, period, amount)
    
    def get_user_status(self, user_id: str) -> Dict[str, Any]:
        """Get comprehensive user cost status."""
        return self.budget_manager.get_user_budget_status(user_id)
    
    def get_analytics(self, user_id: Optional[str] = None, days: int = 30) -> Dict[str, Any]:
        """Get usage analytics."""
        return self.budget_manager.get_usage_analytics(user_id, days)


# Global cost manager instance
cost_manager = CostManager()