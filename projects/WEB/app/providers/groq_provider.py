"""
Groq Provider Implementation

This module implements the Groq provider for the AI Providers System,
supporting Groq models with streaming and health check capabilities.
"""

import asyncio
import time
from typing import List, AsyncGenerator, Optional, Dict, Any
import logging
from groq import AsyncGroq

from .base import (
    BaseProvider, ProviderConfig, CompletionRequest, CompletionResponse,
    Usage, ProviderError, AuthenticationError, RateLimitError
)


logger = logging.getLogger(__name__)


class GroqProvider(BaseProvider):
    """
    Groq provider implementation.
    
    Supports text completion, streaming, and health monitoring
    for Groq's model family.
    """
    
    def __init__(self, config: ProviderConfig):
        """
        Initialize Groq provider.
        
        Args:
            config: Provider configuration
        """
        super().__init__(config)
        
        # Initialize Groq client
        self.client = AsyncGroq(
            api_key=config.api_key,
            base_url=config.base_url or "https://api.groq.com/openai/v1",
            timeout=config.timeout,
        )
        
        # Default models if not specified in config
        self._default_models = [
            "mixtral-8x7b-32768",
            "llama2-70b-4096",
            "gemma-7b-it",
            "llama3-8b-8192",
            "llama3-70b-8192"
        ]
        
        logger.info(f"Initialized Groq provider with {len(self.supported_models)} models")
    
    async def generate_completion(self, request: CompletionRequest) -> CompletionResponse:
        """
        Generate text completion using Groq API.
        
        Args:
            request: Standardized completion request
            
        Returns:
            Standardized completion response
            
        Raises:
            ProviderError: If the request fails
        """
        try:
            start_time = time.time()
            
            # Transform request to Groq format
            groq_request = self._transform_request(request)
            
            # Make API call with retry logic
            response = await self._make_api_call(groq_request)
            
            # Transform response to standard format
            standard_response = self._transform_response(response, time.time() - start_time)
            
            logger.debug(f"Groq completion successful in {time.time() - start_time:.2f}s")
            return standard_response
            
        except Exception as e:
            logger.error(f"Groq completion failed: {e}")
            raise self._handle_error(e)
    
    async def stream_completion(self, request: CompletionRequest) -> AsyncGenerator[str, None]:
        """
        Stream text completion using Groq API.
        
        Args:
            request: Standardized completion request
            
        Yields:
            Chunks of completion text
            
        Raises:
            ProviderError: If the request fails
        """
        try:
            # Transform request to Groq format with streaming enabled
            groq_request = self._transform_request(request)
            groq_request["stream"] = True
            
            # Make streaming API call
            stream = await self.client.chat.completions.create(**groq_request)
            
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
            
            logger.debug("Groq streaming completion successful")
            
        except Exception as e:
            logger.error(f"Groq streaming failed: {e}")
            raise self._handle_error(e)
    
    async def health_check(self) -> bool:
        """
        Check if Groq provider is healthy and accessible.
        
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
            
            groq_request = self._transform_request(test_request)
            
            # Set a shorter timeout for health checks
            response = await asyncio.wait_for(
                self.client.chat.completions.create(**groq_request),
                timeout=10.0
            )
            
            # Check if we got a valid response
            if response and response.choices:
                logger.debug("Groq health check passed")
                return True
            
            logger.warning("Groq health check failed: Invalid response")
            return False
            
        except Exception as e:
            logger.warning(f"Groq health check failed: {e}")
            return False
    
    def validate_config(self) -> bool:
        """
        Validate Groq provider configuration.
        
        Returns:
            True if configuration is valid, False otherwise
        """
        try:
            # Check required fields
            if not self.config.api_key:
                logger.error("Groq API key is required")
                return False
            
            # Validate API key format (Groq keys start with 'gsk_')
            if not self.config.api_key.startswith('gsk_'):
                logger.warning("Groq API key should start with 'gsk_'")
            
            # Validate base URL if provided
            if self.config.base_url:
                if not self.config.base_url.startswith(('http://', 'https://')):
                    logger.error("Groq base URL must start with http:// or https://")
                    return False
            
            # Validate timeout
            if self.config.timeout <= 0:
                logger.error("Groq timeout must be positive")
                return False
            
            logger.debug("Groq configuration validation passed")
            return True
            
        except Exception as e:
            logger.error(f"Groq configuration validation failed: {e}")
            return False
    
    @property
    def supported_models(self) -> List[str]:
        """
        Return list of supported Groq models.
        
        Returns:
            List of model names supported by Groq
        """
        if self.config.models:
            return self.config.models
        return self._default_models
    
    def _transform_request(self, request: CompletionRequest) -> Dict[str, Any]:
        """
        Transform standardized request to Groq API format.
        
        Args:
            request: Standardized completion request
            
        Returns:
            Groq API request dictionary
        """
        # Convert messages to Groq format (OpenAI-compatible)
        messages = []
        for msg in request.messages:
            messages.append({
                "role": msg.role,
                "content": msg.content
            })
        
        # Build Groq request
        groq_request = {
            "model": request.model or self.get_default_model(),
            "messages": messages,
        }
        
        # Add optional parameters
        if request.max_tokens is not None:
            groq_request["max_tokens"] = request.max_tokens
        
        if request.temperature is not None:
            groq_request["temperature"] = request.temperature
        
        if request.top_p is not None:
            groq_request["top_p"] = request.top_p
        
        if request.stop:
            groq_request["stop"] = request.stop
        
        if request.stream:
            groq_request["stream"] = True
        
        return groq_request
    
    def _transform_response(self, response: Any, response_time: float) -> CompletionResponse:
        """
        Transform Groq API response to standardized format.
        
        Args:
            response: Groq API response
            response_time: Time taken for the request
            
        Returns:
            Standardized completion response
        """
        # Build choices in standard format
        choices = []
        for choice in response.choices:
            choices.append({
                "index": choice.index,
                "message": {
                    "role": "assistant",
                    "content": choice.message.content or ""
                },
                "finish_reason": choice.finish_reason
            })
        
        # Build usage information
        usage = Usage(
            prompt_tokens=response.usage.prompt_tokens if response.usage else 0,
            completion_tokens=response.usage.completion_tokens if response.usage else 0,
            total_tokens=response.usage.total_tokens if response.usage else 0
        )
        
        return CompletionResponse(
            id=response.id,
            model=response.model,
            provider=self.name,
            choices=choices,
            usage=usage,
            created=response.created
        )
    
    async def _make_api_call(self, groq_request: Dict[str, Any]) -> Any:
        """
        Make API call to Groq with retry logic.
        
        Args:
            groq_request: Groq API request dictionary
            
        Returns:
            Groq API response
            
        Raises:
            ProviderError: If all retries fail
        """
        last_error = None
        
        for attempt in range(self.config.max_retries + 1):
            try:
                response = await self.client.chat.completions.create(**groq_request)
                return response
                
            except Exception as e:
                last_error = e
                
                # Don't retry on certain errors
                error_str = str(e).lower()
                
                if "authentication" in error_str or "unauthorized" in error_str:
                    raise AuthenticationError(f"Groq authentication failed: {e}", self.name)
                
                if "permission" in error_str:
                    raise AuthenticationError(f"Groq permission denied: {e}", self.name)
                
                # Handle rate limiting
                if "rate limit" in error_str or "quota" in error_str:
                    if attempt < self.config.max_retries:
                        wait_time = self.config.retry_delay * (2 ** attempt)
                        logger.warning(f"Groq rate limited, waiting {wait_time}s before retry {attempt + 1}")
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        raise RateLimitError(f"Groq rate limit exceeded: {e}", self.name)
                
                # Retry on other errors
                if attempt < self.config.max_retries:
                    wait_time = self.config.retry_delay * (2 ** attempt)
                    logger.warning(f"Groq request failed, retrying in {wait_time}s: {e}")
                    await asyncio.sleep(wait_time)
                    continue
        
        # All retries failed
        raise ProviderError(f"Groq request failed after {self.config.max_retries} retries: {last_error}", self.name)
    
    def _handle_error(self, error: Exception) -> ProviderError:
        """
        Handle and classify Groq API errors.
        
        Args:
            error: The original error
            
        Returns:
            Appropriate ProviderError subclass
        """
        if isinstance(error, (AuthenticationError, RateLimitError, ProviderError)):
            return error
        
        error_str = str(error).lower()
        
        if "authentication" in error_str or "unauthorized" in error_str:
            return AuthenticationError(f"Groq authentication failed: {error}", self.name)
        
        if "permission" in error_str:
            return AuthenticationError(f"Groq permission denied: {error}", self.name)
        
        if "rate limit" in error_str or "quota" in error_str:
            return RateLimitError(f"Groq rate limit exceeded: {error}", self.name)
        
        if "bad request" in error_str or "invalid" in error_str:
            return ProviderError(f"Groq bad request: {error}", self.name, retryable=False)
        
        if "server error" in error_str or "internal" in error_str:
            return ProviderError(f"Groq server error: {error}", self.name, retryable=True)
        
        if isinstance(error, asyncio.TimeoutError):
            return ProviderError(f"Groq request timeout: {error}", self.name, retryable=True)
        
        # Generic error
        return ProviderError(f"Groq error: {error}", self.name, retryable=True)