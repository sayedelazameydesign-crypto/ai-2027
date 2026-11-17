"""
Intelligence module for AI Providers System.

This module provides intelligent features including semantic routing,
cost management, and speculative execution.
"""

from .semantic_router import semantic_router, SemanticRouter
from .cost_manager import cost_manager, CostManager
from .speculative_engine import speculative_engine, SpeculativeEngine

__all__ = [
    'semantic_router',
    'SemanticRouter',
    'cost_manager', 
    'CostManager',
    'speculative_engine',
    'SpeculativeEngine'
]