"""
OpenAI Provider Implementation

This module implements the OpenAI provider for the AI Providers System,
supporting GPT models with streaming and health check capabilities.
"""

import asyncio
import time
from typing import List, AsyncGenerator, Optional, Dict, Any
import logging
import openai
from openai import AsyncOpenAI

from .base import (
    BaseProvider, ProviderConfig, CompletionRequest, CompletionResponse,
    Usage, ProviderError, AuthenticationError, RateLimitError
)


logger = logging.getLogger(__name__)


class OpenaiProvider(BaseProvider):
    """
    OpenAI provider implementation for GPT models.
    
    Supports text completion, streaming, and health monitoring
    for OpenAI's GPT model family.
    """
    
    def __init__(self, config: ProviderConfig):
        """
        Initialize OpenAI provider.
        
        Args:
            config: Provider configuration
        """
        super().__init__(config)
        
        # Initialize OpenAI client
        self.client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout,
        )
        
        # Default models if not specified in config
        self._default_models = [
            "gpt-4",
            "gpt-4-turbo-preview",
            "gpt-3.5-turbo",
            "gpt-3.5-turbo-16k"
        ]
        
        logger.info(f"Initialized OpenAI provider with {len(self.supported_models)} models")
    
    async def generate_completion(self, request: CompletionRequest) -> CompletionResponse:
        """
        Generate text completion using OpenAI API.
        
        Args:
            request: Standardized completion request
            
        Returns:
            Standardized completion response
            
        Raises:
            ProviderError: If the request fails
        """
        try:
            start_time = time.time()
            
            # Transform request to OpenAI format
            openai_request = self._transform_request(request)
            
            # Make API call with retry logic
            response = await self._make_api_call(openai_request)
            
            # Transform response to standard format
            standard_response = self._transform_response(response, time.time() - start_time)
            
            logger.debug(f"OpenAI completion successful in {time.time() - start_time:.2f}s")
            return standard_response
            
        except Exception as e:
            logger.error(f"OpenAI completion failed: {e}")
            raise self._handle_error(e)
    
    async def stream_completion(self, request: CompletionRequest) -> AsyncGenerator[str, None]:
        """
        Stream text completion using OpenAI API.
        
        Args:
            request: Standardized completion request
            
        Yields:
            Chunks of completion text
            
        Raises:
            ProviderError: If the request fails
        """
        try:
            # Transform request to OpenAI format with streaming enabled
            openai_request = self._transform_request(request)
            openai_request["stream"] = True
            
            # Make streaming API call
            stream = await self.client.chat.completions.create(**openai_request)
            
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
            
            logger.debug("OpenAI streaming completion successful")
            
        except Exception as e:
            logger.error(f"OpenAI streaming failed: {e}")
            raise self._handle_error(e)
    
    async def health_check(self) -> bool:
        """
        Check if OpenAI provider is healthy and accessible.
        
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
            
            openai_request = self._transform_request(test_request)
            
            # Set a shorter timeout for health checks
            response = await asyncio.wait_for(
                self.client.chat.completions.create(**openai_request),
                timeout=10.0
            )
            
            # Check if we got a valid response
            if response and response.choices:
                logger.debug("OpenAI health check passed")
                return True
            
            logger.warning("OpenAI health check failed: Invalid response")
            return False
            
        except Exception as e:
            logger.warning(f"OpenAI health check failed: {e}")
            return False
    
    def validate_config(self) -> bool:
        """
        Validate OpenAI provider configuration.
        
        Returns:
            True if configuration is valid, False otherwise
        """
        try:
            # Check required fields
            if not self.config.api_key:
                logger.error("OpenAI API key is required")
                return False
            
            # Validate API key format (OpenAI keys start with 'sk-')
            if not self.config.api_key.startswith('sk-'):
                logger.warning("OpenAI API key should start with 'sk-'")
            
            # Validate base URL if provided
            if self.config.base_url:
                if not self.config.base_url.startswith(('http://', 'https://')):
                    logger.error("OpenAI base URL must start with http:// or https://")
                    return False
            
            # Validate timeout
            if self.config.timeout <= 0:
                logger.error("OpenAI timeout must be positive")
                return False
            
            logger.debug("OpenAI configuration validation passed")
            return True
            
        except Exception as e:
            logger.error(f"OpenAI configuration validation failed: {e}")
            return False
    
    @property
    def supported_models(self) -> List[str]:
        """
        Return list of supported OpenAI models.
        
        Returns:
            List of model names supported by OpenAI
        """
        if self.config.models:
            return self.config.models
        return self._default_models
    
    def _transform_request(self, request: CompletionRequest) -> Dict[str, Any]:
        """
        Transform standardized request to OpenAI API format.
        
        Args:
            request: Standardized completion request
            
        Returns:
            OpenAI API request dictionary
        """
        # Convert messages to OpenAI format
        messages = []
        for msg in request.messages:
            messages.append({
                "role": msg.role,
                "content": msg.content
            })
        
        # Build OpenAI request
        openai_request = {
            "model": request.model or self.get_default_model(),
            "messages": messages,
        }
        
        # Add optional parameters
        if request.max_tokens is not None:
            openai_request["max_tokens"] = request.max_tokens
        
        if request.temperature is not None:
            openai_request["temperature"] = request.temperature
        
        if request.top_p is not None:
            openai_request["top_p"] = request.top_p
        
        if request.stop:
            openai_request["stop"] = request.stop
        
        if request.stream:
            openai_request["stream"] = True
        
        return openai_request
    
    def _transform_response(self, response: Any, response_time: float) -> CompletionResponse:
        """
        Transform OpenAI API response to standardized format.
        
        Args:
            response: OpenAI API response
            response_time: Time taken for the request
            
        Returns:
            Standardized completion response
        """
        # Extract content from response
        content = ""
        if response.choices and response.choices[0].message:
            content = response.choices[0].message.content or ""
        
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
    
    async def _make_api_call(self, openai_request: Dict[str, Any]) -> Any:
        """
        Make API call to OpenAI with retry logic.
        
        Args:
            openai_request: OpenAI API request dictionary
            
        Returns:
            OpenAI API response
            
        Raises:
            ProviderError: If all retries fail
        """
        last_error = None
        
        for attempt in range(self.config.max_retries + 1):
            try:
                response = await self.client.chat.completions.create(**openai_request)
                return response
                
            except Exception as e:
                last_error = e
                
                # Don't retry on certain errors
                if isinstance(e, openai.AuthenticationError):
                    raise AuthenticationError(f"OpenAI authentication failed: {e}", self.name)
                
                if isinstance(e, openai.PermissionDeniedError):
                    raise AuthenticationError(f"OpenAI permission denied: {e}", self.name)
                
                # Handle rate limiting
                if isinstance(e, openai.RateLimitError):
                    retry_after = getattr(e, 'retry_after', None)
                    if attempt < self.config.max_retries:
                        wait_time = retry_after or (self.config.retry_delay * (2 ** attempt))
                        logger.warning(f"OpenAI rate limited, waiting {wait_time}s before retry {attempt + 1}")
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        raise RateLimitError(f"OpenAI rate limit exceeded: {e}", self.name, retry_after)
                
                # Retry on other errors
                if attempt < self.config.max_retries:
                    wait_time = self.config.retry_delay * (2 ** attempt)
                    logger.warning(f"OpenAI request failed, retrying in {wait_time}s: {e}")
                    await asyncio.sleep(wait_time)
                    continue
        
        # All retries failed
        raise ProviderError(f"OpenAI request failed after {self.config.max_retries} retries: {last_error}", self.name)
    
    def _handle_error(self, error: Exception) -> ProviderError:
        """
        Handle and classify OpenAI API errors.
        
        Args:
            error: The original error
            
        Returns:
            Appropriate ProviderError subclass
        """
        if isinstance(error, (AuthenticationError, RateLimitError, ProviderError)):
            return error
        
        if isinstance(error, openai.AuthenticationError):
            return AuthenticationError(f"OpenAI authentication failed: {error}", self.name)
        
        if isinstance(error, openai.PermissionDeniedError):
            return AuthenticationError(f"OpenAI permission denied: {error}", self.name)
        
        if isinstance(error, openai.RateLimitError):
            retry_after = getattr(error, 'retry_after', None)
            return RateLimitError(f"OpenAI rate limit exceeded: {error}", self.name, retry_after)
        
        if isinstance(error, openai.BadRequestError):
            return ProviderError(f"OpenAI bad request: {error}", self.name, retryable=False)
        
        if isinstance(error, openai.InternalServerError):
            return ProviderError(f"OpenAI server error: {error}", self.name, retryable=True)
        
        if isinstance(error, asyncio.TimeoutError):
            return ProviderError(f"OpenAI request timeout: {error}", self.name, retryable=True)
        
        # Generic error
        return ProviderError(f"OpenAI error: {error}", self.name, retryable=True)