"""
Enhanced Request Handler and Orchestration System

This module implements intelligent request processing with provider selection,
response normalization, comprehensive telemetry, and advanced optimization features
including PII redaction, semantic routing, cost management, and speculative execution.
"""

import asyncio
import time
import uuid
from typing import Dict, List, Optional, Any, AsyncGenerator
from datetime import datetime
import logging
from dataclasses import dataclass
from enum import Enum

from .fallback_manager import FallbackManager, ProviderPriority
from .circuit_breaker import circuit_breaker_manager
from .telemetry import telemetry_manager, RequestMetrics
from ..providers.base import (
    BaseProvider, CompletionRequest, CompletionResponse, 
    ProviderError, AuthenticationError, RateLimitError
)
from ..security.pii_redactor import pii_redactor, PIIMatch
from ..intelligence.semantic_router import semantic_router
from ..intelligence.cost_manager import cost_manager
from ..intelligence.speculative_engine import speculative_engine, SpeculativeConfig


logger = logging.getLogger(__name__)


class RequestStatus(Enum):
    """Request processing status."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class RequestContext:
    """Context information for request processing."""
    request_id: str
    start_time: datetime
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    client_ip: Optional[str] = None
    user_agent: Optional[str] = None
    preferred_provider: Optional[str] = None
    max_retries: int = 3
    timeout: float = 60.0
    enable_pii_redaction: bool = True
    enable_semantic_routing: bool = True
    enable_cost_management: bool = True
    enable_speculative_execution: bool = True
    cost_limit: Optional[float] = None


class PromptTemplate:
    """Template system for normalizing prompts across different models."""
    
    def __init__(self):
        """Initialize prompt template system."""
        self.templates = {
            'openai': self._openai_template,
            'anthropic': self._anthropic_template,
            'google': self._google_template,
            'default': self._default_template
        }
    
    def normalize_prompt(self, request: CompletionRequest, provider_name: str) -> CompletionRequest:
        """
        Normalize prompt for specific provider.
        
        Args:
            request: Original completion request
            provider_name: Target provider name
            
        Returns:
            Normalized completion request
        """
        template_func = self.templates.get(provider_name, self.templates['default'])
        return template_func(request)
    
    def _openai_template(self, request: CompletionRequest) -> CompletionRequest:
        """OpenAI-specific prompt normalization."""
        # OpenAI handles messages as-is
        return request
    
    def _anthropic_template(self, request: CompletionRequest) -> CompletionRequest:
        """Anthropic-specific prompt normalization."""
        # Anthropic prefers system messages to be separate
        normalized_messages = []
        system_content = []
        
        for msg in request.messages:
            if msg.role == "system":
                system_content.append(msg.content)
            else:
                normalized_messages.append(msg)
        
        # Add system content as first user message if present
        if system_content:
            system_text = "\n\n".join(system_content)
            if normalized_messages and normalized_messages[0].role == "user":
                normalized_messages[0].content = f"{system_text}\n\n{normalized_messages[0].content}"
            else:
                from ..providers.base import ChatMessage
                normalized_messages.insert(0, ChatMessage(role="user", content=system_text))
        
        return CompletionRequest(
            messages=normalized_messages,
            model=request.model,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            top_p=request.top_p,
            stream=request.stream,
            stop=request.stop
        )
    
    def _google_template(self, request: CompletionRequest) -> CompletionRequest:
        """Google Gemini-specific prompt normalization."""
        # Google prefers alternating user/assistant pattern
        normalized_messages = []
        
        for i, msg in enumerate(request.messages):
            if msg.role == "system":
                # Convert system messages to user messages
                normalized_messages.append(ChatMessage(role="user", content=f"System: {msg.content}"))
            else:
                normalized_messages.append(msg)
        
        return CompletionRequest(
            messages=normalized_messages,
            model=request.model,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            top_p=request.top_p,
            stream=request.stream,
            stop=request.stop
        )
    
    def _default_template(self, request: CompletionRequest) -> CompletionRequest:
        """Default prompt normalization."""
        return request


class ResponseNormalizer:
    """Normalizes responses from different providers to unified format."""
    
    def __init__(self):
        """Initialize response normalizer."""
        self.normalizers = {
            'openai': self._normalize_openai,
            'anthropic': self._normalize_anthropic,
            'google': self._normalize_google,
            'default': self._normalize_default
        }
    
    def normalize_response(self, response: CompletionResponse, provider_name: str) -> CompletionResponse:
        """
        Normalize provider response to standard format.
        
        Args:
            response: Provider-specific response
            provider_name: Provider name
            
        Returns:
            Normalized response
        """
        normalizer_func = self.normalizers.get(provider_name, self.normalizers['default'])
        return normalizer_func(response)
    
    def _normalize_openai(self, response: CompletionResponse) -> CompletionResponse:
        """Normalize OpenAI response."""
        # OpenAI responses are already in standard format
        return response
    
    def _normalize_anthropic(self, response: CompletionResponse) -> CompletionResponse:
        """Normalize Anthropic response."""
        # Ensure consistent choice format
        normalized_choices = []
        for choice in response.choices:
            normalized_choice = {
                "index": choice.get("index", 0),
                "message": {
                    "role": "assistant",
                    "content": choice.get("message", {}).get("content", "")
                },
                "finish_reason": choice.get("finish_reason", "stop")
            }
            normalized_choices.append(normalized_choice)
        
        return CompletionResponse(
            id=response.id,
            model=response.model,
            provider=response.provider,
            choices=normalized_choices,
            usage=response.usage,
            created=response.created
        )
    
    def _normalize_google(self, response: CompletionResponse) -> CompletionResponse:
        """Normalize Google response."""
        # Similar normalization as Anthropic
        return self._normalize_anthropic(response)
    
    def _normalize_default(self, response: CompletionResponse) -> CompletionResponse:
        """Default response normalization."""
        return response


class StreamingNormalizer:
    """Normalizes streaming responses from different providers."""
    
    def __init__(self):
        """Initialize streaming normalizer."""
        pass
    
    async def normalize_stream(self, stream: AsyncGenerator[str, None], provider_name: str) -> AsyncGenerator[str, None]:
        """
        Normalize streaming response.
        
        Args:
            stream: Provider-specific stream
            provider_name: Provider name
            
        Yields:
            Normalized stream chunks
        """
        async for chunk in stream:
            # Basic normalization - ensure chunks are strings
            if isinstance(chunk, str):
                yield chunk
            elif hasattr(chunk, 'content'):
                yield str(chunk.content)
            else:
                yield str(chunk)


class RequestHandler:
    """
    Enhanced intelligent request handler with advanced optimization.
    
    Handles request processing with PII redaction, semantic routing,
    cost management, speculative execution, and comprehensive telemetry.
    """
    
    def __init__(self, fallback_manager: FallbackManager):
        """
        Initialize enhanced request handler.
        
        Args:
            fallback_manager: Fallback manager instance
        """
        self.fallback_manager = fallback_manager
        self.prompt_template = PromptTemplate()
        self.response_normalizer = ResponseNormalizer()
        self.streaming_normalizer = StreamingNormalizer()
        
        # Active requests tracking
        self.active_requests: Dict[str, RequestContext] = {}
        
        # Performance metrics
        self.request_count = 0
        self.total_cost = 0.0
        self.pii_redactions = 0
        
        logger.info("Enhanced request handler initialized")
    
    async def process_completion(self, 
                               request: CompletionRequest, 
                               context: Optional[RequestContext] = None) -> CompletionResponse:
        """
        Process completion request with advanced optimization.
        
        Args:
            request: Completion request
            context: Optional request context
            
        Returns:
            Normalized completion response
            
        Raises:
            ProviderError: If all providers fail
        """
        # Create context if not provided
        if context is None:
            context = RequestContext(
                request_id=str(uuid.uuid4()),
                start_time=datetime.now()
            )
        
        # Track active request
        self.active_requests[context.request_id] = context
        self.request_count += 1
        
        try:
            # Start telemetry
            metrics = RequestMetrics(
                request_id=context.request_id,
                start_time=context.start_time,
                user_id=context.user_id,
                request_type="completion"
            )
            
            # Step 1: PII Redaction
            original_request = request
            pii_matches = []
            
            if context.enable_pii_redaction:
                request, pii_matches = await self._redact_pii_from_request(request)
                if pii_matches:
                    self.pii_redactions += len(pii_matches)
                    logger.info(f"Redacted {len(pii_matches)} PII instances from request {context.request_id}")
            
            # Step 2: Semantic Analysis and Routing
            recommended_providers = []
            if context.enable_semantic_routing:
                available_providers = await self.fallback_manager.get_provider_priority_list()
                available_names = [p.provider_name for p in available_providers]
                
                recommendations = await semantic_router.route_request(
                    request, available_names, context.cost_limit
                )
                recommended_providers = [r.provider_name for r in recommendations]
                
                logger.debug(f"Semantic routing recommendations: {recommended_providers}")
            
            # Step 3: Cost Management Check
            if context.enable_cost_management and context.user_id:
                # Estimate tokens for cost calculation
                estimated_tokens = self._estimate_request_tokens(request)
                
                # Check with top recommended provider or fallback to first available
                target_provider = recommended_providers[0] if recommended_providers else "openai"
                target_model = "gpt-3.5-turbo"  # Default model
                
                can_proceed, cost_estimate, warnings = await cost_manager.check_request_cost(
                    context.user_id, target_provider, target_model, 
                    estimated_tokens, estimated_tokens // 2
                )
                
                if not can_proceed:
                    raise ProviderError(f"Budget exceeded: {'; '.join(warnings)}", "cost_manager")
                
                if warnings:
                    logger.warning(f"Cost warnings for user {context.user_id}: {warnings}")
                
                metrics.cost_estimate = cost_estimate.total_cost
            
            # Step 4: Provider Selection and Execution
            if context.enable_speculative_execution and speculative_engine:
                # Use speculative execution
                result = await speculative_engine.execute_speculative_request(
                    request, context.request_id, context.user_id
                )
                
                response = result.response
                provider_used = result.provider_used
                response_time = result.total_time_ms / 1000.0
                
                metrics.speculative_requests = result.requests_made
                metrics.cancelled_requests = len(result.cancelled_requests)
                
            else:
                # Traditional execution with recommended providers
                response, provider_used, response_time = await self._execute_traditional_request(
                    request, context, recommended_providers
                )
            
            # Step 5: Response Processing
            # Restore PII in response if needed
            if pii_matches and context.enable_pii_redaction:
                response = await self._restore_pii_in_response(response, pii_matches)
            
            # Normalize response
            normalized_response = self.response_normalizer.normalize_response(response, provider_used)
            
            # Step 6: Cost Recording
            if context.enable_cost_management and context.user_id:
                actual_cost = await cost_manager.record_request_completion(
                    context.user_id, provider_used, normalized_response.model,
                    normalized_response.usage.prompt_tokens,
                    normalized_response.usage.completion_tokens,
                    context.request_id
                )
                self.total_cost += actual_cost
                metrics.actual_cost = actual_cost
            
            # Update metrics
            metrics.provider_used = provider_used
            metrics.response_time = response_time
            metrics.tokens_used = normalized_response.usage.total_tokens
            metrics.success = True
            
            # Record telemetry
            telemetry_manager.record_request(metrics)
            
            logger.info(
                f"Request {context.request_id} completed with {provider_used} "
                f"in {response_time:.2f}s (PII: {len(pii_matches)}, Cost: ${metrics.actual_cost:.6f})"
            )
            
            return normalized_response
            
        except Exception as e:
            # Record failure metrics
            metrics.success = False
            metrics.error_message = str(e)
            telemetry_manager.record_request(metrics)
            
            logger.error(f"Request {context.request_id} failed: {e}")
            raise
            
        finally:
            # Remove from active requests
            if context.request_id in self.active_requests:
                del self.active_requests[context.request_id]
    
    async def process_streaming_completion(self, 
                                         request: CompletionRequest, 
                                         context: Optional[RequestContext] = None) -> AsyncGenerator[str, None]:
        """
        Process streaming completion request.
        
        Args:
            request: Completion request with stream=True
            context: Optional request context
            
        Yields:
            Normalized stream chunks
            
        Raises:
            ProviderError: If all providers fail
        """
        # Create context if not provided
        if context is None:
            context = RequestContext(
                request_id=str(uuid.uuid4()),
                start_time=datetime.now()
            )
        
        # Track active request
        self.active_requests[context.request_id] = context
        
        try:
            # Get available providers
            providers = await self.fallback_manager.get_provider_priority_list()
            
            if not providers:
                raise ProviderError("No providers available", "system")
            
            # Try first available provider for streaming
            for provider_info in providers:
                provider_name = provider_info.provider_name
                
                try:
                    # Check circuit breaker
                    circuit_breaker = circuit_breaker_manager.get_circuit_breaker(provider_name)
                    if not circuit_breaker.is_available():
                        continue
                    
                    # Get provider instance
                    provider = await self.fallback_manager.get_provider(provider_name)
                    if not provider:
                        continue
                    
                    # Normalize request
                    normalized_request = self.prompt_template.normalize_prompt(request, provider_name)
                    normalized_request.stream = True
                    
                    # Start streaming through circuit breaker
                    start_time = time.time()
                    
                    stream = await circuit_breaker.call(
                        provider.stream_completion,
                        normalized_request
                    )
                    
                    # Normalize and yield stream chunks
                    chunk_count = 0
                    async for chunk in self.streaming_normalizer.normalize_stream(stream, provider_name):
                        chunk_count += 1
                        yield chunk
                    
                    response_time = time.time() - start_time
                    
                    # Record success metrics
                    metrics = RequestMetrics(
                        request_id=context.request_id,
                        start_time=context.start_time,
                        user_id=context.user_id,
                        request_type="streaming",
                        provider_used=provider_name,
                        response_time=response_time,
                        success=True
                    )
                    telemetry_manager.record_request(metrics)
                    
                    await self.fallback_manager.record_provider_success(provider_name, response_time)
                    
                    logger.info(f"Streaming request {context.request_id} completed with {provider_name}")
                    return
                    
                except Exception as e:
                    logger.warning(f"Streaming failed with {provider_name}: {e}")
                    await self.fallback_manager.record_provider_failure(provider_name, str(e))
                    continue
            
            # All providers failed
            raise ProviderError("All providers failed for streaming", "system")
            
        finally:
            # Remove from active requests
            if context.request_id in self.active_requests:
                del self.active_requests[context.request_id]
    
    async def health_check_all_providers(self) -> Dict[str, bool]:
        """
        Perform health check on all providers.
        
        Returns:
            Dictionary mapping provider names to health status
        """
        providers = await self.fallback_manager.get_all_providers()
        health_results = {}
        
        for provider_name, provider in providers.items():
            try:
                is_healthy = await provider.health_check()
                health_results[provider_name] = is_healthy
                
                if is_healthy:
                    await self.fallback_manager.record_provider_success(provider_name, 0.1)
                else:
                    await self.fallback_manager.record_provider_failure(provider_name, "Health check failed")
                    
            except Exception as e:
                health_results[provider_name] = False
                await self.fallback_manager.record_provider_failure(provider_name, f"Health check error: {e}")
        
        return health_results
    
    def get_active_requests(self) -> Dict[str, RequestContext]:
        """
        Get currently active requests.
        
        Returns:
            Dictionary of active request contexts
        """
        return self.active_requests.copy()
    
    def get_request_stats(self) -> Dict[str, Any]:
        """
        Get comprehensive request processing statistics.
        
        Returns:
            Dictionary with request statistics
        """
        base_stats = {
            'active_requests': len(self.active_requests),
            'total_requests_processed': self.request_count,
            'total_cost_incurred': self.total_cost,
            'pii_redactions_performed': self.pii_redactions,
            'success_rate': telemetry_manager.get_success_rate(),
            'average_response_time': telemetry_manager.get_average_response_time(),
            'provider_stats': telemetry_manager.get_provider_stats()
        }
        
        # Add semantic router stats if available
        if semantic_router:
            base_stats['semantic_routing'] = semantic_router.get_analysis_stats()
        
        # Add speculative engine stats if available
        if speculative_engine:
            base_stats['speculative_execution'] = speculative_engine.get_performance_stats()
        
        return base_stats
    
    async def _redact_pii_from_request(self, request: CompletionRequest) -> Tuple[CompletionRequest, List[PIIMatch]]:
        """Redact PII from request messages."""
        all_pii_matches = []
        redacted_messages = []
        
        for message in request.messages:
            redacted_content, pii_matches = pii_redactor.redact_text(message.content)
            all_pii_matches.extend(pii_matches)
            
            # Create new message with redacted content
            from ..providers.base import ChatMessage
            redacted_message = ChatMessage(
                role=message.role,
                content=redacted_content
            )
            redacted_messages.append(redacted_message)
        
        # Create new request with redacted messages
        redacted_request = CompletionRequest(
            messages=redacted_messages,
            model=request.model,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            top_p=request.top_p,
            stream=request.stream,
            stop=request.stop
        )
        
        return redacted_request, all_pii_matches
    
    async def _restore_pii_in_response(self, response: CompletionResponse, 
                                     pii_matches: List[PIIMatch]) -> CompletionResponse:
        """Restore PII in response content."""
        if not pii_matches:
            return response
        
        # Restore PII in response choices
        restored_choices = []
        for choice in response.choices:
            if 'message' in choice and 'content' in choice['message']:
                original_content = choice['message']['content']
                
                # Restore PII using the redactor
                restored_content = pii_redactor.restore_text(original_content, pii_matches)
                
                restored_choice = choice.copy()
                restored_choice['message'] = choice['message'].copy()
                restored_choice['message']['content'] = restored_content
                
                restored_choices.append(restored_choice)
            else:
                restored_choices.append(choice)
        
        # Create new response with restored content
        return CompletionResponse(
            id=response.id,
            model=response.model,
            provider=response.provider,
            choices=restored_choices,
            usage=response.usage,
            created=response.created
        )
    
    def _estimate_request_tokens(self, request: CompletionRequest) -> int:
        """Estimate token count for request."""
        total_chars = sum(len(msg.content) for msg in request.messages)
        # Rough estimation: 1 token ≈ 4 characters
        return total_chars // 4
    
    async def _execute_traditional_request(self, request: CompletionRequest, 
                                         context: RequestContext,
                                         recommended_providers: List[str]) -> Tuple[CompletionResponse, str, float]:
        """Execute request using traditional fallback approach."""
        # Get available providers in priority order
        providers = await self.fallback_manager.get_provider_priority_list()
        
        # Reorder based on semantic recommendations if available
        if recommended_providers:
            # Create a mapping of provider names to their info
            provider_map = {p.provider_name: p for p in providers}
            
            # Reorder providers based on recommendations
            reordered_providers = []
            for rec_name in recommended_providers:
                if rec_name in provider_map:
                    reordered_providers.append(provider_map[rec_name])
            
            # Add any remaining providers
            for provider_info in providers:
                if provider_info not in reordered_providers:
                    reordered_providers.append(provider_info)
            
            providers = reordered_providers
        
        if not providers:
            raise ProviderError("No providers available", "system")
        
        last_error = None
        
        # Try providers in order
        for provider_info in providers:
            provider_name = provider_info.provider_name
            
            try:
                # Check circuit breaker
                circuit_breaker = circuit_breaker_manager.get_circuit_breaker(provider_name)
                if not circuit_breaker.is_available():
                    logger.debug(f"Skipping {provider_name} - circuit breaker open")
                    continue
                
                # Get provider instance
                provider = await self.fallback_manager.get_provider(provider_name)
                if not provider:
                    continue
                
                # Normalize request for this provider
                normalized_request = self.prompt_template.normalize_prompt(request, provider_name)
                
                # Execute request through circuit breaker
                start_time = time.time()
                
                response = await circuit_breaker.call(
                    provider.generate_completion,
                    normalized_request
                )
                
                response_time = time.time() - start_time
                
                # Update fallback manager with success
                await self.fallback_manager.record_provider_success(provider_name, response_time)
                
                return response, provider_name, response_time
                
            except Exception as e:
                last_error = e
                response_time = time.time() - start_time if 'start_time' in locals() else 0
                
                # Record failure
                await self.fallback_manager.record_provider_failure(provider_name, str(e))
                
                logger.warning(f"Provider {provider_name} failed for request {context.request_id}: {e}")
                
                # Don't retry on authentication errors
                if isinstance(e, AuthenticationError):
                    break
                
                continue
        
        # All providers failed
        raise ProviderError(f"All providers failed. Last error: {last_error}", "system")