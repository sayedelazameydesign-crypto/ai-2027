"""
Secure Credential Vault Integration

This module provides integration with secure credential storage systems
like HashiCorp Vault and AWS Secrets Manager for enhanced security.
"""

import os
import json
import asyncio
from typing import Dict, Optional, Any, Union
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging

try:
    import hvac  # HashiCorp Vault client
    VAULT_AVAILABLE = True
except ImportError:
    VAULT_AVAILABLE = False

try:
    import boto3
    from botocore.exceptions import ClientError
    AWS_AVAILABLE = True
except ImportError:
    AWS_AVAILABLE = False

logger = logging.getLogger(__name__)


@dataclass
class CredentialInfo:
    """Information about a stored credential."""
    key: str
    value: str
    created_at: datetime
    expires_at: Optional[datetime] = None
    metadata: Dict[str, Any] = None
    
    def is_expired(self) -> bool:
        """Check if credential is expired."""
        if self.expires_at is None:
            return False
        return datetime.now() > self.expires_at


class CredentialVault(ABC):
    """Abstract base class for credential vault implementations."""
    
    @abstractmethod
    async def get_credential(self, key: str) -> Optional[str]:
        """Get credential value by key."""
        pass
    
    @abstractmethod
    async def set_credential(self, key: str, value: str, ttl: Optional[int] = None) -> bool:
        """Set credential value with optional TTL."""
        pass
    
    @abstractmethod
    async def delete_credential(self, key: str) -> bool:
        """Delete credential by key."""
        pass
    
    @abstractmethod
    async def list_credentials(self) -> list[str]:
        """List all credential keys."""
        pass
    
    @abstractmethod
    async def health_check(self) -> bool:
        """Check vault health."""
        pass


class HashiCorpVault(CredentialVault):
    """HashiCorp Vault implementation."""
    
    def __init__(self, 
                 url: str,
                 token: Optional[str] = None,
                 mount_point: str = "secret",
                 namespace: Optional[str] = None):
        """
        Initialize HashiCorp Vault client.
        
        Args:
            url: Vault server URL
            token: Vault token (if None, uses VAULT_TOKEN env var)
            mount_point: Vault mount point
            namespace: Vault namespace
        """
        if not VAULT_AVAILABLE:
            raise ImportError("hvac library not available. Install with: pip install hvac")
        
        self.url = url
        self.mount_point = mount_point
        self.namespace = namespace
        
        # Initialize client
        self.client = hvac.Client(
            url=url,
            token=token or os.getenv('VAULT_TOKEN'),
            namespace=namespace
        )
        
        logger.info(f"HashiCorp Vault initialized: {url}")
    
    async def get_credential(self, key: str) -> Optional[str]:
        """Get credential from Vault."""
        try:
            # Use KV v2 API
            response = self.client.secrets.kv.v2.read_secret_version(
                path=key,
                mount_point=self.mount_point
            )
            
            if response and 'data' in response and 'data' in response['data']:
                return response['data']['data'].get('value')
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get credential {key} from Vault: {e}")
            return None
    
    async def set_credential(self, key: str, value: str, ttl: Optional[int] = None) -> bool:
        """Set credential in Vault."""
        try:
            secret_data = {'value': value}
            
            if ttl:
                secret_data['ttl'] = ttl
            
            self.client.secrets.kv.v2.create_or_update_secret(
                path=key,
                secret=secret_data,
                mount_point=self.mount_point
            )
            
            logger.info(f"Credential {key} stored in Vault")
            return True
            
        except Exception as e:
            logger.error(f"Failed to set credential {key} in Vault: {e}")
            return False
    
    async def delete_credential(self, key: str) -> bool:
        """Delete credential from Vault."""
        try:
            self.client.secrets.kv.v2.delete_metadata_and_all_versions(
                path=key,
                mount_point=self.mount_point
            )
            
            logger.info(f"Credential {key} deleted from Vault")
            return True
            
        except Exception as e:
            logger.error(f"Failed to delete credential {key} from Vault: {e}")
            return False
    
    async def list_credentials(self) -> list[str]:
        """List all credential keys."""
        try:
            response = self.client.secrets.kv.v2.list_secrets(
                path="",
                mount_point=self.mount_point
            )
            
            if response and 'data' in response and 'keys' in response['data']:
                return response['data']['keys']
            
            return []
            
        except Exception as e:
            logger.error(f"Failed to list credentials from Vault: {e}")
            return []
    
    async def health_check(self) -> bool:
        """Check Vault health."""
        try:
            return self.client.sys.is_initialized() and not self.client.sys.is_sealed()
        except Exception as e:
            logger.error(f"Vault health check failed: {e}")
            return False


