"""Configuration Management System"""

from typing import Dict, Optional, List
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings
from enum import Enum
import os

class ProviderType(str, Enum):
    """Supported AI Providers"""
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GOOGLE = "google"
    HUGGINGFACE = "huggingface"
    OPENROUTER = "openrouter"
    DEEPSEEK = "deepseek"
    TOGETHER = "together"
    GROQ = "groq"
    COHERE = "cohere"
    MISTRAL = "mistral"

class ModelConfig(BaseModel):
    """Configuration for a single model provider"""
    provider: ProviderType
    model_name: str
    api_key: str
    base_url: Optional[str] = None
    max_tokens: int = 4000
    temperature: float = 0.7

class Settings(BaseSettings):
    """Application Settings"""
    
    # App Info
    app_name: str = "Web Dev Agent"
    app_version: str = "2.0.0"
    environment: str = "production"
    debug: bool = False
    
    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 4
    
    # Primary Provider
    primary_provider: str = "anthropic"
    fallback_providers: str = ""
    auto_fallback: bool = True
    cost_optimization: bool = True
    speed_priority: bool = False
    
    # Anthropic
    anthropic_api_key: Optional[str] = None
    anthropic_model: str = "claude-sonnet-4-20250514"
    
    # OpenAI
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o"
    
    # Google
    google_api_key: Optional[str] = None
    google_model: str = "gemini-1.5-pro"
    
    # HuggingFace
    huggingface_api_key: Optional[str] = None
    huggingface_model: str = "meta-llama/Meta-Llama-3-70B-Instruct"
    huggingface_base_url: str = "https://api-inference.huggingface.co/models"
    
    # OpenRouter
    openrouter_api_key: Optional[str] = None
    openrouter_model: str = "anthropic/claude-3.5-sonnet"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    
    # DeepSeek
    deepseek_api_key: Optional[str] = None
    deepseek_model: str = "deepseek-coder"
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    
    # Together
    together_api_key: Optional[str] = None
    together_model: str = "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo"
    together_base_url: str = "https://api.together.xyz/v1"
    
    # Groq
    groq_api_key: Optional[str] = None
    groq_model: str = "llama-3.1-70b-versatile"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    
    # Cohere
    cohere_api_key: Optional[str] = None
    cohere_model: str = "command-r-plus"
    
    # Mistral
    mistral_api_key: Optional[str] = None
    mistral_model: str = "mistral-large-latest"
    mistral_base_url: str = "https://api.mistral.ai/v1"
    
    # Security
    secret_key: str = "change-this-secret-key-in-production"
    allowed_origins: str = "*"
    cors_enabled: bool = True
    
    # Rate Limiting
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 60
    
    # Cache
    cache_enabled: bool = True
    redis_host: str = "localhost"
    redis_port: int = 6379
    
    # File Storage
    upload_dir: str = "/tmp/uploads"
    output_dir: str = "/tmp/outputs"
    preview_dir: str = "/tmp/previews"
    max_upload_size: int = 10485760  # 10MB
    
    # Features
    enable_preview: bool = True
    enable_security_scan: bool = True
    enable_performance_optimization: bool = True
    
    class Config:
        env_file = ".env"
        case_sensitive = False

class ConfigManager:
    """Manages system configuration and provider settings"""
    
    def __init__(self):
        self.settings = Settings()
        self.providers = self._initialize_providers()
    
    def _initialize_providers(self) -> Dict[ProviderType, ModelConfig]:
        """Initialize all configured providers"""
        providers = {}
        
        # Anthropic
        if self.settings.anthropic_api_key:
            providers[ProviderType.ANTHROPIC] = ModelConfig(
                provider=ProviderType.ANTHROPIC,
                model_name=self.settings.anthropic_model,
                api_key=self.settings.anthropic_api_key
            )
        
        # OpenAI
        if self.settings.openai_api_key:
            providers[ProviderType.OPENAI] = ModelConfig(
                provider=ProviderType.OPENAI,
                model_name=self.settings.openai_model,
                api_key=self.settings.openai_api_key
            )
        
        # Google
        if self.settings.google_api_key:
            providers[ProviderType.GOOGLE] = ModelConfig(
                provider=ProviderType.GOOGLE,
                model_name=self.settings.google_model,
                api_key=self.settings.google_api_key
            )
        
        # HuggingFace
        if self.settings.huggingface_api_key:
            providers[ProviderType.HUGGINGFACE] = ModelConfig(
                provider=ProviderType.HUGGINGFACE,
                model_name=self.settings.huggingface_model,
                api_key=self.settings.huggingface_api_key,
                base_url=self.settings.huggingface_base_url
            )
        
        # OpenRouter
        if self.settings.openrouter_api_key:
            providers[ProviderType.OPENROUTER] = ModelConfig(
                provider=ProviderType.OPENROUTER,
                model_name=self.settings.openrouter_model,
                api_key=self.settings.openrouter_api_key,
                base_url=self.settings.openrouter_base_url
            )
        
        # DeepSeek
        if self.settings.deepseek_api_key:
            providers[ProviderType.DEEPSEEK] = ModelConfig(
                provider=ProviderType.DEEPSEEK,
                model_name=self.settings.deepseek_model,
                api_key=self.settings.deepseek_api_key,
                base_url=self.settings.deepseek_base_url
            )
        
        # Together
        if self.settings.together_api_key:
            providers[ProviderType.TOGETHER] = ModelConfig(
                provider=ProviderType.TOGETHER,
                model_name=self.settings.together_model,
                api_key=self.settings.together_api_key,
                base_url=self.settings.together_base_url
            )
        
        # Groq
        if self.settings.groq_api_key:
            providers[ProviderType.GROQ] = ModelConfig(
                provider=ProviderType.GROQ,
                model_name=self.settings.groq_model,
                api_key=self.settings.groq_api_key,
                base_url=self.settings.groq_base_url
            )
        
        # Cohere
        if self.settings.cohere_api_key:
            providers[ProviderType.COHERE] = ModelConfig(
                provider=ProviderType.COHERE,
                model_name=self.settings.cohere_model,
                api_key=self.settings.cohere_api_key
            )
        
        # Mistral
        if self.settings.mistral_api_key:
            providers[ProviderType.MISTRAL] = ModelConfig(
                provider=ProviderType.MISTRAL,
                model_name=self.settings.mistral_model,
                api_key=self.settings.mistral_api_key,
                base_url=self.settings.mistral_base_url
            )
        
        return providers
    
    def get_provider_config(self, provider: ProviderType) -> Optional[ModelConfig]:
        """Get configuration for specific provider"""
        return self.providers.get(provider)
    
    def get_available_providers(self) -> List[ProviderType]:
        """Get list of configured providers"""
        return list(self.providers.keys())
    
    def get_primary_provider(self) -> ProviderType:
        """Get primary provider"""
        try:
            return ProviderType(self.settings.primary_provider)
        except ValueError:
            if self.providers:
                return next(iter(self.providers.keys()))
            raise ValueError("No providers configured")
    
    def get_fallback_providers(self) -> List[ProviderType]:
        """Get fallback providers"""
        if not self.settings.fallback_providers:
            return []
        
        fallbacks = []
        for provider_str in self.settings.fallback_providers.split(","):
            provider_str = provider_str.strip()
            try:
                provider = ProviderType(provider_str)
                if provider in self.providers:
                    fallbacks.append(provider)
            except ValueError:
                continue
        
        return fallbacks

# Global config instance
config = ConfigManager()
