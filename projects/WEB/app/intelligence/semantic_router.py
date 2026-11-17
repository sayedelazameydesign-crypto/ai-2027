"""
LLM-based Semantic Router

This module implements intelligent request routing based on semantic analysis
of user prompts to optimize provider selection for cost and performance.
"""

import asyncio
import json
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from enum import Enum
import logging
import re
from datetime import datetime, timedelta

from ..providers.base import CompletionRequest, ChatMessage
from ..core.fallback_manager import ProviderPriority


logger = logging.getLogger(__name__)


class IntentCategory(Enum):
    """Categories of user intent."""
    CODE_GENERATION = "code_generation"
    CODE_REVIEW = "code_review"
    CODE_DEBUGGING = "code_debugging"
    CODE_EXPLANATION = "code_explanation"
    GENERAL_CHAT = "general_chat"
    TECHNICAL_QUESTION = "technical_question"
    CREATIVE_WRITING = "creative_writing"
    DATA_ANALYSIS = "data_analysis"
    MATH_PROBLEM = "math_problem"
    TRANSLATION = "translation"
    SUMMARIZATION = "summarization"
    RESEARCH = "research"


class ComplexityLevel(Enum):
    """Complexity levels for requests."""
    SIMPLE = "simple"      # Quick responses, basic queries
    MODERATE = "moderate"  # Standard complexity
    COMPLEX = "complex"    # High complexity, detailed analysis
    EXPERT = "expert"      # Requires specialized knowledge


class LanguageType(Enum):
    """Programming language types."""
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    JAVA = "java"
    CSHARP = "csharp"
    CPP = "cpp"
    GO = "go"
    RUST = "rust"
    PHP = "php"
    RUBY = "ruby"
    SWIFT = "swift"
    KOTLIN = "kotlin"
    SQL = "sql"
    HTML_CSS = "html_css"
    SHELL = "shell"
    OTHER = "other"


@dataclass
class SemanticAnalysis:
    """Results of semantic analysis."""
    intent: IntentCategory
    complexity: ComplexityLevel
    language: Optional[LanguageType]
    confidence: float
    keywords: List[str]
    estimated_tokens: int
    requires_reasoning: bool
    requires_creativity: bool
    time_sensitive: bool
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'intent': self.intent.value,
            'complexity': self.complexity.value,
            'language': self.language.value if self.language else None,
            'confidence': self.confidence,
            'keywords': self.keywords,
            'estimated_tokens': self.estimated_tokens,
            'requires_reasoning': self.requires_reasoning,
            'requires_creativity': self.requires_creativity,
            'time_sensitive': self.time_sensitive
        }


@dataclass
class ProviderRecommendation:
    """Provider recommendation with reasoning."""
    provider_name: str
    confidence: float
    reasoning: str
    estimated_cost: float
    estimated_time: float
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'provider_name': self.provider_name,
            'confidence': self.confidence,
            'reasoning': self.reasoning,
            'estimated_cost': self.estimated_cost,
            'estimated_time': self.estimated_time
        }


