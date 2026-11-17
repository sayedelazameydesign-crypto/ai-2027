"""
Google Gemini Provider Implementation

This module implements the Google Gemini provider for the AI Providers System,
supporting Gemini models with streaming and health check capabilities.
"""

import asyncio
import time
from typing import List, AsyncGenerator, Optional, Dict, Any
import logging
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

from .base import (
    BaseProvider, ProviderConfig, CompletionRequest, CompletionResponse,
    Usage, ProviderError, AuthenticationError, RateLimitError
)


logger = logging.getLogger(__name__)


class GoogleProvider(BaseProvider):
    """
    Google Gemini provider implementation.
    
    Supports text completion, streaming, and health monitoring
    for Google's Gemini model family.
    """
    
    def __init__(self, config: ProviderConfig):
        """
        Initialize Google Gemini provider.
        
        Args:
            config: Provider configuration
        """
        super().__init__(config)
        
        # Configure Google AI
        genai.configure(api_key=config.api_key)
        
        # Default models if not specified in config
        self._default_models = [
            "gemini-1.5-pro",
            "gemini-1.5-flash",
            "gemini-pro",
            "gemini-pro-vision"
        ]
        
        # Safety settings for content generation
        self.safety_settings = {
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
        }
        
        logger.info(f"Initialized Google provider with {len(self.supported_models)} models")
    
    async def generate_completion(self, request: CompletionRequest) -> CompletionResponse:
        """
        Generate text completion using Google Gemini API.
        
        Args:
            request: Standardized completion request
            
        Returns:
            Standardized completion response
            
        Raises:
            ProviderError: If the request fails
        """
        try:
            start_time = time.time()
            
            # Transform request to Google format
            model_name, prompt, generation_config = self._transform_request(request)
            
            # Create model instance
            model = genai.GenerativeModel(
                model_name=model_name,
                safety_settings=self.safety_settings
            )
            
            # Make API call with retry logic
            response = await self._make_api_call(model, prompt, generation_config)
            
            # Transform response to standard format
            standard_response = self._transform_response(response, model_name, time.time() - start_time)
            
            logger.debug(f"Google completion successful in {time.time() - start_time:.2f}s")
            return standard_response
            
        except Exception as e:
            logger.error(f"Google completion failed: {e}")
            raise self._handle_error(e)
    
    async def stream_completion(self, request: CompletionRequest) -> AsyncGenerator[str, None]:
        """
        Stream text completion using Google Gemini API.
        
        Args:
            request: Standardized completion request
            
        Yields:
            Chunks of completion text
            
        Raises:
            ProviderError: If the request fails
        """
        try:
            # Transform request to Google format
            model_name, prompt, generation_config = self._transform_request(request)
            
            # Create model instance
            model = genai.GenerativeModel(
                model_name=model_name,
                safety_settings=self.safety_settings
            )
            
            # Make streaming API call
            response = await model.generate_content_async(
                prompt,
                generation_config=generation_config,
                stream=True
            )
            
            async for chunk in response:
                if chunk.text:
                    yield chunk.text
            
            logger.debug("Google streaming completion successful")
            
        except Exception as e:
            logger.error(f"Google streaming failed: {e}")
            raise self._handle_error(e)
    
    async def health_check(self) -> bool:
        """
        Check if Google provider is healthy and accessible.
        
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
            
            model_name, prompt, generation_config = self._transform_request(test_request)
            
            # Create model instance
            model = genai.GenerativeModel(
                model_name=model_name,
                safety_settings=self.safety_settings
            )
            
            # Set a shorter timeout for health checks
            response = await asyncio.wait_for(
                model.generate_content_async(prompt, generation_config=generation_config),
                timeout=10.0
            )
            
            # Check if we got a valid response
            if response and response.text:
                logger.debug("Google health check passed")
                return True
            
            logger.warning("Google health check failed: Invalid response")
            return False
            
        except Exception as e:
            logger.warning(f"Google health check failed: {e}")
            return False
    
    def validate_config(self) -> bool:
        """
        Validate Google provider configuration.
        
        Returns:
            True if configuration is valid, False otherwise
        """
        try:
            # Check required fields
            if not self.config.api_key:
                logger.error("Google API key is required")
                return False
            
            # Validate API key format (Google keys start with 'AIza')
            if not self.config.api_key.startswith('AIza'):
                logger.warning("Google API key should start with 'AIza'")
            
            # Validate timeout
            if self.config.timeout <= 0:
                logger.error("Google timeout must be positive")
                return False
            
            logger.debug("Google configuration validation passed")
            return True
            
        except Exception as e:
            logger.error(f"Google configuration validation failed: {e}")
            return False
    
    @property
    def supported_models(self) -> List[str]:
        """
        Return list of supported Google models.
        
        Returns:
            List of model names supported by Google
        """
        if self.config.models:
            return self.config.models
        return self._default_models
    
    def _transform_request(self, request: CompletionRequest) -> tuple[str, str, Dict[str, Any]]:
        """
        Transform standardized request to Google API format.
        
        Args:
            request: Standardized completion request
            
        Returns:
            Tuple of (model_name, prompt, generation_config)
        """
        # Get model name
        model_name = request.model or self.get_default_model()
        
        # Convert messages to a single prompt
        prompt_parts = []
        for msg in request.messages:
            if msg.role == "system":
                prompt_parts.append(f"System: {msg.content}")
            elif msg.role == "user":
                prompt_parts.append(f"User: {msg.content}")
            elif msg.role == "assistant":
                prompt_parts.append(f"Assistant: {msg.content}")
        
        prompt = "\n".join(prompt_parts)
        
        # Build generation config
        generation_config = {}
        
        if request.max_tokens is not None:
            generation_config["max_output_tokens"] = request.max_tokens
        
        if request.temperature is not None:
            generation_config["temperature"] = request.temperature
        
        if request.top_p is not None:
            generation_config["top_p"] = request.top_p
        
        if request.stop:
            generation_config["stop_sequences"] = request.stop
        
        return model_name, prompt, generation_config
    
    def _transform_response(self, response: Any, model_name: str, response_time: float) -> CompletionResponse:
        """
        Transform Google API response to standardized format.
        
        Args:
            response: Google API response
            model_name: Name of the model used
            response_time: Time taken for the request
            
        Returns:
            Standardized completion response
        """
        # Extract content from response
        content = response.text if response.text else ""
        
        # Build choices in standard format
        choices = [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": content
            },
            "finish_reason": "stop"  # Google doesn't provide detailed finish reasons
        }]
        
        # Build usage information (Google doesn't provide detailed token counts)
        # Estimate based on content length
        prompt_tokens = 0
        completion_tokens = len(content.split()) if content else 0
        
        usage = Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens
        )
        
        return CompletionResponse(
            id=f"google-{int(time.time())}-{hash(content) % 10000}",
            model=model_name,
            provider=self.name,
            choices=choices,
            usage=usage,
            created=int(time.time())
        )
    
    async def _make_api_call(self, model: Any, prompt: str, generation_config: Dict[str, Any]) -> Any:
        """
        Make API call to Google with retry logic.
        
        Args:
            model: Google GenerativeModel instance
            prompt: The prompt to send
            generation_config: Generation configuration
            
        Returns:
            Google API response
            
        Raises:
            ProviderError: If all retries fail
        """
        last_error = None
        
        for attempt in range(self.config.max_retries + 1):
            try:
                response = await model.generate_content_async(
                    prompt,
                    generation_config=generation_config
                )
                return response
                
            except Exception as e:
                last_error = e
                
                # Don't retry on certain errors
                if "API_KEY_INVALID" in str(e) or "authentication" in str(e).lower():
                    raise AuthenticationError(f"Google authentication failed: {e}", self.name)
                
                # Handle rate limiting
                if "quota" in str(e).lower() or "rate" in str(e).lower():
                    if attempt < self.config.max_retries:
                        wait_time = self.config.retry_delay * (2 ** attempt)
                        logger.warning(f"Google rate limited, waiting {wait_time}s before retry {attempt + 1}")
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        raise RateLimitError(f"Google rate limit exceeded: {e}", self.name)
                
                # Retry on other errors
                if attempt < self.config.max_retries:
                    wait_time = self.config.retry_delay * (2 ** attempt)
                    logger.warning(f"Google request failed, retrying in {wait_time}s: {e}")
                    await asyncio.sleep(wait_time)
                    continue
        
        # All retries failed
        raise ProviderError(f"Google request failed after {self.config.max_retries} retries: {last_error}", self.name)
    
    def _handle_error(self, error: Exception) -> ProviderError:
        """
        Handle and classify Google API errors.
        
        Args:
            error: The original error
            
        Returns:
            Appropriate ProviderError subclass
        """
        if isinstance(error, (AuthenticationError, RateLimitError, ProviderError)):
            return error
        
        error_str = str(error).lower()
        
        if "api_key_invalid" in error_str or "authentication" in error_str:
            return AuthenticationError(f"Google authentication failed: {error}", self.name)
        
        if "quota" in error_str or "rate" in error_str:
            return RateLimitError(f"Google rate limit exceeded: {error}", self.name)
        
        if "bad request" in error_str or "invalid" in error_str:
            return ProviderError(f"Google bad request: {error}", self.name, retryable=False)
        
        if "server error" in error_str or "internal" in error_str:
            return ProviderError(f"Google server error: {error}", self.name, retryable=True)
        
        if isinstance(error, asyncio.TimeoutError):
            return ProviderError(f"Google request timeout: {error}", self.name, retryable=True)
        
        # Generic error
        return ProviderError(f"Google error: {error}", self.name, retryable=True)