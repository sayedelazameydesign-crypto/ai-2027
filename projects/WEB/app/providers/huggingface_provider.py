"""
HuggingFace Provider Implementation

This module implements the HuggingFace provider for the AI Providers System,
supporting HuggingFace models with streaming and health check capabilities.
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


class HuggingfaceProvider(BaseProvider):
    """
    HuggingFace provider implementation.
    
    Supports text completion, streaming, and health monitoring
    for HuggingFace's model family using Inference API.
    """
    
    def __init__(self, config: ProviderConfig):
        """
        Initialize HuggingFace provider.
        
        Args:
            config: Provider configuration
        """
        super().__init__(config)
        
        # Set default base URL if not provided
        self.base_url = config.base_url or "https://api-inference.huggingface.co"
        
        # Default models if not specified in config
        self._default_models = [
            "microsoft/DialoGPT-large",
            "microsoft/DialoGPT-medium",
            "facebook/blenderbot-400M-distill",
            "microsoft/GODEL-v1_1-large-seq2seq",
            "HuggingFaceH4/zephyr-7b-beta"
        ]
        
        # HTTP session for connection pooling
        self._session: Optional[aiohttp.ClientSession] = None
        
        logger.info(f"Initialized HuggingFace provider with {len(self.supported_models)} models")
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.config.timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session
    
    async def generate_completion(self, request: CompletionRequest) -> CompletionResponse:
        """
        Generate text completion using HuggingFace API.
        
        Args:
            request: Standardized completion request
            
        Returns:
            Standardized completion response
            
        Raises:
            ProviderError: If the request fails
        """
        try:
            start_time = time.time()
            
            # Transform request to HuggingFace format
            hf_request = self._transform_request(request)
            
            # Make API call with retry logic
            response = await self._make_api_call(hf_request, request.model or self.get_default_model())
            
            # Transform response to standard format
            standard_response = self._transform_response(response, request.model or self.get_default_model(), time.time() - start_time)
            
            logger.debug(f"HuggingFace completion successful in {time.time() - start_time:.2f}s")
            return standard_response
            
        except Exception as e:
            logger.error(f"HuggingFace completion failed: {e}")
            raise self._handle_error(e)
    
    async def stream_completion(self, request: CompletionRequest) -> AsyncGenerator[str, None]:
        """
        Stream text completion using HuggingFace API.
        
        Note: HuggingFace Inference API doesn't support streaming natively,
        so this method simulates streaming by yielding the complete response.
        
        Args:
            request: Standardized completion request
            
        Yields:
            Chunks of completion text
            
        Raises:
            ProviderError: If the request fails
        """
        try:
            # Generate complete response
            response = await self.generate_completion(request)
            
            # Simulate streaming by yielding chunks
            if response.choices and response.choices[0]['message']['content']:
                content = response.choices[0]['message']['content']
                
                # Split into words and yield progressively
                words = content.split()
                chunk_size = max(1, len(words) // 10)  # Aim for ~10 chunks
                
                for i in range(0, len(words), chunk_size):
                    chunk = ' '.join(words[i:i + chunk_size])
                    if i + chunk_size < len(words):
                        chunk += ' '
                    yield chunk
                    await asyncio.sleep(0.1)  # Small delay to simulate streaming
            
            logger.debug("HuggingFace streaming completion successful")
            
        except Exception as e:
            logger.error(f"HuggingFace streaming failed: {e}")
            raise self._handle_error(e)
    
    async def health_check(self) -> bool:
        """
        Check if HuggingFace provider is healthy and accessible.
        
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
            
            hf_request = self._transform_request(test_request)
            
            # Set a shorter timeout for health checks
            response = await asyncio.wait_for(
                self._make_api_call(hf_request, test_request.model or self.get_default_model()),
                timeout=15.0  # HuggingFace can be slower
            )
            
            # Check if we got a valid response
            if response and isinstance(response, list) and len(response) > 0:
                logger.debug("HuggingFace health check passed")
                return True
            
            logger.warning("HuggingFace health check failed: Invalid response")
            return False
            
        except Exception as e:
            logger.warning(f"HuggingFace health check failed: {e}")
            return False
    
    def validate_config(self) -> bool:
        """
        Validate HuggingFace provider configuration.
        
        Returns:
            True if configuration is valid, False otherwise
        """
        try:
            # Check required fields
            if not self.config.api_key:
                logger.error("HuggingFace API key is required")
                return False
            
            # Validate API key format (HuggingFace keys start with 'hf_')
            if not self.config.api_key.startswith('hf_'):
                logger.warning("HuggingFace API key should start with 'hf_'")
            
            # Validate base URL
            if self.base_url and not self.base_url.startswith(('http://', 'https://')):
                logger.error("HuggingFace base URL must start with http:// or https://")
                return False
            
            # Validate timeout
            if self.config.timeout <= 0:
                logger.error("HuggingFace timeout must be positive")
                return False
            
            logger.debug("HuggingFace configuration validation passed")
            return True
            
        except Exception as e:
            logger.error(f"HuggingFace configuration validation failed: {e}")
            return False
    
    @property
    def supported_models(self) -> List[str]:
        """
        Return list of supported HuggingFace models.
        
        Returns:
            List of model names supported by HuggingFace
        """
        if self.config.models:
            return self.config.models
        return self._default_models
    
    def _transform_request(self, request: CompletionRequest) -> Dict[str, Any]:
        """
        Transform standardized request to HuggingFace API format.
        
        Args:
            request: Standardized completion request
            
        Returns:
            HuggingFace API request dictionary
        """
        # Convert messages to a single input text
        input_text = ""
        for msg in request.messages:
            if msg.role == "system":
                input_text += f"System: {msg.content}\n"
            elif msg.role == "user":
                input_text += f"User: {msg.content}\n"
            elif msg.role == "assistant":
                input_text += f"Assistant: {msg.content}\n"
        
        # Add prompt for assistant response
        input_text += "Assistant:"
        
        # Build HuggingFace request
        hf_request = {
            "inputs": input_text.strip(),
            "parameters": {}
        }
        
        # Add optional parameters
        if request.max_tokens is not None:
            hf_request["parameters"]["max_new_tokens"] = request.max_tokens
        
        if request.temperature is not None:
            hf_request["parameters"]["temperature"] = request.temperature
        
        if request.top_p is not None:
            hf_request["parameters"]["top_p"] = request.top_p
        
        if request.stop:
            hf_request["parameters"]["stop_sequences"] = request.stop
        
        # Set some reasonable defaults
        hf_request["parameters"]["return_full_text"] = False
        hf_request["parameters"]["do_sample"] = True
        
        return hf_request
    
    def _transform_response(self, response: List[Dict[str, Any]], model_name: str, response_time: float) -> CompletionResponse:
        """
        Transform HuggingFace API response to standardized format.
        
        Args:
            response: HuggingFace API response
            model_name: Name of the model used
            response_time: Time taken for the request
            
        Returns:
            Standardized completion response
        """
        # Extract content from response
        content = ""
        if response and len(response) > 0:
            if "generated_text" in response[0]:
                content = response[0]["generated_text"].strip()
            elif "text" in response[0]:
                content = response[0]["text"].strip()
        
        # Build choices in standard format
        choices = [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": content
            },
            "finish_reason": "stop"
        }]
        
        # Build usage information (HuggingFace doesn't provide detailed token counts)
        # Estimate based on content length
        prompt_tokens = 0  # Not provided by HuggingFace
        completion_tokens = len(content.split()) if content else 0
        
        usage = Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens
        )
        
        return CompletionResponse(
            id=f"hf-{int(time.time())}-{hash(content) % 10000}",
            model=model_name,
            provider=self.name,
            choices=choices,
            usage=usage,
            created=int(time.time())
        )
    
    async def _make_api_call(self, hf_request: Dict[str, Any], model_name: str) -> List[Dict[str, Any]]:
        """
        Make API call to HuggingFace with retry logic.
        
        Args:
            hf_request: HuggingFace API request dictionary
            model_name: Name of the model to use
            
        Returns:
            HuggingFace API response
            
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
                
                url = f"{self.base_url}/models/{model_name}"
                
                async with session.post(url, json=hf_request, headers=headers) as response:
                    response_text = await response.text()
                    
                    if response.status == 200:
                        return json.loads(response_text)
                    
                    # Handle specific error codes
                    if response.status == 401:
                        raise AuthenticationError(f"HuggingFace authentication failed: {response_text}", self.name)
                    
                    if response.status == 429:
                        retry_after = response.headers.get('Retry-After')
                        if attempt < self.config.max_retries:
                            wait_time = int(retry_after) if retry_after else (self.config.retry_delay * (2 ** attempt))
                            logger.warning(f"HuggingFace rate limited, waiting {wait_time}s before retry {attempt + 1}")
                            await asyncio.sleep(wait_time)
                            continue
                        else:
                            raise RateLimitError(f"HuggingFace rate limit exceeded: {response_text}", self.name, int(retry_after) if retry_after else None)
                    
                    # Handle model loading (503 Service Unavailable)
                    if response.status == 503:
                        if "loading" in response_text.lower():
                            if attempt < self.config.max_retries:
                                wait_time = 20  # Model loading can take time
                                logger.warning(f"HuggingFace model loading, waiting {wait_time}s before retry {attempt + 1}")
                                await asyncio.sleep(wait_time)
                                continue
                    
                    # Other HTTP errors
                    raise ProviderError(f"HuggingFace API error: {response.status} - {response_text}", self.name)
                
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
                    logger.warning(f"HuggingFace request failed, retrying in {wait_time}s: {e}")
                    await asyncio.sleep(wait_time)
                    continue
        
        # All retries failed
        raise ProviderError(f"HuggingFace request failed after {self.config.max_retries} retries: {last_error}", self.name)
    
    def _handle_error(self, error: Exception) -> ProviderError:
        """
        Handle and classify HuggingFace API errors.
        
        Args:
            error: The original error
            
        Returns:
            Appropriate ProviderError subclass
        """
        if isinstance(error, (AuthenticationError, RateLimitError, ProviderError)):
            return error
        
        error_str = str(error).lower()
        
        if "authentication" in error_str or "unauthorized" in error_str:
            return AuthenticationError(f"HuggingFace authentication failed: {error}", self.name)
        
        if "rate limit" in error_str or "quota" in error_str:
            return RateLimitError(f"HuggingFace rate limit exceeded: {error}", self.name)
        
        if "bad request" in error_str or "invalid" in error_str:
            return ProviderError(f"HuggingFace bad request: {error}", self.name, retryable=False)
        
        if "server error" in error_str or "internal" in error_str or "loading" in error_str:
            return ProviderError(f"HuggingFace server error: {error}", self.name, retryable=True)
        
        if isinstance(error, asyncio.TimeoutError):
            return ProviderError(f"HuggingFace request timeout: {error}", self.name, retryable=True)
        
        # Generic error
        return ProviderError(f"HuggingFace error: {error}", self.name, retryable=True)
    
    async def __aenter__(self):
        """Async context manager entry."""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self._session and not self._session.closed:
            await self._session.close()