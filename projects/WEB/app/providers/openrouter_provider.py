"""
OpenRouter Provider Implementation

This module implements the OpenRouter provider for the AI Providers System,
supporting OpenRouter models with streaming and health check capabilities.
"""

import asyncio
import time
from typing import List, AsyncGenerator, Optional, Dict, Any
import logging
import aiohttp
import json

from .base import (
    BaseProvider, ProviderConfig, CompletionRequest, CompletionResponse,
    Usage, ProviderError, AuthenticationError, RateLimitError
)


logger = logging.getLogger(__name__)