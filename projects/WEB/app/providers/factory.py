"""
Provider Factory for AI Providers System

© 2026 SAYED_ELAZAMEY - All Rights Reserved
PROPRIETARY SOFTWARE - UNAUTHORIZED USE PROHIBITED

This module implements the Factory pattern to create and manage AI provider instances
with caching, registration, and validation capabilities.

This software is protected by copyright law. Unauthorized reproduction,
distribution, or modification is strictly prohibited.
"""

from typing import Dict, Type, Optional, List
from enum import Enum
import logging
from datetime import datetime

from .base import BaseProvider, ProviderConfig, ConfigurationError


logger = logging.getLogger(__name__)


class ProviderType(Enum):
    """Enumeration of supported AI provider types."""
    
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GOOGLE = "google"
    DEEPSEEK = "deepseek"
    GROQ = "groq"
    HUGGINGFACE = "huggingface"
    OPENROUTER = "openrouter"


class ProviderFactory:
    """
    Factory class for creating and managing AI provider instances.
    
    Implements the Factory pattern with caching, registration, and validation
    to ensure consistent provider creation and management.
    """
    
    # Class-level registry of provider types to classes
    _providers: Dict[ProviderType, Type[BaseProvider]] = {}
    
    # Instance cache for created providers
    _instances: Dict[str, BaseProvider] = {}
    
    # Registration timestamp tracking
    _registration_times: Dict[ProviderType, datetime] = {}
    
    @classmethod
    def register_provider(cls, provider_type: ProviderType, provider_class: Type[BaseProvider]) -> None:
        """
        Register a new provider type with the factory.
        
        Args:
            provider_type: The provider type enum
            provider_class: The provider class to register
            
        Raises:
            ValueError: If provider_class doesn't inherit from BaseProvider
        """
        # Validate that the class inherits from BaseProvider
        if not issubclass(provider_class, BaseProvider):
            raise ValueError(f"Provider class {provider_class.__name__} must inherit from BaseProvider")
        
        # Check if provider implements all required abstract methods
        abstract_methods = BaseProvider.__abstractmethods__
        for method in abstract_methods:
            if not hasattr(provider_class, method):
                raise ValueError(f"Provider class {provider_class.__name__} must implement {method}")
        
        cls._providers[provider_type] = provider_class
        cls._registration_times[provider_type] = datetime.now()
        
        logger.info(f"Registered provider: {provider_type.value} -> {provider_class.__name__}")
    
    @classmethod
    def unregister_provider(cls, provider_type: ProviderType) -> None:
        """
        Unregister a provider type from the factory.
        
        Args:
            provider_type: The provider type to unregister
        """
        if provider_type in cls._providers:
            del cls._providers[provider_type]
            if provider_type in cls._registration_times:
                del cls._registration_times[provider_type]
            
            # Clear cached instances for this provider type
            cls._clear_provider_cache(provider_type.value)
            
            logger.info(f"Unregistered provider: {provider_type.value}")
    
    @classmethod
    def create_provider(cls, provider_type: ProviderType, config: ProviderConfig) -> BaseProvider:
        """
        Create a provider instance with caching.
        
        Args:
            provider_type: The type of provider to create
            config: Configuration for the provider
            
        Returns:
            Provider instance
            
        Raises:
            ValueError: If provider type is not registered
            ConfigurationError: If configuration is invalid
        """
        # Check if provider type is registered
        if provider_type not in cls._providers:
            available_types = [pt.value for pt in cls._providers.keys()]
            raise ValueError(
                f"Unknown provider type: {provider_type.value}. "
                f"Available types: {available_types}"
            )
        
        # Generate cache key based on provider type and config hash
        cache_key = cls._generate_cache_key(provider_type, config)
        
        # Return cached instance if available
        if cache_key in cls._instances:
            logger.debug(f"Returning cached instance for {provider_type.value}")
            return cls._instances[cache_key]
        
        # Create new instance
        try:
            provider_class = cls._providers[provider_type]
            instance = provider_class(config)
            
            # Validate the instance
            if not instance.validate_config():
                raise ConfigurationError(
                    f"Invalid configuration for {provider_type.value}",
                    provider_type.value
                )
            
            # Cache the instance
            cls._instances[cache_key] = instance
            
            logger.info(f"Created new provider instance: {provider_type.value}")
            return instance
            
        except Exception as e:
            logger.error(f"Failed to create provider {provider_type.value}: {e}")
            if isinstance(e, (ConfigurationError, ValueError)):
                raise
            raise ConfigurationError(
                f"Provider creation failed: {e}",
                provider_type.value
            )
    
    @classmethod
    def get_provider_by_name(cls, provider_name: str, config: ProviderConfig) -> BaseProvider:
        """
        Create a provider instance by name string.
        
        Args:
            provider_name: Name of the provider (e.g., "openai", "anthropic")
            config: Configuration for the provider
            
        Returns:
            Provider instance
            
        Raises:
            ValueError: If provider name is not recognized
        """
        try:
            provider_type = ProviderType(provider_name.lower())
            return cls.create_provider(provider_type, config)
        except ValueError:
            available_names = [pt.value for pt in cls._providers.keys()]
            raise ValueError(
                f"Unknown provider name: {provider_name}. "
                f"Available providers: {available_names}"
            )
    
    @classmethod
    def get_available_providers(cls) -> List[ProviderType]:
        """
        Get list of registered provider types.
        
        Returns:
            List of available provider types
        """
        return list(cls._providers.keys())
    
    @classmethod
    def get_available_provider_names(cls) -> List[str]:
        """
        Get list of registered provider names as strings.
        
        Returns:
            List of available provider names
        """
        return [pt.value for pt in cls._providers.keys()]
    
    @classmethod
    def is_provider_registered(cls, provider_type: ProviderType) -> bool:
        """
        Check if a provider type is registered.
        
        Args:
            provider_type: The provider type to check
            
        Returns:
            True if provider is registered, False otherwise
        """
        return provider_type in cls._providers
    
    @classmethod
    def get_provider_class(cls, provider_type: ProviderType) -> Optional[Type[BaseProvider]]:
        """
        Get the provider class for a given type.
        
        Args:
            provider_type: The provider type
            
        Returns:
            Provider class or None if not registered
        """
        return cls._providers.get(provider_type)
    
    @classmethod
    def clear_cache(cls) -> None:
        """Clear all cached provider instances."""
        cls._instances.clear()
        logger.info("Cleared all cached provider instances")
    
    @classmethod
    def _clear_provider_cache(cls, provider_name: str) -> None:
        """
        Clear cached instances for a specific provider.
        
        Args:
            provider_name: Name of the provider to clear from cache
        """
        keys_to_remove = [
            key for key in cls._instances.keys()
            if key.startswith(f"{provider_name}_")
        ]
        
        for key in keys_to_remove:
            del cls._instances[key]
        
        if keys_to_remove:
            logger.info(f"Cleared {len(keys_to_remove)} cached instances for {provider_name}")
    
    @classmethod
    def _generate_cache_key(cls, provider_type: ProviderType, config: ProviderConfig) -> str:
        """
        Generate a cache key for a provider instance.
        
        Args:
            provider_type: The provider type
            config: The provider configuration
            
        Returns:
            Cache key string
        """
        # Use first 8 characters of API key hash for cache key
        # This allows different API keys to have separate instances
        api_key_hash = hash(config.api_key) % 100000000  # Keep it positive and manageable
        base_url_hash = hash(config.base_url or "") % 10000
        
        return f"{provider_type.value}_{api_key_hash}_{base_url_hash}"
    
    @classmethod
    def get_cache_stats(cls) -> Dict[str, int]:
        """
        Get statistics about cached instances.
        
        Returns:
            Dictionary with cache statistics
        """
        stats = {
            'total_cached_instances': len(cls._instances),
            'registered_providers': len(cls._providers),
        }
        
        # Count instances per provider type
        for provider_type in cls._providers.keys():
            provider_name = provider_type.value
            count = sum(1 for key in cls._instances.keys() if key.startswith(f"{provider_name}_"))
            stats[f'{provider_name}_instances'] = count
        
        return stats
    
    @classmethod
    def validate_provider_implementation(cls, provider_class: Type[BaseProvider]) -> List[str]:
        """
        Validate that a provider class properly implements the BaseProvider interface.
        
        Args:
            provider_class: The provider class to validate
            
        Returns:
            List of validation errors (empty if valid)
        """
        errors = []
        
        # Check inheritance
        if not issubclass(provider_class, BaseProvider):
            errors.append(f"{provider_class.__name__} must inherit from BaseProvider")
            return errors  # No point checking further if not a subclass
        
        # Check abstract methods implementation
        abstract_methods = BaseProvider.__abstractmethods__
        for method in abstract_methods:
            if not hasattr(provider_class, method):
                errors.append(f"Missing implementation of abstract method: {method}")
            elif getattr(provider_class, method) is getattr(BaseProvider, method):
                errors.append(f"Abstract method {method} not properly overridden")
        
        # Check method signatures (basic validation)
        required_methods = {
            'generate_completion': ['request'],
            'stream_completion': ['request'],
            'health_check': [],
            'validate_config': [],
        }
        
        for method_name, expected_params in required_methods.items():
            if hasattr(provider_class, method_name):
                method = getattr(provider_class, method_name)
                if callable(method):
                    # Basic signature validation could be added here
                    pass
        
        # Check properties
        if not hasattr(provider_class, 'supported_models'):
            errors.append("Missing property: supported_models")
        
        return errors
    
    @classmethod
    def auto_register_providers(cls) -> None:
        """
        Automatically register all available provider classes.
        
        This method attempts to import and register all known provider implementations.
        """
        provider_modules = {
            ProviderType.OPENAI: 'openai_provider',
            ProviderType.ANTHROPIC: 'anthropic_provider',
            ProviderType.GOOGLE: 'google_provider',
            ProviderType.DEEPSEEK: 'deepseek_provider',
            ProviderType.GROQ: 'groq_provider',
            ProviderType.HUGGINGFACE: 'huggingface_provider',
            ProviderType.OPENROUTER: 'openrouter_provider',
        }
        
        registered_count = 0
        
        for provider_type, module_name in provider_modules.items():
            try:
                # Dynamic import of provider module
                module = __import__(f'app.providers.{module_name}', fromlist=[module_name])
                
                # Get provider class (assumes class name follows pattern: XxxProvider)
                class_name = f"{provider_type.value.title()}Provider"
                if hasattr(module, class_name):
                    provider_class = getattr(module, class_name)
                    cls.register_provider(provider_type, provider_class)
                    registered_count += 1
                else:
                    logger.warning(f"Provider class {class_name} not found in {module_name}")
                    
            except ImportError as e:
                logger.debug(f"Could not import {module_name}: {e}")
            except Exception as e:
                logger.error(f"Error registering {provider_type.value}: {e}")
        
        logger.info(f"Auto-registered {registered_count} providers")
    
    def __init__(self):
        """Initialize factory instance (not typically needed as factory is class-based)."""
        pass
    
    def __str__(self) -> str:
        """String representation of the factory."""
        return f"ProviderFactory(registered={len(self._providers)}, cached={len(self._instances)})"
    
    def __repr__(self) -> str:
        """Detailed string representation of the factory."""
        return (
            f"ProviderFactory("
            f"registered_providers={list(self._providers.keys())}, "
            f"cached_instances={len(self._instances)}"
            f")"
        )