class PatternMatcher:
    """Pattern-based intent and complexity detection."""
    
    # Intent detection patterns
    INTENT_PATTERNS = {
        IntentCategory.CODE_GENERATION: [
            r'\b(write|create|generate|build|make|implement)\s+.*\b(function|class|method|code|script|program|app|website)\b',
            r'\b(how to|show me how to)\s+.*\b(code|program|implement|build)\b',
            r'\bcan you (write|create|make|build)\b',
            r'\bneed.*\b(function|class|method|code)\b'
        ],
        IntentCategory.CODE_REVIEW: [
            r'\b(review|check|analyze|examine|look at)\s+.*\bcode\b',
            r'\bis this.*\b(correct|right|good|proper)\b',
            r'\bwhat.*wrong.*\bcode\b',
            r'\bimprove.*\bcode\b'
        ],
        IntentCategory.CODE_DEBUGGING: [
            r'\b(debug|fix|error|bug|issue|problem)\b',
            r'\bnot working\b',
            r'\bwhy.*\b(fail|error|crash|break)\b',
            r'\bexception\b.*\bhelp\b'
        ],
        IntentCategory.CODE_EXPLANATION: [
            r'\b(explain|understand|what does|how does)\b.*\bcode\b',
            r'\bwhat is.*\b(function|method|class)\b',
            r'\bhow.*work\b',
            r'\bcan you explain\b'
        ],
        IntentCategory.GENERAL_CHAT: [
            r'\b(hello|hi|hey|good morning|good afternoon)\b',
            r'\bhow are you\b',
            r'\bwhat.*weather\b',
            r'\btell me about\b'
        ],
        IntentCategory.TECHNICAL_QUESTION: [
            r'\bwhat is\b.*\b(algorithm|framework|library|technology)\b',
            r'\bhow.*\b(works|architecture|design pattern)\b',
            r'\bdifference between\b',
            r'\bbest practices?\b'
        ]
    }
    
    # Complexity indicators
    COMPLEXITY_PATTERNS = {
        ComplexityLevel.SIMPLE: [
            r'\bsimple\b', r'\bbasic\b', r'\bquick\b', r'\beasy\b',
            r'\bhello world\b', r'\bexample\b'
        ],
        ComplexityLevel.COMPLEX: [
            r'\bcomplex\b', r'\badvanced\b', r'\boptimize\b', r'\bperformance\b',
            r'\barchitecture\b', r'\bscalable\b', r'\benterprise\b',
            r'\bmachine learning\b', r'\bai\b', r'\balgorithm\b'
        ],
        ComplexityLevel.EXPERT: [
            r'\bexpert\b', r'\bprofessional\b', r'\bproduction\b',
            r'\bmicroservices\b', r'\bdistributed\b', r'\bconcurrency\b',
            r'\bsecurity\b', r'\bcryptography\b'
        ]
    }
    
    # Language detection patterns
    LANGUAGE_PATTERNS = {
        LanguageType.PYTHON: [
            r'\bpython\b', r'\bdjango\b', r'\bflask\b', r'\bpandas\b',
            r'\bnumpy\b', r'\.py\b', r'\bdef\s+\w+\(', r'\bimport\s+\w+'
        ],
        LanguageType.JAVASCRIPT: [
            r'\bjavascript\b', r'\bjs\b', r'\bnode\.?js\b', r'\breact\b',
            r'\bvue\b', r'\bangular\b', r'\.js\b', r'\bfunction\s*\('
        ],
        LanguageType.TYPESCRIPT: [
            r'\btypescript\b', r'\bts\b', r'\.ts\b', r'\binterface\s+\w+',
            r'\btype\s+\w+\s*='
        ],
        LanguageType.JAVA: [
            r'\bjava\b', r'\bspring\b', r'\.java\b', r'\bpublic class\b',
            r'\bpublic static void main\b'
        ],
        LanguageType.SQL: [
            r'\bsql\b', r'\bselect\b', r'\binsert\b', r'\bupdate\b',
            r'\bdelete\b', r'\bdatabase\b', r'\btable\b'
        ]
    }


