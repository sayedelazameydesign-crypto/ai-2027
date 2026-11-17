"""
PII Redaction System

This module implements advanced PII (Personally Identifiable Information) detection
and redaction to protect user privacy before sending requests to external providers.
"""

import re
import hashlib
from typing import Dict, List, Tuple, Optional, Set
from dataclasses import dataclass
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class PIIType(Enum):
    """Types of PII that can be detected."""
    EMAIL = "email"
    PHONE = "phone"
    SSN = "ssn"
    CREDIT_CARD = "credit_card"
    NAME = "name"
    ADDRESS = "address"
    IP_ADDRESS = "ip_address"
    URL = "url"
    API_KEY = "api_key"
    PASSWORD = "password"


@dataclass
class PIIMatch:
    """Represents a detected PII match."""
    pii_type: PIIType
    original_text: str
    start_pos: int
    end_pos: int
    confidence: float
    replacement_token: str


class PIIPatterns:
    """PII detection patterns and rules."""
    
    # Email patterns
    EMAIL_PATTERNS = [
        r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
        r'\b[A-Za-z0-9._%+-]+\s*@\s*[A-Za-z0-9.-]+\s*\.\s*[A-Z|a-z]{2,}\b'
    ]
    
    # Phone number patterns (various formats)
    PHONE_PATTERNS = [
        r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b',  # 123-456-7890, 123.456.7890, 1234567890
        r'\(\d{3}\)\s*\d{3}[-.]?\d{4}',    # (123) 456-7890
        r'\+\d{1,3}[-.\s]?\d{1,4}[-.\s]?\d{1,4}[-.\s]?\d{1,9}',  # International
        r'\b\d{10,15}\b'  # Generic long numbers
    ]
    
    # SSN patterns
    SSN_PATTERNS = [
        r'\b\d{3}-\d{2}-\d{4}\b',  # 123-45-6789
        r'\b\d{9}\b'  # 123456789 (context-dependent)
    ]
    
    # Credit card patterns
    CREDIT_CARD_PATTERNS = [
        r'\b4\d{3}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b',  # Visa
        r'\b5[1-5]\d{2}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b',  # MasterCard
        r'\b3[47]\d{2}[-\s]?\d{6}[-\s]?\d{5}\b',  # American Express
        r'\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b'  # Generic 16-digit
    ]
    
    # Name patterns (common patterns)
    NAME_PATTERNS = [
        r'\b[A-Z][a-z]+\s+[A-Z][a-z]+\b',  # First Last
        r'\b[A-Z][a-z]+\s+[A-Z]\.\s+[A-Z][a-z]+\b',  # First M. Last
        r'\bMr\.|Mrs\.|Ms\.|Dr\.\s+[A-Z][a-z]+',  # Title + Name
    ]
    
    # Address patterns
    ADDRESS_PATTERNS = [
        r'\b\d+\s+[A-Za-z\s]+(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Place|Pl)\b',
        r'\b\d+\s+[A-Za-z\s]+,\s*[A-Za-z\s]+,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?\b'  # Full address
    ]
    
    # IP Address patterns
    IP_PATTERNS = [
        r'\b(?:\d{1,3}\.){3}\d{1,3}\b',  # IPv4
        r'\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b'  # IPv6 (simplified)
    ]
    
    # URL patterns
    URL_PATTERNS = [
        r'https?://[^\s<>"{}|\\^`\[\]]+',
        r'www\.[^\s<>"{}|\\^`\[\]]+',
        r'ftp://[^\s<>"{}|\\^`\[\]]+'
    ]
    
    # API Key patterns
    API_KEY_PATTERNS = [
        r'sk-[a-zA-Z0-9]{48}',  # OpenAI
        r'sk-ant-[a-zA-Z0-9-]{95}',  # Anthropic
        r'AIza[0-9A-Za-z_-]{35}',  # Google
        r'Bearer\s+[a-zA-Z0-9_-]{20,}',  # Generic Bearer tokens
        r'api[_-]?key["\']?\s*[:=]\s*["\']?[a-zA-Z0-9_-]{16,}',  # Generic API keys
    ]
    
    # Password patterns
    PASSWORD_PATTERNS = [
        r'password["\']?\s*[:=]\s*["\']?[^\s"\']{8,}',
        r'passwd["\']?\s*[:=]\s*["\']?[^\s"\']{8,}',
        r'pwd["\']?\s*[:=]\s*["\']?[^\s"\']{8,}'
    ]


