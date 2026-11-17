"""
Configuration Manager for AI Providers System

This module handles loading, validation, and management of provider configurations
from environment variables and configuration files.
"""

import os
import json
from typing import Dict, List, Optional, Any
from pathlib import Path
from pydantic import BaseModel, Field, validator
from pydantic_settings import BaseSettings
from datetime import datetime
import logging

from ..providers.base import ProviderConfig, ConfigurationError


logger = logging.getLogger(__name__)


class ProviderSettings(BaseModel):
    """Settings for individual AI providers."""
    
    enabled: bool = True
    priority: int = Field(ge=1, le=100, default=50)
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
    
    @validator('priority')
    def validate_priority(cls, v):
        """Validate priority is within acceptable range."""
        if not 1 <= v <= 100:
            raise ValueError("Priority must be between 1 and 100")
        return v


class SystemConfig(BaseSettings):
    """System-wide configuration with fail-fast validation."""
    
    # Provider configurations
    providers: Dict[str, ProviderSettings] = Field(default_factory=dict)
    
    # System settings
    fallback_enabled: bool = True
    health_check_interval: int = Field(default=300, ge=60)
    max_concurrent_requests: int = Field(default=100, ge=1)
    request_timeout: int = Field(default=60, ge=1)
    logging_level: str = Field(default="INFO")
    fallback_timeout_ms: int = Field(default=500, ge=100, le=2000)
    
    # Security settings
    enable_security_scanning: bool = True
    max_content_length: int = Field(default=1000000, ge=1000)  # 1MB default
    
    class Config:
        env_file = ".env"
        case_sensitive = False
        env_nested_delimiter = "__"
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._validate_enabled_providers()
    
    def _validate_enabled_providers(self):
        """Fail-fast validation: ensure all enabled providers have valid API keys."""
        enabled_providers = [name for name, config in self.providers.items() if config.enabled]
        
        if not enabled_providers:
            logger.warning("No providers are enabled. System will not be functional.")
            return
        
        for provider_name in enabled_providers:
            provider_config = self.providers[provider_name]
            if not provider_config.api_key:
                raise ValueError(f"Provider '{provider_name}' is enabled but missing API key")
        
        logger.info(f"Validated {len(enabled_providers)} enabled providers")