class AWSSecretsManager(CredentialVault):
    """AWS Secrets Manager implementation."""
    
    def __init__(self, 
                 region_name: str = "us-east-1",
                 aws_access_key_id: Optional[str] = None,
                 aws_secret_access_key: Optional[str] = None):
        """
        Initialize AWS Secrets Manager client.
        
        Args:
            region_name: AWS region
            aws_access_key_id: AWS access key (if None, uses default credentials)
            aws_secret_access_key: AWS secret key
        """
        if not AWS_AVAILABLE:
            raise ImportError("boto3 library not available. Install with: pip install boto3")
        
        self.region_name = region_name
        
        # Initialize client
        session = boto3.Session(
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            region_name=region_name
        )
        
        self.client = session.client('secretsmanager')
        
        logger.info(f"AWS Secrets Manager initialized: {region_name}")
    
    async def get_credential(self, key: str) -> Optional[str]:
        """Get credential from AWS Secrets Manager."""
        try:
            response = self.client.get_secret_value(SecretId=key)
            
            if 'SecretString' in response:
                secret_data = json.loads(response['SecretString'])
                return secret_data.get('value', response['SecretString'])
            elif 'SecretBinary' in response:
                return response['SecretBinary'].decode('utf-8')
            
            return None
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'ResourceNotFoundException':
                logger.debug(f"Credential {key} not found in AWS Secrets Manager")
            else:
                logger.error(f"Failed to get credential {key} from AWS: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to get credential {key} from AWS: {e}")
            return None
    
    async def set_credential(self, key: str, value: str, ttl: Optional[int] = None) -> bool:
        """Set credential in AWS Secrets Manager."""
        try:
            secret_data = {'value': value}
            
            if ttl:
                secret_data['ttl'] = ttl
            
            try:
                # Try to update existing secret
                self.client.update_secret(
                    SecretId=key,
                    SecretString=json.dumps(secret_data)
                )
            except ClientError as e:
                if e.response['Error']['Code'] == 'ResourceNotFoundException':
                    # Create new secret
                    self.client.create_secret(
                        Name=key,
                        SecretString=json.dumps(secret_data),
                        Description=f"AI Provider credential: {key}"
                    )
                else:
                    raise
            
            logger.info(f"Credential {key} stored in AWS Secrets Manager")
            return True
            
        except Exception as e:
            logger.error(f"Failed to set credential {key} in AWS: {e}")
            return False
    
    async def delete_credential(self, key: str) -> bool:
        """Delete credential from AWS Secrets Manager."""
        try:
            self.client.delete_secret(
                SecretId=key,
                ForceDeleteWithoutRecovery=True
            )
            
            logger.info(f"Credential {key} deleted from AWS Secrets Manager")
            return True
            
        except Exception as e:
            logger.error(f"Failed to delete credential {key} from AWS: {e}")
            return False
    
    async def list_credentials(self) -> list[str]:
        """List all credential keys."""
        try:
            paginator = self.client.get_paginator('list_secrets')
            secret_names = []
            
            for page in paginator.paginate():
                for secret in page['SecretList']:
                    secret_names.append(secret['Name'])
            
            return secret_names
            
        except Exception as e:
            logger.error(f"Failed to list credentials from AWS: {e}")
            return []
    
    async def health_check(self) -> bool:
        """Check AWS Secrets Manager health."""
        try:
            # Try to list secrets (limited to 1)
            self.client.list_secrets(MaxResults=1)
            return True
        except Exception as e:
            logger.error(f"AWS Secrets Manager health check failed: {e}")
            return False


class LocalVault(CredentialVault):
    """Local file-based vault for development/testing."""
    
    def __init__(self, vault_file: str = ".vault.json"):
        """
        Initialize local vault.
        
        Args:
            vault_file: Path to vault file
        """
        self.vault_file = vault_file
        self.credentials: Dict[str, CredentialInfo] = {}
        self._load_vault()
        
        logger.info(f"Local vault initialized: {vault_file}")
    
    def _load_vault(self):
        """Load vault from file."""
        try:
            if os.path.exists(self.vault_file):
                with open(self.vault_file, 'r') as f:
                    data = json.load(f)
                
                for key, cred_data in data.items():
                    self.credentials[key] = CredentialInfo(
                        key=key,
                        value=cred_data['value'],
                        created_at=datetime.fromisoformat(cred_data['created_at']),
                        expires_at=datetime.fromisoformat(cred_data['expires_at']) if cred_data.get('expires_at') else None,
                        metadata=cred_data.get('metadata', {})
                    )
        except Exception as e:
            logger.error(f"Failed to load local vault: {e}")
    
    def _save_vault(self):
        """Save vault to file."""
        try:
            data = {}
            for key, cred_info in self.credentials.items():
                data[key] = {
                    'value': cred_info.value,
                    'created_at': cred_info.created_at.isoformat(),
                    'expires_at': cred_info.expires_at.isoformat() if cred_info.expires_at else None,
                    'metadata': cred_info.metadata or {}
                }
            
            with open(self.vault_file, 'w') as f:
                json.dump(data, f, indent=2)
                
        except Exception as e:
            logger.error(f"Failed to save local vault: {e}")
    
    async def get_credential(self, key: str) -> Optional[str]:
        """Get credential from local vault."""
        cred_info = self.credentials.get(key)
        
        if cred_info and not cred_info.is_expired():
            return cred_info.value
        elif cred_info and cred_info.is_expired():
            # Remove expired credential
            del self.credentials[key]
            self._save_vault()
        
        return None
    
    async def set_credential(self, key: str, value: str, ttl: Optional[int] = None) -> bool:
        """Set credential in local vault."""
        expires_at = None
        if ttl:
            expires_at = datetime.now() + timedelta(seconds=ttl)
        
        self.credentials[key] = CredentialInfo(
            key=key,
            value=value,
            created_at=datetime.now(),
            expires_at=expires_at
        )
        
        self._save_vault()
        return True
    
    async def delete_credential(self, key: str) -> bool:
        """Delete credential from local vault."""
        if key in self.credentials:
            del self.credentials[key]
            self._save_vault()
            return True
        return False
    
    async def list_credentials(self) -> list[str]:
        """List all credential keys."""
        # Remove expired credentials
        expired_keys = [
            key for key, cred in self.credentials.items()
            if cred.is_expired()
        ]
        
        for key in expired_keys:
            del self.credentials[key]
        
        if expired_keys:
            self._save_vault()
        
        return list(self.credentials.keys())
    
    async def health_check(self) -> bool:
        """Check local vault health."""
        try:
            # Try to write a test file
            test_file = f"{self.vault_file}.test"
            with open(test_file, 'w') as f:
                f.write('test')
            os.remove(test_file)
            return True
        except Exception:
            return False


