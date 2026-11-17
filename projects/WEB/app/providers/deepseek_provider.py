"""
DeepSeek Provider Implementation

This module implements the DeepSeek provider for the AI Providers System,
supporting DeepSeek models with streaming and health check capabilities.
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


class DeepseekProvider(BaseProvider):
    """
    DeepSeek provider implementation.
    
    Supports text completion, streaming, and health monitoring
    for DeepSeek's model family using OpenAI-compatible API.
    """
    
    def __init__(self, config: ProviderConfig):
        """
        Initialize DeepSeek provider.
        
        Args:
            config: Provider configuration
        """
        super().__init__(config)
        
        # Set default base URL if not provided
        self.base_url = config.base_url or "https://api.deepseek.com/v1"
        
        # Default models if not specified in config
        self._default_models = [
            "deepseek-chat",
            "deepseek-coder",
            "deepseek-reasoner"
        ]
        
        # HTTP session for connection pooling
        self._session: Optional[aiohttp.ClientSession] = None
        
        logger.info(f"Initialized DeepSeek provider with {len(self.supported_models)} models")
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.config.timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session
    
    async def generate_completion(self, request: CompletionRequest) -> CompletionResponse:
        """
        Generate text completion using DeepSeek API.
        
        Args:
            request: Standardized completion request
            
        Returns:
            Standardized completion response
            
        Raises:
            ProviderError: If the request fails
        """
        try:
            start_time = time.time()
            
            # Transform request to DeepSeek format
            deepseek_request = self._transform_request(request)
            
            # Make API call with retry logic
            response = await self._make_api_call(deepseek_request)
            
            # Transform response to standard format
            standard_response = self._transform_response(response, time.time() - start_time)
            
            logger.debug(f"DeepSeek completion successful in {time.time() - start_time:.2f}s")
            return standard_response
            
        except Exception as e:
            logger.error(f"DeepSeek completion failed: {e}")
            raise self._handle_error(e)
    
    async def stream_completion(self, request: CompletionRequest) -> AsyncGenerator[str, None]:
        """
        Stream text completion using DeepSeek API.
        
        Args:
            request: Standardized completion request
            
        Yields:
            Chunks of completion text
            
        Raises:
            ProviderError: If the request fails
        """
        try:
            # Transform request to DeepSeek format with streaming enabled
            deepseek_request = self._transform_request(request)
            deepseek_request["stream"] = True
            
            # Make streaming API call
            session = await self._get_session()
            headers = {
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json"
            }
            
            url = f"{self.base_url}/chat/completions"
            
            async with session.post(url, json=deepseek_request, headers=headers) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise ProviderError(f"DeepSeek API error: {response.status} - {error_text}", self.name)
                
                async for line in response.content:
                    line = line.decode('utf-8').strip()
                    if line.startswith('data: '):
                        data = line[6:]  # Remove 'data: ' prefix
                        if data == '[DONE]':
                            break
                        
                        try:
                            chunk = json.loads(data)
                            if 'choices' in chunk and chunk['choices']:
                                delta = chunk['choices'][0].get('delta', {})
                                if 'content' in delta and delta['content']:
                                    yield delta['content']
                        except json.JSONDecodeError:
                            continue
            
            logger.debug("DeepSeek streaming completion successful")
            
        except Exception as e:
            logger.error(f"DeepSeek streaming failed: {e}")
            raise self._handle_error(e)
    
    async def health_check(self) -> bool:
        """
        Check if DeepSeek provider is healthy and accessible.
        
        Returns:
            True if provider is healthy, False otherwise
        """
        try:
            # Simple health check using a minimal request
            test_request = CompletionRequest(
                messages=[{"role": "user", "content": "Hello"}],
                max_tokens=1,
                model=self.get_default_model()
            )
            
            deepseek_request = self._transform_request(test_request)
            
            # Set a shorter timeout for health checks
            response = await asyncio.wait_for(
                self._make_api_call(deepseek_request),
                timeout=10.0
            )
            
            # Check if we got a valid response
            if response and 'choices' in response and response['choices']:
                logger.debug("DeepSeek health check passed")
                return True
            
            logger.warning("DeepSeek health check failed: Invalid response")
            return False
            
        except Exception as e:
            logger.warning(f"DeepSeek health check failed: {e}")
            return False
    
    def validate_config(self) -> bool:
        """
        Validate DeepSeek provider configuration.
        
        Returns:
            True if configuration is valid, False otherwise
        """
        try:
            # Check required fields
            if not self.config.api_key:
                logger.error("DeepSeek API key is required")
                return False
            
            # Validate base URL
            if self.base_url and not self.base_url.startswith(('http://', 'https://')):
                logger.error("DeepSeek base URL must start with http:// or https://")
                return False
            
            # Validate timeout
            if self.config.timeout <= 0:
                logger.error("DeepSeek timeout must be positive")
                return False
            
            logger.debug("DeepSeek configuration validation passed")
            return True
            
        except Exception as e:
            logger.error(f"DeepSeek configuration validation failed: {e}")
            return False
    
    @property
    def supported_models(self) -> List[str]:
        """
        Return list of supported DeepSeek models.
        
        Returns:
            List of model names supported by DeepSeek
        """
        if self.config.models:
            return self.config.models
        return self._default_models
    
    def _transform_request(self, request: CompletionRequest) -> Dict[str, Any]:
        """
        Transform standardized request to DeepSeek API format.
        
        Args:
            request: Standardized completion request
            
        Returns:
            DeepSeek API request dictionary
        """
        # Convert messages to DeepSeek format (OpenAI-compatible)
        messages = []
        for msg in request.messages:
            messages.append({
                "role": msg.role,
                "content": msg.content
            })
        
        # Build DeepSeek request
        deepseek_request = {
            "model": request.model or self.get_default_model(),
            "messages": messages,
        }
        
        # Add optional parameters
        if request.max_tokens is not None:
            deepseek_request["max_tokens"] = request.max_tokens
        
        if request.temperature is not None:
            deepseek_request["temperature"] = request.temperature
        
        if request.top_p is not None:
            deepseek_request["top_p"] = request.top_p
        
        if request.stop:
            deepseek_request["stop"] = request.stop
        
        if request.stream:
            deepseek_request["stream"] = True
        
        return deepseek_request
    
    def _transform_response(self, response: Dict[str, Any], response_time: float) -> CompletionResponse:
        """
        Transform DeepSeek API response to standardized format.
        
        Args:
            response: DeepSeek API response
            response_time: Time taken for the request
            
        Returns:
            Standardized completion response
        """
        # Build usage information
        usage_data = response.get('usage', {})
        usage = Usage(
            prompt_tokens=usage_data.get('prompt_tokens', 0),
            completion_tokens=usage_data.get('completion_tokens', 0),
            total_tokens=usage_data.get('total_tokens', 0)
        )
        
        return CompletionResponse(
            id=response.get('id', f"deepseek-{int(time.time())}"),
            model=response.get('model', self.get_default_model()),
            provider=self.name,
            choices=response.get('choices', []),
            usage=usage,
            created=response.get('created', int(time.time()))
        )
    
    async def _make_api_call(self, deepseek_request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Make API call to DeepSeek with retry logic.
        
        Args:
            deepseek_request: DeepSeek API request dictionary
            
        Returns:
            DeepSeek API response
            
        Raises:
            ProviderError: If all retries fail
        """
        last_error = None
        
        for attempt in range(self.config.max_retries + 1):
            try:
                session = await self._get_session()
                headers = {
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json"
                }
                
                url = f"{self.base_url}/chat/completions"
                
                async with session.post(url, json=deepseek_request, headers=headers) as response:
                    response_text = await response.text()
                    
                    if response.status == 200:
                        return json.loads(response_text)
                    
                    # Handle specific error codes
                    if response.status == 401:
                        raise AuthenticationError(f"DeepSeek authentication failed: {response_text}", self.name)
                    
                    if response.status == 429:
                        retry_after = response.headers.get('Retry-After')
                        if attempt < self.config.max_retries:
                            wait_time = int(retry_after) if retry_after else (self.config.retry_delay * (2 ** attempt))
                            logger.warning(f"DeepSeek rate limited, waiting {wait_time}s before retry {attempt + 1}")
                            await asyncio.sleep(wait_time)
                            continue
                        else:
                            raise RateLimitError(f"DeepSeek rate limit exceeded: {response_text}", self.name, int(retry_after) if retry_after else None)
                    
                    # Other HTTP errors
                    raise ProviderError(f"DeepSeek API error: {response.status} - {response_text}", self.name)
                
            except Exception as e:
                last_error = e
                
                # Don't retry on authentication errors
                if isinstance(e, AuthenticationError):
                    raise
                
                # Don't retry on final rate limit attempt
                if isinstance(e, RateLimitError):
                    raise
                
                # Retry on other errors
                if attempt < self.config.max_retries:
                    wait_time = self.config.retry_delay * (2 ** attempt)
                    logger.warning(f"DeepSeek request failed, retrying in {wait_time}s: {e}")
                    await asyncio.sleep(wait_time)
                    continue
        
        # All retries failed
        raise ProviderError(f"DeepSeek request failed after {self.config.max_retries} retries: {last_error}", self.name)
    
    def _handle_error(self, error: Exception) -> ProviderError:
        """
        Handle and classify DeepSeek API errors.
        
        Args:
            error: The original error
            
        Returns:
            Appropriate ProviderError subclass
        """
        if isinstance(error, (AuthenticationError, RateLimitError, ProviderError)):
            return error
        
        error_str = str(error).lower()
        
        if "authentication" in error_str or "unauthorized" in error_str:
            return AuthenticationError(f"DeepSeek authentication failed: {error}", self.name)
        
        if "rate limit" in error_str or "quota" in error_str:
            return RateLimitError(f"DeepSeek rate limit exceeded: {error}", self.name)
        
        if "bad request" in error_str or "invalid" in error_str:
            return ProviderError(f"DeepSeek bad request: {error}", self.name, retryable=False)
        
        if "server error" in error_str or "internal" in error_str:
            return ProviderError(f"DeepSeek server error: {error}", self.name, retryable=True)
        
        if isinstance(error, asyncio.TimeoutError):
            return ProviderError(f"DeepSeek request timeout: {error}", self.name, retryable=True)
        
        # Generic error
        return ProviderError(f"DeepSeek error: {error}", self.name, retryable=True)
    
    async def __aenter__(self):
        """Async context manager entry."""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self._session and not self._session.closed:
            await self._session.close()