class PIIRedactor:
    """
    Advanced PII detection and redaction system.
    
    Detects various types of PII in text and replaces them with
    secure tokens while maintaining text structure and context.
    """
    
    def __init__(self, 
                 enabled_types: Optional[Set[PIIType]] = None,
                 preserve_format: bool = True,
                 use_deterministic_tokens: bool = True):
        """
        Initialize PII redactor.
        
        Args:
            enabled_types: Set of PII types to detect (all if None)
            preserve_format: Whether to preserve original text format
            use_deterministic_tokens: Use consistent tokens for same PII
        """
        self.enabled_types = enabled_types or set(PIIType)
        self.preserve_format = preserve_format
        self.use_deterministic_tokens = use_deterministic_tokens
        
        # Token mapping for deterministic redaction
        self.token_mapping: Dict[str, str] = {}
        self.reverse_mapping: Dict[str, str] = {}
        
        # Compile patterns for performance
        self.compiled_patterns = self._compile_patterns()
        
        logger.info(f"PII Redactor initialized with {len(self.enabled_types)} types")
    
    def _compile_patterns(self) -> Dict[PIIType, List[re.Pattern]]:
        """Compile regex patterns for better performance."""
        patterns = {}
        
        if PIIType.EMAIL in self.enabled_types:
            patterns[PIIType.EMAIL] = [re.compile(p, re.IGNORECASE) for p in PIIPatterns.EMAIL_PATTERNS]
        
        if PIIType.PHONE in self.enabled_types:
            patterns[PIIType.PHONE] = [re.compile(p) for p in PIIPatterns.PHONE_PATTERNS]
        
        if PIIType.SSN in self.enabled_types:
            patterns[PIIType.SSN] = [re.compile(p) for p in PIIPatterns.SSN_PATTERNS]
        
        if PIIType.CREDIT_CARD in self.enabled_types:
            patterns[PIIType.CREDIT_CARD] = [re.compile(p) for p in PIIPatterns.CREDIT_CARD_PATTERNS]
        
        if PIIType.NAME in self.enabled_types:
            patterns[PIIType.NAME] = [re.compile(p) for p in PIIPatterns.NAME_PATTERNS]
        
        if PIIType.ADDRESS in self.enabled_types:
            patterns[PIIType.ADDRESS] = [re.compile(p, re.IGNORECASE) for p in PIIPatterns.ADDRESS_PATTERNS]
        
        if PIIType.IP_ADDRESS in self.enabled_types:
            patterns[PIIType.IP_ADDRESS] = [re.compile(p) for p in PIIPatterns.IP_PATTERNS]
        
        if PIIType.URL in self.enabled_types:
            patterns[PIIType.URL] = [re.compile(p, re.IGNORECASE) for p in PIIPatterns.URL_PATTERNS]
        
        if PIIType.API_KEY in self.enabled_types:
            patterns[PIIType.API_KEY] = [re.compile(p, re.IGNORECASE) for p in PIIPatterns.API_KEY_PATTERNS]
        
        if PIIType.PASSWORD in self.enabled_types:
            patterns[PIIType.PASSWORD] = [re.compile(p, re.IGNORECASE) for p in PIIPatterns.PASSWORD_PATTERNS]
        
        return patterns
    
    def detect_pii(self, text: str) -> List[PIIMatch]:
        """
        Detect PII in text.
        
        Args:
            text: Text to analyze
            
        Returns:
            List of PII matches found
        """
        matches = []
        
        for pii_type, patterns in self.compiled_patterns.items():
            for pattern in patterns:
                for match in pattern.finditer(text):
                    confidence = self._calculate_confidence(pii_type, match.group())
                    
                    if confidence > 0.5:  # Confidence threshold
                        replacement_token = self._generate_token(pii_type, match.group())
                        
                        pii_match = PIIMatch(
                            pii_type=pii_type,
                            original_text=match.group(),
                            start_pos=match.start(),
                            end_pos=match.end(),
                            confidence=confidence,
                            replacement_token=replacement_token
                        )
                        matches.append(pii_match)
        
        # Sort by position (reverse order for replacement)
        matches.sort(key=lambda x: x.start_pos, reverse=True)
        
        # Remove overlapping matches (keep highest confidence)
        filtered_matches = self._remove_overlaps(matches)
        
        return filtered_matches
    
    def redact_text(self, text: str) -> Tuple[str, List[PIIMatch]]:
        """
        Redact PII from text.
        
        Args:
            text: Original text
            
        Returns:
            Tuple of (redacted_text, pii_matches)
        """
        matches = self.detect_pii(text)
        redacted_text = text
        
        # Replace PII with tokens (reverse order to maintain positions)
        for match in matches:
            redacted_text = (
                redacted_text[:match.start_pos] + 
                match.replacement_token + 
                redacted_text[match.end_pos:]
            )
        
        logger.debug(f"Redacted {len(matches)} PII instances from text")
        return redacted_text, matches
    
    def restore_text(self, redacted_text: str, matches: List[PIIMatch]) -> str:
        """
        Restore original text from redacted version.
        
        Args:
            redacted_text: Text with PII redacted
            matches: Original PII matches
            
        Returns:
            Restored original text
        """
        restored_text = redacted_text
        
        # Sort matches by position (forward order for restoration)
        sorted_matches = sorted(matches, key=lambda x: x.start_pos)
        
        for match in sorted_matches:
            if match.replacement_token in restored_text:
                restored_text = restored_text.replace(
                    match.replacement_token,
                    match.original_text,
                    1  # Replace only first occurrence
                )
        
        return restored_text
    
    def _calculate_confidence(self, pii_type: PIIType, text: str) -> float:
        """Calculate confidence score for PII detection."""
        # Basic confidence calculation
        base_confidence = 0.8
        
        # Adjust based on PII type and text characteristics
        if pii_type == PIIType.EMAIL:
            if '@' in text and '.' in text:
                return 0.95
        elif pii_type == PIIType.PHONE:
            # Check for phone number characteristics
            digits = sum(c.isdigit() for c in text)
            if digits >= 10:
                return 0.9
        elif pii_type == PIIType.API_KEY:
            if any(prefix in text.lower() for prefix in ['sk-', 'api', 'key', 'token']):
                return 0.95
        elif pii_type == PIIType.CREDIT_CARD:
            # Luhn algorithm check could be added here
            return 0.85
        
        return base_confidence
    
    def _generate_token(self, pii_type: PIIType, original_text: str) -> str:
        """Generate replacement token for PII."""
        if self.use_deterministic_tokens:
            # Use hash for consistent tokens
            if original_text in self.token_mapping:
                return self.token_mapping[original_text]
            
            # Generate new token
            hash_obj = hashlib.sha256(original_text.encode())
            hash_hex = hash_obj.hexdigest()[:8]
            
            if self.preserve_format:
                token = self._format_preserving_token(pii_type, original_text, hash_hex)
            else:
                token = f"[{pii_type.value.upper()}_{hash_hex}]"
            
            # Store mapping
            self.token_mapping[original_text] = token
            self.reverse_mapping[token] = original_text
            
            return token
        else:
            # Generate random token
            import uuid
            token_id = str(uuid.uuid4())[:8]
            
            if self.preserve_format:
                return self._format_preserving_token(pii_type, original_text, token_id)
            else:
                return f"[{pii_type.value.upper()}_{token_id}]"
    
    def _format_preserving_token(self, pii_type: PIIType, original_text: str, token_id: str) -> str:
        """Generate format-preserving replacement token."""
        if pii_type == PIIType.EMAIL:
            return f"user{token_id}@example.com"
        elif pii_type == PIIType.PHONE:
            if '-' in original_text:
                return f"555-{token_id[:3]}-{token_id[3:7]}"
            elif '(' in original_text:
                return f"(555) {token_id[:3]}-{token_id[3:7]}"
            else:
                return f"555{token_id[:7]}"
        elif pii_type == PIIType.NAME:
            return f"Person{token_id[:4]}"
        elif pii_type == PIIType.ADDRESS:
            return f"123 {token_id[:4]} Street"
        elif pii_type == PIIType.IP_ADDRESS:
            return f"192.168.1.{hash(token_id) % 255}"
        elif pii_type == PIIType.CREDIT_CARD:
            return f"****-****-****-{token_id[:4]}"
        else:
            return f"[{pii_type.value.upper()}_{token_id}]"
    
    def _remove_overlaps(self, matches: List[PIIMatch]) -> List[PIIMatch]:
        """Remove overlapping PII matches, keeping highest confidence."""
        if not matches:
            return matches
        
        # Sort by confidence (descending)
        sorted_matches = sorted(matches, key=lambda x: x.confidence, reverse=True)
        filtered_matches = []
        
        for match in sorted_matches:
            # Check if this match overlaps with any already selected match
            overlaps = False
            for selected in filtered_matches:
                if (match.start_pos < selected.end_pos and 
                    match.end_pos > selected.start_pos):
                    overlaps = True
                    break
            
            if not overlaps:
                filtered_matches.append(match)
        
        return filtered_matches
    
    def get_redaction_stats(self) -> Dict[str, int]:
        """Get statistics about redacted PII."""
        stats = {}
        for pii_type in PIIType:
            count = sum(1 for token in self.token_mapping.values() 
                       if pii_type.value.upper() in token)
            stats[pii_type.value] = count
        
        return stats
    
    def clear_mappings(self):
        """Clear token mappings."""
        self.token_mapping.clear()
        self.reverse_mapping.clear()
        logger.info("PII token mappings cleared")


# Global PII redactor instance
pii_redactor = PIIRedactor(
    enabled_types={
        PIIType.EMAIL, PIIType.PHONE, PIIType.API_KEY, 
        PIIType.CREDIT_CARD, PIIType.IP_ADDRESS, PIIType.URL
    },
    preserve_format=True,
    use_deterministic_tokens=True
)