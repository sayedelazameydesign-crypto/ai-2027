"""
Anthropic Provider Implementation

This module implements the Anthropic Claude provider for the AI Providers System,
supporting Claude models with streaming and health check capabilities.
"""

import asyncio
import time
from typing import List, AsyncGenerator, Optional, Dict, Any
import logging
import anthropic
from anthropic import AsyncAnthropic

from .base import (
    BaseProvider, ProviderConfig, CompletionRequest, CompletionResponse,
    Usage, ProviderError, AuthenticationError, RateLimitError
)


logger = logging.getLogger(__name__)


class AnthropicProvider(BaseProvider):
    """
    Anthropic Claude provider implementation.
    
    Supports text completion, streaming, and health monitoring
    for Anthropic's Claude model family.
    """
    
    def __init__(self, config: ProviderConfig):
        """
        Initialize Anthropic provider.
        
        Args:
            config: Provider configuration
        """
        super().__init__(config)
        
        # Initialize Anthropic client
        self.client = AsyncAnthropic(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout,
        )
        
        # Default models if not specified in config
        self._default_models = [
            "claude-3-5-sonnet-20241022",
            "claude-3-sonnet-20240229",
            "claude-3-haiku-20240307",
            "claude-3-opus-20240229"
        ]
        
        logger.info(f"Initialized Anthropic provider with {len(self.supported_models)} models")
    
    async def generate_completion(self, request: CompletionRequest) -> CompletionResponse:
        """
        Generate text completion using Anthropic API.
        
        Args:
            request: Standardized completion request
            
        Returns:
            Standardized completion response
            
        Raises:
            ProviderError: If the request fails
        """
        try:
            start_time = time.time()
            
            # Transform request to Anthropic format
            anthropic_request = self._transform_request(request)
            
            # Make API call with retry logic
            response = await self._make_api_call(anthropic_request)
            
            # Transform response to standard format
            standard_response = self._transform_response(response, time.time() - start_time)
            
            logger.debug(f"Anthropic completion successful in {time.time() - start_time:.2f}s")
            return standard_response
            
        except Exception as e:
            logger.error(f"Anthropic completion failed: {e}")
            raise self._handle_error(e)
    
    async def stream_completion(self, request: CompletionRequest) -> AsyncGenerator[str, None]:
        """
        Stream text completion using Anthropic API.
        
        Args:
            request: Standardized completion request
            
        Yields:
            Chunks of completion text
            
        Raises:
            ProviderError: If the request fails
        """
        try:
            # Transform request to Anthropic format
            anthropic_request = self._transform_request(request)
            
            # Make streaming API call
            async with self.client.messages.stream(**anthropic_request) as stream:
                async for text in stream.text_stream:
                    yield text
            
            logger.debug("Anthropic streaming completion successful")
            
        except Exception as e:
            logger.error(f"Anthropic streaming failed: {e}")
            raise self._handle_error(e)
    
    async def health_check(self) -> bool:
        """
        Check if Anthropic provider is healthy and accessible.
        
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
            
            anthropic_request = self._transform_request(test_request)
            
            # Set a shorter timeout for health checks
            response = await asyncio.wait_for(
                self.client.messages.create(**anthropic_request),
                timeout=10.0
            )
            
            # Check if we got a valid response
            if response and response.content:
                logger.debug("Anthropic health check passed")
                return True
            
            logger.warning("Anthropic health check failed: Invalid response")
            return False
            
        except Exception as e:
            logger.warning(f"Anthropic health check failed: {e}")
            return False
    
    def validate_config(self) -> bool:
        """
        Validate Anthropic provider configuration.
        
        Returns:
            True if configuration is valid, False otherwise
        """
        try:
            # Check required fields
            if not self.config.api_key:
                logger.error("Anthropic API key is required")
                return False
            
            # Validate API key format (Anthropic keys start with 'sk-ant-')
            if not self.config.api_key.startswith('sk-ant-'):
                logger.warning("Anthropic API key should start with 'sk-ant-'")
            
            # Validate base URL if provided
            if self.config.base_url:
                if not self.config.base_url.startswith(('http://', 'https://')):
                    logger.error("Anthropic base URL must start with http:// or https://")
                    return False
            
            # Validate timeout
            if self.config.timeout <= 0:
                logger.error("Anthropic timeout must be positive")
                return False
            
            logger.debug("Anthropic configuration validation passed")
            return True
            
        except Exception as e:
            logger.error(f"Anthropic configuration validation failed: {e}")
            return False
    
    @property
    def supported_models(self) -> List[str]:
        """
        Return list of supported Anthropic models.
        
        Returns:
            List of model names supported by Anthropic
        """
        if self.config.models:
            return self.config.models
        return self._default_models
    
    def _transform_request(self, request: CompletionRequest) -> Dict[str, Any]:
        """
        Transform standardized request to Anthropic API format.
        
        Args:
            request: Standardized completion request
            
        Returns:
            Anthropic API request dictionary
        """
        # Convert messages to Anthropic format
        messages = []
        system_message = None
        
        for msg in request.messages:
            if msg.role == "system":
                system_message = msg.content
            else:
                messages.append({
                    "role": msg.role,
                    "content": msg.content
                })
        
        # Build Anthropic request
        anthropic_request = {
            "model": request.model or self.get_default_model(),
            "messages": messages,
        }
        
        # Add system message if present
        if system_message:
            anthropic_request["system"] = system_message
        
        # Add optional parameters
        if request.max_tokens is not None:
            anthropic_request["max_tokens"] = request.max_tokens
        else:
            # Anthropic requires max_tokens
            anthropic_request["max_tokens"] = 1024
        
        if request.temperature is not None:
            anthropic_request["temperature"] = request.temperature
        
        if request.top_p is not None:
            anthropic_request["top_p"] = request.top_p
        
        if request.stop:
            anthropic_request["stop_sequences"] = request.stop
        
        return anthropic_request
    
    def _transform_response(self, response: Any, response_time: float) -> CompletionResponse:
        """
        Transform Anthropic API response to standardized format.
        
        Args:
            response: Anthropic API response
            response_time: Time taken for the request
            
        Returns:
            Standardized completion response
        """
        # Extract content from response
        content = ""
        if response.content and len(response.content) > 0:
            content = response.content[0].text if hasattr(response.content[0], 'text') else str(response.content[0])
        
        # Build choices in standard format
        choices = [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": content
            },
            "finish_reason": response.stop_reason
        }]
        
        # Build usage information
        usage = Usage(
            prompt_tokens=response.usage.input_tokens if response.usage else 0,
            completion_tokens=response.usage.output_tokens if response.usage else 0,
            total_tokens=(response.usage.input_tokens + response.usage.output_tokens) if response.usage else 0
        )
        
        return CompletionResponse(
            id=response.id,
            model=response.model,
            provider=self.name,
            choices=choices,
            usage=usage,
            created=int(time.time())
        )
    
    async def _make_api_call(self, anthropic_request: Dict[str, Any]) -> Any:
        """
        Make API call to Anthropic with retry logic.
        
        Args:
            anthropic_request: Anthropic API request dictionary
            
        Returns:
            Anthropic API response
            
        Raises:
            ProviderError: If all retries fail
        """
        last_error = None
        
        for attempt in range(self.config.max_retries + 1):
            try:
                response = await self.client.messages.create(**anthropic_request)
                return response
                
            except Exception as e:
                last_error = e
                
                # Don't retry on certain errors
                if isinstance(e, anthropic.AuthenticationError):
                    raise AuthenticationError(f"Anthropic authentication failed: {e}", self.name)
                
                if isinstance(e, anthropic.PermissionDeniedError):
                    raise AuthenticationError(f"Anthropic permission denied: {e}", self.name)
                
                # Handle rate limiting
                if isinstance(e, anthropic.RateLimitError):
                    retry_after = getattr(e, 'retry_after', None)
                    if attempt < self.config.max_retries:
                        wait_time = retry_after or (self.config.retry_delay * (2 ** attempt))
                        logger.warning(f"Anthropic rate limited, waiting {wait_time}s before retry {attempt + 1}")
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        raise RateLimitError(f"Anthropic rate limit exceeded: {e}", self.name, retry_after)
                
                # Retry on other errors
                if attempt < self.config.max_retries:
                    wait_time = self.config.retry_delay * (2 ** attempt)
                    logger.warning(f"Anthropic request failed, retrying in {wait_time}s: {e}")
                    await asyncio.sleep(wait_time)
                    continue
        
        # All retries failed
        raise ProviderError(f"Anthropic request failed after {self.config.max_retries} retries: {last_error}", self.name)
    
    def _handle_error(self, error: Exception) -> ProviderError:
        """
        Handle and classify Anthropic API errors.
        
        Args:
            error: The original error
            
        Returns:
            Appropriate ProviderError subclass
        """
        if isinstance(error, (AuthenticationError, RateLimitError, ProviderError)):
            return error
        
        if isinstance(error, anthropic.AuthenticationError):
            return AuthenticationError(f"Anthropic authentication failed: {error}", self.name)
        
        if isinstance(error, anthropic.PermissionDeniedError):
            return AuthenticationError(f"Anthropic permission denied: {error}", self.name)
        
        if isinstance(error, anthropic.RateLimitError):
            retry_after = getattr(error, 'retry_after', None)
            return RateLimitError(f"Anthropic rate limit exceeded: {error}", self.name, retry_after)
        
        if isinstance(error, anthropic.BadRequestError):
            return ProviderError(f"Anthropic bad request: {error}", self.name, retryable=False)
        
        if isinstance(error, anthropic.InternalServerError):
            return ProviderError(f"Anthropic server error: {error}", self.name, retryable=True)
        
        if isinstance(error, asyncio.TimeoutError):
            return ProviderError(f"Anthropic request timeout: {error}", self.name, retryable=True)
        
        # Generic error
        return ProviderError(f"Anthropic error: {error}", self.name, retryable=True)