class ConfigurationManager:
    """
    Manages configuration loading, validation, and hot-reloading for AI providers.
    """
    
    def __init__(self, config_file: Optional[str] = None):
        """
        Initialize configuration manager.
        
        Args:
            config_file: Optional path to configuration file
        """
        self.config_file = config_file
        self.config: Optional[SystemConfig] = None
        self._last_modified: Optional[datetime] = None
        self._load_config()
    
    def _load_config(self) -> None:
        """Load configuration from environment variables and files."""
        try:
            # Load base configuration from environment
            env_config = self._load_from_environment()
            
            # Load from file if specified
            file_config = {}
            if self.config_file and Path(self.config_file).exists():
                file_config = self._load_from_file()
            
            # Merge configurations (file overrides environment)
            merged_config = {**env_config, **file_config}
            
            # Create system config
            self.config = SystemConfig(**merged_config)
            self._last_modified = datetime.now()
            
            logger.info("Configuration loaded successfully")
            
        except Exception as e:
            logger.error(f"Failed to load configuration: {e}")
            raise ConfigurationError(f"Configuration loading failed: {e}", "system")
    
    def _load_from_environment(self) -> Dict[str, Any]:
        """Load configuration from environment variables."""
        config = {}
        
        # Load provider configurations from environment
        providers = {}
        
        # OpenAI
        if os.getenv('OPENAI_API_KEY'):
            providers['openai'] = ProviderSettings(
                enabled=os.getenv('OPENAI_ENABLED', 'true').lower() == 'true',
                priority=int(os.getenv('OPENAI_PRIORITY', '10')),
                api_key=os.getenv('OPENAI_API_KEY'),
                base_url=os.getenv('OPENAI_BASE_URL'),
                timeout=int(os.getenv('OPENAI_TIMEOUT', '30')),
                max_retries=int(os.getenv('OPENAI_MAX_RETRIES', '3')),
                models=os.getenv('OPENAI_MODELS', 'gpt-3.5-turbo,gpt-4').split(',')
            )
        
        # Anthropic
        if os.getenv('ANTHROPIC_API_KEY'):
            providers['anthropic'] = ProviderSettings(
                enabled=os.getenv('ANTHROPIC_ENABLED', 'true').lower() == 'true',
                priority=int(os.getenv('ANTHROPIC_PRIORITY', '20')),
                api_key=os.getenv('ANTHROPIC_API_KEY'),
                base_url=os.getenv('ANTHROPIC_BASE_URL'),
                timeout=int(os.getenv('ANTHROPIC_TIMEOUT', '30')),
                max_retries=int(os.getenv('ANTHROPIC_MAX_RETRIES', '3')),
                models=os.getenv('ANTHROPIC_MODELS', 'claude-3-sonnet-20240229,claude-3-haiku-20240307').split(',')
            )
        
        # Google Gemini
        if os.getenv('GOOGLE_API_KEY'):
            providers['google'] = ProviderSettings(
                enabled=os.getenv('GOOGLE_ENABLED', 'true').lower() == 'true',
                priority=int(os.getenv('GOOGLE_PRIORITY', '30')),
                api_key=os.getenv('GOOGLE_API_KEY'),
                base_url=os.getenv('GOOGLE_BASE_URL'),
                timeout=int(os.getenv('GOOGLE_TIMEOUT', '30')),
                max_retries=int(os.getenv('GOOGLE_MAX_RETRIES', '3')),
                models=os.getenv('GOOGLE_MODELS', 'gemini-pro,gemini-pro-vision').split(',')
            )
        
        # DeepSeek
        if os.getenv('DEEPSEEK_API_KEY'):
            providers['deepseek'] = ProviderSettings(
                enabled=os.getenv('DEEPSEEK_ENABLED', 'true').lower() == 'true',
                priority=int(os.getenv('DEEPSEEK_PRIORITY', '40')),
                api_key=os.getenv('DEEPSEEK_API_KEY'),
                base_url=os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com'),
                timeout=int(os.getenv('DEEPSEEK_TIMEOUT', '30')),
                max_retries=int(os.getenv('DEEPSEEK_MAX_RETRIES', '3')),
                models=os.getenv('DEEPSEEK_MODELS', 'deepseek-chat,deepseek-coder').split(',')
            )
        
        # Groq
        if os.getenv('GROQ_API_KEY'):
            providers['groq'] = ProviderSettings(
                enabled=os.getenv('GROQ_ENABLED', 'true').lower() == 'true',
                priority=int(os.getenv('GROQ_PRIORITY', '50')),
                api_key=os.getenv('GROQ_API_KEY'),
                base_url=os.getenv('GROQ_BASE_URL', 'https://api.groq.com/openai/v1'),
                timeout=int(os.getenv('GROQ_TIMEOUT', '30')),
                max_retries=int(os.getenv('GROQ_MAX_RETRIES', '3')),
                models=os.getenv('GROQ_MODELS', 'mixtral-8x7b-32768,llama2-70b-4096').split(',')
            )
        
        # HuggingFace
        if os.getenv('HUGGINGFACE_API_KEY'):
            providers['huggingface'] = ProviderSettings(
                enabled=os.getenv('HUGGINGFACE_ENABLED', 'true').lower() == 'true',
                priority=int(os.getenv('HUGGINGFACE_PRIORITY', '60')),
                api_key=os.getenv('HUGGINGFACE_API_KEY'),
                base_url=os.getenv('HUGGINGFACE_BASE_URL', 'https://api-inference.huggingface.co'),
                timeout=int(os.getenv('HUGGINGFACE_TIMEOUT', '30')),
                max_retries=int(os.getenv('HUGGINGFACE_MAX_RETRIES', '3')),
                models=os.getenv('HUGGINGFACE_MODELS', 'microsoft/DialoGPT-large').split(',')
            )
        
        # OpenRouter
        if os.getenv('OPENROUTER_API_KEY'):
            providers['openrouter'] = ProviderSettings(
                enabled=os.getenv('OPENROUTER_ENABLED', 'true').lower() == 'true',
                priority=int(os.getenv('OPENROUTER_PRIORITY', '70')),
                api_key=os.getenv('OPENROUTER_API_KEY'),
                base_url=os.getenv('OPENROUTER_BASE_URL', 'https://openrouter.ai/api/v1'),
                timeout=int(os.getenv('OPENROUTER_TIMEOUT', '30')),
                max_retries=int(os.getenv('OPENROUTER_MAX_RETRIES', '3')),
                models=os.getenv('OPENROUTER_MODELS', 'openai/gpt-3.5-turbo,anthropic/claude-2').split(',')
            )
        
        if providers:
            config['providers'] = providers
        
        # System settings
        config.update({
            'fallback_enabled': os.getenv('FALLBACK_ENABLED', 'true').lower() == 'true',
            'health_check_interval': int(os.getenv('HEALTH_CHECK_INTERVAL', '300')),
            'max_concurrent_requests': int(os.getenv('MAX_CONCURRENT_REQUESTS', '100')),
            'request_timeout': int(os.getenv('REQUEST_TIMEOUT', '60')),
            'logging_level': os.getenv('LOGGING_LEVEL', 'INFO'),
            'fallback_timeout_ms': int(os.getenv('FALLBACK_TIMEOUT_MS', '500')),
            'enable_security_scanning': os.getenv('ENABLE_SECURITY_SCANNING', 'true').lower() == 'true',
            'max_content_length': int(os.getenv('MAX_CONTENT_LENGTH', '1000000')),
        })
        
        return config
    
    def _load_from_file(self) -> Dict[str, Any]:
        """Load configuration from JSON file."""
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                file_config = json.load(f)
            
            # Convert provider configs to ProviderSettings objects
            if 'providers' in file_config:
                providers = {}
                for name, config in file_config['providers'].items():
                    providers[name] = ProviderSettings(**config)
                file_config['providers'] = providers
            
            logger.info(f"Configuration loaded from file: {self.config_file}")
            return file_config
            
        except Exception as e:
            logger.error(f"Failed to load configuration from file {self.config_file}: {e}")
            raise ConfigurationError(f"File loading failed: {e}", "system")
    
    def get_provider_config(self, provider_name: str) -> ProviderConfig:
        """
        Get configuration for a specific provider.
        
        Args:
            provider_name: Name of the provider
            
        Returns:
            Provider configuration
            
        Raises:
            ConfigurationError: If provider not found or not enabled
        """
        if not self.config:
            raise ConfigurationError("Configuration not loaded", "system")
        
        if provider_name not in self.config.providers:
            raise ConfigurationError(f"Provider '{provider_name}' not configured", provider_name)
        
        provider_settings = self.config.providers[provider_name]
        
        if not provider_settings.enabled:
            raise ConfigurationError(f"Provider '{provider_name}' is disabled", provider_name)
        
        # Convert ProviderSettings to ProviderConfig
        return ProviderConfig(
            api_key=provider_settings.api_key,
            base_url=provider_settings.base_url,
            timeout=provider_settings.timeout,
            max_retries=provider_settings.max_retries,
            retry_delay=provider_settings.retry_delay,
            models=provider_settings.models
        )
    
    def get_enabled_providers(self) -> List[str]:
        """
        Get list of enabled provider names sorted by priority.
        
        Returns:
            List of enabled provider names
        """
        if not self.config:
            return []
        
        enabled = [
            (name, settings.priority)
            for name, settings in self.config.providers.items()
            if settings.enabled
        ]
        
        # Sort by priority (lower number = higher priority)
        enabled.sort(key=lambda x: x[1])
        
        return [name for name, _ in enabled]
    
    def reload_config(self) -> bool:
        """
        Reload configuration from sources.
        
        Returns:
            True if configuration was reloaded, False if no changes detected
        """
        try:
            # Check if file was modified
            if self.config_file and Path(self.config_file).exists():
                file_modified = datetime.fromtimestamp(Path(self.config_file).stat().st_mtime)
                if self._last_modified and file_modified <= self._last_modified:
                    return False
            
            old_config = self.config
            self._load_config()
            
            # Check if configuration actually changed
            if old_config and self.config:
                if old_config.dict() == self.config.dict():
                    return False
            
            logger.info("Configuration reloaded successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to reload configuration: {e}")
            return False
    
    def validate_provider_config(self, provider_name: str, config_dict: Dict[str, Any]) -> bool:
        """
        Validate provider configuration without applying it.
        
        Args:
            provider_name: Name of the provider
            config_dict: Configuration dictionary to validate
            
        Returns:
            True if configuration is valid
            
        Raises:
            ConfigurationError: If configuration is invalid
        """
        try:
            ProviderSettings(**config_dict)
            return True
        except Exception as e:
            raise ConfigurationError(f"Invalid configuration for {provider_name}: {e}", provider_name)
    
    def get_system_config(self) -> SystemConfig:
        """
        Get the current system configuration.
        
        Returns:
            System configuration object
            
        Raises:
            ConfigurationError: If configuration not loaded
        """
        if not self.config:
            raise ConfigurationError("Configuration not loaded", "system")
        return self.config
    
    def update_provider_config(self, provider_name: str, config_dict: Dict[str, Any]) -> None:
        """
        Update configuration for a specific provider.
        
        Args:
            provider_name: Name of the provider
            config_dict: New configuration dictionary
            
        Raises:
            ConfigurationError: If configuration is invalid
        """
        if not self.config:
            raise ConfigurationError("Configuration not loaded", "system")
        
        # Validate new configuration
        new_settings = ProviderSettings(**config_dict)
        
        # Update configuration
        self.config.providers[provider_name] = new_settings
        
        logger.info(f"Updated configuration for provider: {provider_name}")