class SemanticAnalyzer:
    """Semantic analysis engine for request classification."""
    
    def __init__(self):
        """Initialize semantic analyzer."""
        self.pattern_matcher = PatternMatcher()
        self.analysis_cache: Dict[str, SemanticAnalysis] = {}
        self.cache_ttl = timedelta(hours=1)
        
        logger.info("Semantic analyzer initialized")
    
    def analyze_request(self, request: CompletionRequest) -> SemanticAnalysis:
        """
        Analyze request to determine intent, complexity, and characteristics.
        
        Args:
            request: Completion request to analyze
            
        Returns:
            Semantic analysis results
        """
        # Combine all messages into text for analysis
        text = self._extract_text_from_request(request)
        
        # Check cache
        cache_key = self._generate_cache_key(text)
        if cache_key in self.analysis_cache:
            cached_analysis = self.analysis_cache[cache_key]
            # Check if cache is still valid
            if hasattr(cached_analysis, '_cached_at'):
                if datetime.now() - cached_analysis._cached_at < self.cache_ttl:
                    return cached_analysis
        
        # Perform analysis
        analysis = self._perform_analysis(text, request)
        
        # Cache result
        analysis._cached_at = datetime.now()
        self.analysis_cache[cache_key] = analysis
        
        return analysis
    
    def _extract_text_from_request(self, request: CompletionRequest) -> str:
        """Extract text content from request messages."""
        text_parts = []
        
        for message in request.messages:
            text_parts.append(f"{message.role}: {message.content}")
        
        return "\n".join(text_parts)
    
    def _perform_analysis(self, text: str, request: CompletionRequest) -> SemanticAnalysis:
        """Perform detailed semantic analysis."""
        text_lower = text.lower()
        
        # Detect intent
        intent = self._detect_intent(text_lower)
        
        # Detect complexity
        complexity = self._detect_complexity(text_lower, len(text))
        
        # Detect programming language
        language = self._detect_language(text_lower)
        
        # Extract keywords
        keywords = self._extract_keywords(text_lower)
        
        # Estimate token count
        estimated_tokens = self._estimate_tokens(text)
        
        # Analyze requirements
        requires_reasoning = self._requires_reasoning(text_lower, intent)
        requires_creativity = self._requires_creativity(text_lower, intent)
        time_sensitive = self._is_time_sensitive(text_lower)
        
        # Calculate confidence
        confidence = self._calculate_confidence(intent, complexity, language, text)
        
        return SemanticAnalysis(
            intent=intent,
            complexity=complexity,
            language=language,
            confidence=confidence,
            keywords=keywords,
            estimated_tokens=estimated_tokens,
            requires_reasoning=requires_reasoning,
            requires_creativity=requires_creativity,
            time_sensitive=time_sensitive
        )
    
    def _detect_intent(self, text: str) -> IntentCategory:
        """Detect user intent from text."""
        intent_scores = {}
        
        for intent, patterns in self.pattern_matcher.INTENT_PATTERNS.items():
            score = 0
            for pattern in patterns:
                matches = len(re.findall(pattern, text, re.IGNORECASE))
                score += matches
            intent_scores[intent] = score
        
        # Return intent with highest score, default to GENERAL_CHAT
        if intent_scores:
            best_intent = max(intent_scores, key=intent_scores.get)
            if intent_scores[best_intent] > 0:
                return best_intent
        
        # Fallback logic based on keywords
        if any(word in text for word in ['code', 'function', 'class', 'method', 'programming']):
            return IntentCategory.CODE_GENERATION
        elif any(word in text for word in ['explain', 'what is', 'how does']):
            return IntentCategory.CODE_EXPLANATION
        
        return IntentCategory.GENERAL_CHAT
    
    def _detect_complexity(self, text: str, text_length: int) -> ComplexityLevel:
        """Detect complexity level."""
        complexity_scores = {}
        
        for complexity, patterns in self.pattern_matcher.COMPLEXITY_PATTERNS.items():
            score = 0
            for pattern in patterns:
                matches = len(re.findall(pattern, text, re.IGNORECASE))
                score += matches
            complexity_scores[complexity] = score
        
        # Factor in text length
        if text_length > 1000:
            complexity_scores[ComplexityLevel.COMPLEX] = complexity_scores.get(ComplexityLevel.COMPLEX, 0) + 2
        elif text_length > 500:
            complexity_scores[ComplexityLevel.MODERATE] = complexity_scores.get(ComplexityLevel.MODERATE, 0) + 1
        
        # Return complexity with highest score
        if complexity_scores:
            best_complexity = max(complexity_scores, key=complexity_scores.get)
            if complexity_scores[best_complexity] > 0:
                return best_complexity
        
        return ComplexityLevel.MODERATE
    
    def _detect_language(self, text: str) -> Optional[LanguageType]:
        """Detect programming language."""
        language_scores = {}
        
        for language, patterns in self.pattern_matcher.LANGUAGE_PATTERNS.items():
            score = 0
            for pattern in patterns:
                matches = len(re.findall(pattern, text, re.IGNORECASE))
                score += matches
            if score > 0:
                language_scores[language] = score
        
        if language_scores:
            return max(language_scores, key=language_scores.get)
        
        return None
    
    def _extract_keywords(self, text: str) -> List[str]:
        """Extract important keywords."""
        # Simple keyword extraction
        technical_keywords = [
            'algorithm', 'function', 'class', 'method', 'variable',
            'database', 'api', 'framework', 'library', 'performance',
            'security', 'optimization', 'testing', 'debugging'
        ]
        
        found_keywords = []
        for keyword in technical_keywords:
            if keyword in text:
                found_keywords.append(keyword)
        
        return found_keywords[:10]  # Limit to top 10
    
    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count (rough approximation)."""
        # Rough estimation: 1 token ≈ 4 characters for English
        return len(text) // 4
    
    def _requires_reasoning(self, text: str, intent: IntentCategory) -> bool:
        """Check if request requires complex reasoning."""
        reasoning_indicators = [
            'analyze', 'compare', 'evaluate', 'optimize', 'design',
            'architecture', 'best practice', 'pros and cons', 'trade-off'
        ]
        
        has_reasoning_words = any(indicator in text for indicator in reasoning_indicators)
        
        # Certain intents inherently require reasoning
        reasoning_intents = {
            IntentCategory.CODE_REVIEW,
            IntentCategory.CODE_DEBUGGING,
            IntentCategory.TECHNICAL_QUESTION
        }
        
        return has_reasoning_words or intent in reasoning_intents
    
    def _requires_creativity(self, text: str, intent: IntentCategory) -> bool:
        """Check if request requires creativity."""
        creativity_indicators = [
            'creative', 'innovative', 'unique', 'original', 'brainstorm',
            'idea', 'design', 'story', 'write'
        ]
        
        has_creativity_words = any(indicator in text for indicator in creativity_indicators)
        
        creative_intents = {
            IntentCategory.CREATIVE_WRITING,
            IntentCategory.CODE_GENERATION
        }
        
        return has_creativity_words or intent in creative_intents
    
    def _is_time_sensitive(self, text: str) -> bool:
        """Check if request is time-sensitive."""
        urgency_indicators = [
            'urgent', 'asap', 'quickly', 'fast', 'immediate', 'now',
            'deadline', 'emergency', 'critical'
        ]
        
        return any(indicator in text for indicator in urgency_indicators)
    
    def _calculate_confidence(self, intent: IntentCategory, complexity: ComplexityLevel, 
                            language: Optional[LanguageType], text: str) -> float:
        """Calculate confidence score for analysis."""
        base_confidence = 0.7
        
        # Adjust based on clear indicators
        if language is not None:
            base_confidence += 0.1
        
        if len(text) > 100:  # More text usually means better analysis
            base_confidence += 0.1
        
        # Specific intent confidence adjustments
        if intent in [IntentCategory.CODE_GENERATION, IntentCategory.CODE_DEBUGGING]:
            if any(word in text.lower() for word in ['function', 'class', 'method', 'code']):
                base_confidence += 0.1
        
        return min(base_confidence, 1.0)
    
    def _generate_cache_key(self, text: str) -> str:
        """Generate cache key for text."""
        import hashlib
        return hashlib.md5(text.encode()).hexdigest()


class SemanticRouter:
    """
    Intelligent semantic router for provider selection.
    
    Uses semantic analysis to route requests to optimal providers
    based on intent, complexity, and performance characteristics.
    """
    
    def __init__(self):
        """Initialize semantic router."""
        self.analyzer = SemanticAnalyzer()
        self.provider_profiles = self._initialize_provider_profiles()
        
        logger.info("Semantic router initialized")
    
    def _initialize_provider_profiles(self) -> Dict[str, Dict[str, Any]]:
        """Initialize provider capability profiles."""
        return {
            'openai': {
                'strengths': [IntentCategory.CODE_GENERATION, IntentCategory.GENERAL_CHAT, IntentCategory.CREATIVE_WRITING],
                'complexity_preference': [ComplexityLevel.MODERATE, ComplexityLevel.COMPLEX],
                'cost_factor': 1.0,
                'speed_factor': 0.9,
                'reasoning_capability': 0.9,
                'creativity_capability': 0.95,
                'code_capability': 0.9
            },
            'anthropic': {
                'strengths': [IntentCategory.CODE_REVIEW, IntentCategory.TECHNICAL_QUESTION, IntentCategory.RESEARCH],
                'complexity_preference': [ComplexityLevel.COMPLEX, ComplexityLevel.EXPERT],
                'cost_factor': 1.2,
                'speed_factor': 0.8,
                'reasoning_capability': 0.95,
                'creativity_capability': 0.85,
                'code_capability': 0.95
            },
            'google': {
                'strengths': [IntentCategory.TECHNICAL_QUESTION, IntentCategory.DATA_ANALYSIS, IntentCategory.RESEARCH],
                'complexity_preference': [ComplexityLevel.MODERATE, ComplexityLevel.COMPLEX],
                'cost_factor': 0.8,
                'speed_factor': 1.1,
                'reasoning_capability': 0.85,
                'creativity_capability': 0.75,
                'code_capability': 0.8
            },
            'groq': {
                'strengths': [IntentCategory.GENERAL_CHAT, IntentCategory.TEXT_COMPLETION],
                'complexity_preference': [ComplexityLevel.SIMPLE, ComplexityLevel.MODERATE],
                'cost_factor': 0.3,
                'speed_factor': 1.5,
                'reasoning_capability': 0.7,
                'creativity_capability': 0.7,
                'code_capability': 0.75
            },
            'deepseek': {
                'strengths': [IntentCategory.CODE_GENERATION, IntentCategory.CODE_DEBUGGING],
                'complexity_preference': [ComplexityLevel.MODERATE, ComplexityLevel.COMPLEX],
                'cost_factor': 0.5,
                'speed_factor': 1.2,
                'reasoning_capability': 0.8,
                'creativity_capability': 0.7,
                'code_capability': 0.9
            }
        }
    
    async def route_request(self, request: CompletionRequest, 
                          available_providers: List[str],
                          cost_limit: Optional[float] = None) -> List[ProviderRecommendation]:
        """
        Route request to optimal providers based on semantic analysis.
        
        Args:
            request: Completion request to route
            available_providers: List of available provider names
            cost_limit: Optional cost limit per request
            
        Returns:
            List of provider recommendations sorted by suitability
        """
        # Analyze request
        analysis = self.analyzer.analyze_request(request)
        
        # Generate recommendations for each available provider
        recommendations = []
        
        for provider_name in available_providers:
            if provider_name in self.provider_profiles:
                recommendation = self._evaluate_provider(provider_name, analysis, cost_limit)
                if recommendation:
                    recommendations.append(recommendation)
        
        # Sort by confidence (descending)
        recommendations.sort(key=lambda x: x.confidence, reverse=True)
        
        logger.debug(f"Generated {len(recommendations)} provider recommendations for {analysis.intent.value}")
        
        return recommendations
    
    def _evaluate_provider(self, provider_name: str, analysis: SemanticAnalysis, 
                          cost_limit: Optional[float]) -> Optional[ProviderRecommendation]:
        """Evaluate provider suitability for request."""
        profile = self.provider_profiles[provider_name]
        
        # Calculate base score
        score = 0.0
        reasoning_parts = []
        
        # Intent matching
        if analysis.intent in profile['strengths']:
            score += 0.3
            reasoning_parts.append(f"Strong in {analysis.intent.value}")
        
        # Complexity matching
        if analysis.complexity in profile['complexity_preference']:
            score += 0.2
            reasoning_parts.append(f"Suitable for {analysis.complexity.value} tasks")
        
        # Capability matching
        if analysis.requires_reasoning:
            score += profile['reasoning_capability'] * 0.2
            reasoning_parts.append(f"Reasoning capability: {profile['reasoning_capability']:.1%}")
        
        if analysis.requires_creativity:
            score += profile['creativity_capability'] * 0.15
            reasoning_parts.append(f"Creativity capability: {profile['creativity_capability']:.1%}")
        
        if analysis.intent in [IntentCategory.CODE_GENERATION, IntentCategory.CODE_REVIEW, IntentCategory.CODE_DEBUGGING]:
            score += profile['code_capability'] * 0.15
            reasoning_parts.append(f"Code capability: {profile['code_capability']:.1%}")
        
        # Time sensitivity (favor faster providers)
        if analysis.time_sensitive:
            score += (profile['speed_factor'] - 1.0) * 0.1
            if profile['speed_factor'] > 1.0:
                reasoning_parts.append("Fast response time")
        
        # Estimate cost
        estimated_cost = analysis.estimated_tokens * 0.001 * profile['cost_factor']  # Rough estimation
        
        # Apply cost limit
        if cost_limit and estimated_cost > cost_limit:
            score *= 0.5  # Penalize expensive options
            reasoning_parts.append(f"Exceeds cost limit (${estimated_cost:.4f} > ${cost_limit:.4f})")
        
        # Estimate time
        estimated_time = analysis.estimated_tokens * 0.01 / profile['speed_factor']  # Rough estimation
        
        # Normalize score
        confidence = min(score, 1.0)
        
        # Only recommend if confidence is reasonable
        if confidence < 0.3:
            return None
        
        reasoning = "; ".join(reasoning_parts)
        
        return ProviderRecommendation(
            provider_name=provider_name,
            confidence=confidence,
            reasoning=reasoning,
            estimated_cost=estimated_cost,
            estimated_time=estimated_time
        )
    
    def get_analysis_stats(self) -> Dict[str, Any]:
        """Get analysis statistics."""
        return {
            'cache_size': len(self.analyzer.analysis_cache),
            'provider_profiles': len(self.provider_profiles),
            'supported_intents': [intent.value for intent in IntentCategory],
            'supported_complexities': [complexity.value for complexity in ComplexityLevel]
        }


# Global semantic router instance
semantic_router = SemanticRouter()