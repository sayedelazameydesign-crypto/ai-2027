"""
Base Provider Interface for AI Providers System

© 2026 SAYED_ELAZAMEY - All Rights Reserved
PROPRIETARY SOFTWARE - UNAUTHORIZED USE PROHIBITED

This module defines the abstract base class that all AI providers must implement,
ensuring consistent interface across different AI service providers.

This software is protected by copyright law. Unauthorized reproduction,
distribution, or modification is strictly prohibited.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, AsyncGenerator, List
from pydantic import BaseModel, Field, validator
from datetime import datetime
import uuid
import re


class ProviderConfig(BaseModel):
    """Configuration model for AI providers with validation."""
    
    api_key: str = Field(..., min_length=1)
    base_url: Optional[str] = None
    timeout: int = Field(default=30, ge=1, le=300)
    max_retries: int = Field(default=3, ge=0, le=10)
    retry_delay: float = Field(default=1.0, ge=0.1, le=10.0)
    rate_limit: Optional[int] = None
    models: List[str] = Field(default_factory=list)
    
    @validator('api_key')
    def validate_api_key(cls, v):
        """Validate API key is not empty or whitespace."""
        if not v or v.isspace():
            raise ValueError("API key cannot be empty or whitespace")
        return v.strip()
    
    @validator('base_url')
    def validate_base_url(cls, v):
        """Validate base URL format if provided."""
        if v and not v.startswith(('http://', 'https://')):
            raise ValueError("Base URL must start with http:// or https://")
        return v


class ChatMessage(BaseModel):
    """Chat message model for conversations."""
    
    role: str = Field(..., pattern="^(system|user|assistant)$")
    content: str = Field(..., min_length=1)
    
    @validator('content')
    def validate_content(cls, v):
        """Validate content is not empty."""
        if not v or v.isspace():
            raise ValueError("Message content cannot be empty")
        return v.strip()


class CompletionRequest(BaseModel):
    """Standardized request model for text completion."""
    
    messages: List[ChatMessage]
    model: Optional[str] = None
    max_tokens: Optional[int] = Field(None, ge=1, le=4096)
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    top_p: Optional[float] = Field(None, ge=0.0, le=1.0)
    stream: bool = False
    stop: Optional[List[str]] = None
    
    @validator('messages')
    def validate_messages(cls, v):
        """Validate messages list is not empty."""
        if not v:
            raise ValueError("Messages list cannot be empty")
        return v


class Usage(BaseModel):
    """Token usage information."""
    
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    
    @validator('total_tokens')
    def validate_total_tokens(cls, v, values):
        """Validate total tokens equals sum of prompt and completion tokens."""
        prompt = values.get('prompt_tokens', 0)
        completion = values.get('completion_tokens', 0)
        if v != prompt + completion:
            raise ValueError("Total tokens must equal prompt_tokens + completion_tokens")
        return v


class SecurityScanner:
    """Security scanner for detecting dangerous patterns in content."""
    
    DANGEROUS_PATTERNS = [
        r'(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*["\']?[a-zA-Z0-9_-]{10,}',
        r'(?i)eval\s*\(',
        r'(?i)exec\s*\(',
        r'(?i)__import__\s*\(',
        r'(?i)subprocess\.',
        r'(?i)os\.system',
        r'sk-[a-zA-Z0-9]{48}',  # OpenAI API key pattern
        r'AIza[0-9A-Za-z_-]{35}',  # Google API key pattern
        r'(?i)rm\s+-rf',  # Dangerous shell commands
        r'(?i)DROP\s+TABLE',  # SQL injection patterns
        r'(?i)<script[^>]*>',  # XSS patterns
    ]
    
    @classmethod
    def scan_content(cls, content: str) -> List[str]:
        """
        Scan content for security violations.
        
        Args:
            content: The content to scan
            
        Returns:
            List of security violation descriptions
        """
        violations = []
        for pattern in cls.DANGEROUS_PATTERNS:
            if re.search(pattern, content):
                violations.append(f"Potential security risk detected: {pattern}")
        return violations


class CompletionResponse(BaseModel):
    """Standardized response model for text completion."""
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(datetime.now().timestamp()))
    model: str
    provider: str
    choices: List[Dict[str, Any]]
    usage: Usage
    
    def __init__(self, **data):
        """Initialize response with security scanning."""
        # Security scan before creating response
        if 'choices' in data:
            for choice in data['choices']:
                if 'message' in choice and 'content' in choice['message']:
                    violations = SecurityScanner.scan_content(choice['message']['content'])
                    if violations:
                        raise ValueError(f"Security violations detected: {violations}")
        super().__init__(**data)
    
    class Config:
        # Prevent any provider-specific objects from being serialized
        arbitrary_types_allowed = False


class HealthStatus(BaseModel):
    """Health status model for providers."""
    
    provider: str
    healthy: bool
    last_check: datetime = Field(default_factory=datetime.now)
    response_time_ms: Optional[float] = None
    error_message: Optional[str] = None
    failure_count: int = 0


class ProviderError(Exception):
    """Base exception for provider-related errors."""
    
    def __init__(self, message: str, provider: str, retryable: bool = True):
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable
        self.timestamp = datetime.now()


class AuthenticationError(ProviderError):
    """Authentication-related errors (non-retryable)."""
    
    def __init__(self, message: str, provider: str):
        super().__init__(message, provider, retryable=False)


class RateLimitError(ProviderError):
    """Rate limit errors (retryable with delay)."""
    
    def __init__(self, message: str, provider: str, retry_after: Optional[int] = None):
        super().__init__(message, provider, retryable=True)
        self.retry_after = retry_after


class ConfigurationError(ProviderError):
    """Configuration-related errors (non-retryable)."""
    
    def __init__(self, message: str, provider: str):
        super().__init__(message, provider, retryable=False)


class BaseProvider(ABC):
    """
    Abstract base class for all AI providers.
    
    This class defines the contract that all AI providers must implement,
    ensuring consistent interface across different AI service providers.
    """
    
    def __init__(self, config: ProviderConfig):
        """
        Initialize the provider with configuration.
        
        Args:
            config: Provider configuration
        """
        self.config = config
        self.name = self.__class__.__name__.replace('Provider', '').lower()
        
        # Validate configuration on initialization
        if not self.validate_config():
            raise ConfigurationError(f"Invalid configuration for {self.name}", self.name)
    
    @abstractmethod
    async def generate_completion(self, request: CompletionRequest) -> CompletionResponse:
        """
        Generate a text completion using the provider's API.
        
        Args:
            request: Standardized completion request
            
        Returns:
            Standardized completion response
            
        Raises:
            ProviderError: If the request fails
        """
        pass
    
    @abstractmethod
    async def stream_completion(self, request: CompletionRequest) -> AsyncGenerator[str, None]:
        """
        Stream a text completion using the provider's API.
        
        Args:
            request: Standardized completion request
            
        Yields:
            Chunks of completion text
            
        Raises:
            ProviderError: If the request fails
        """
        pass
    
    @abstractmethod
    async def health_check(self) -> bool:
        """
        Check if the provider is healthy and accessible.
        
        Returns:
            True if provider is healthy, False otherwise
        """
        pass
    
    @abstractmethod
    def validate_config(self) -> bool:
        """
        Validate the provider configuration.
        
        Returns:
            True if configuration is valid, False otherwise
        """
        pass
    
    @property
    @abstractmethod
    def supported_models(self) -> List[str]:
        """
        Return list of supported models for this provider.
        
        Returns:
            List of model names supported by this provider
        """
        pass
    
    def get_default_model(self) -> Optional[str]:
        """
        Get the default model for this provider.
        
        Returns:
            Default model name or None if no models available
        """
        models = self.supported_models
        return models[0] if models else None
    
    def __str__(self) -> str:
        """String representation of the provider."""
        return f"{self.__class__.__name__}(name={self.name})"
    
    def __repr__(self) -> str:
        """Detailed string representation of the provider."""
        return f"{self.__class__.__name__}(name={self.name}, config={self.config})"