class CredentialManager:
    """
    Unified credential manager with multiple vault backends.
    
    Provides a single interface for credential management with
    automatic fallback between different vault implementations.
    """
    
    def __init__(self):
        """Initialize credential manager."""
        self.vaults: list[CredentialVault] = []
        self.primary_vault: Optional[CredentialVault] = None
        self._setup_vaults()
        
        logger.info(f"Credential manager initialized with {len(self.vaults)} vaults")
    
    def _setup_vaults(self):
        """Setup available vaults based on configuration."""
        # Try HashiCorp Vault
        vault_url = os.getenv('VAULT_URL')
        vault_token = os.getenv('VAULT_TOKEN')
        
        if vault_url and vault_token and VAULT_AVAILABLE:
            try:
                vault = HashiCorpVault(
                    url=vault_url,
                    token=vault_token,
                    mount_point=os.getenv('VAULT_MOUNT_POINT', 'secret'),
                    namespace=os.getenv('VAULT_NAMESPACE')
                )
                self.vaults.append(vault)
                self.primary_vault = vault
                logger.info("HashiCorp Vault configured as primary")
            except Exception as e:
                logger.warning(f"Failed to setup HashiCorp Vault: {e}")
        
        # Try AWS Secrets Manager
        aws_region = os.getenv('AWS_REGION', 'us-east-1')
        
        if AWS_AVAILABLE and (os.getenv('AWS_ACCESS_KEY_ID') or os.getenv('AWS_PROFILE')):
            try:
                aws_vault = AWSSecretsManager(
                    region_name=aws_region,
                    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
                    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
                )
                self.vaults.append(aws_vault)
                
                if not self.primary_vault:
                    self.primary_vault = aws_vault
                    logger.info("AWS Secrets Manager configured as primary")
                    
            except Exception as e:
                logger.warning(f"Failed to setup AWS Secrets Manager: {e}")
        
        # Always add local vault as fallback
        local_vault = LocalVault(os.getenv('LOCAL_VAULT_FILE', '.vault.json'))
        self.vaults.append(local_vault)
        
        if not self.primary_vault:
            self.primary_vault = local_vault
            logger.info("Local vault configured as primary")
    
    async def get_credential(self, key: str) -> Optional[str]:
        """Get credential from available vaults."""
        for vault in self.vaults:
            try:
                value = await vault.get_credential(key)
                if value:
                    return value
            except Exception as e:
                logger.warning(f"Failed to get credential from vault: {e}")
                continue
        
        return None
    
    async def set_credential(self, key: str, value: str, ttl: Optional[int] = None) -> bool:
        """Set credential in primary vault."""
        if self.primary_vault:
            return await self.primary_vault.set_credential(key, value, ttl)
        return False
    
    async def delete_credential(self, key: str) -> bool:
        """Delete credential from all vaults."""
        success = False
        
        for vault in self.vaults:
            try:
                if await vault.delete_credential(key):
                    success = True
            except Exception as e:
                logger.warning(f"Failed to delete credential from vault: {e}")
        
        return success
    
    async def health_check_all(self) -> Dict[str, bool]:
        """Check health of all vaults."""
        health_status = {}
        
        for i, vault in enumerate(self.vaults):
            vault_name = f"{vault.__class__.__name__}_{i}"
            try:
                health_status[vault_name] = await vault.health_check()
            except Exception as e:
                logger.error(f"Health check failed for {vault_name}: {e}")
                health_status[vault_name] = False
        
        return health_status


# Global credential manager instance
credential_manager = CredentialManager()