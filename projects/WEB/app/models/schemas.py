"""Data Models and Schemas"""

from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any
from enum import Enum
from datetime import datetime

class CodeLanguage(str, Enum):
    """Supported programming languages"""
    HTML = "html"
    CSS = "css"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    PYTHON = "python"
    JAVA = "java"
    CPP = "cpp"
    GO = "go"
    RUST = "rust"
    PHP = "php"
    RUBY = "ruby"
    REACT = "react"
    VUE = "vue"
    ANGULAR = "angular"
    SVELTE = "svelte"

class SecurityLevel(str, Enum):
    """Security validation levels"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class TaskType(str, Enum):
    """Types of development tasks"""
    CODE_GENERATION = "code_generation"
    ERROR_FIXING = "error_fixing"
    CODE_REVIEW = "code_review"
    OPTIMIZATION = "optimization"
    REFACTORING = "refactoring"
    DOCUMENTATION = "documentation"
    TESTING = "testing"
    SECURITY_AUDIT = "security_audit"

class CodeRequest(BaseModel):
    """Request for code generation"""
    prompt: str = Field(..., min_length=10, max_length=10000, description="Task description")
    language: CodeLanguage = Field(..., description="Target programming language")
    security_level: SecurityLevel = Field(default=SecurityLevel.HIGH, description="Security requirements")
    optimize_performance: bool = Field(default=True, description="Enable performance optimization")
    include_preview: bool = Field(default=True, description="Generate preview if applicable")
    include_tests: bool = Field(default=False, description="Generate unit tests")
    include_docs: bool = Field(default=False, description="Generate documentation")
    framework: Optional[str] = Field(default=None, description="Specific framework to use")
    style_guide: Optional[str] = Field(default=None, description="Coding style guide")
    
    @validator('prompt')
    def validate_prompt(cls, v):
        """Validate prompt for security"""
        forbidden = ['eval(', 'exec(', '__import__', 'os.system', 'subprocess']
        if any(word in v.lower() for word in forbidden):
            raise ValueError("Prompt contains potentially dangerous operations")
        return v

class SecurityIssue(BaseModel):
    """Security issue details"""
    severity: str
    category: str
    description: str
    line_number: Optional[int] = None
    recommendation: str

class SecurityReport(BaseModel):
    """Security analysis report"""
    score: int = Field(ge=0, le=100)
    critical_issues: List[SecurityIssue] = []
    warnings: List[SecurityIssue] = []
    info: List[SecurityIssue] = []
    passed: bool

class PerformanceMetrics(BaseModel):
    """Performance analysis metrics"""
    size_kb: float
    lines: int
    complexity: int
    functions_count: int
    classes_count: Optional[int] = None
    estimated_load_time: Optional[float] = None
    optimization_suggestions: List[str] = []

class CodeResponse(BaseModel):
    """Response with generated code"""
    code: str
    language: CodeLanguage
    preview_url: Optional[str] = None
    security_report: SecurityReport
    performance_metrics: PerformanceMetrics
    suggestions: List[str] = []
    tests: Optional[str] = None
    documentation: Optional[str] = None
    provider_used: str
    generation_time: float
    tokens_used: Optional[int] = None
    estimated_cost: Optional[float] = None
    
class ErrorAnalysis(BaseModel):
    """Error analysis request"""
    code: str = Field(..., min_length=1, max_length=50000)
    error_message: str = Field(..., min_length=1, max_length=5000)
    language: CodeLanguage
    context: Optional[str] = None

class ErrorFix(BaseModel):
    """Error fix details"""
    original_code: str
    fixed_code: str
    changes_made: List[str]
    explanation: str

class ErrorResponse(BaseModel):
    """Error analysis response"""
    analysis: str
    root_cause: str
    fixes: List[ErrorFix]
    prevention_tips: List[str]
    related_issues: List[str] = []
    provider_used: str
    
class CodeReviewRequest(BaseModel):
    """Code review request"""
    code: str = Field(..., min_length=1, max_length=50000)
    language: CodeLanguage
    focus_areas: List[str] = Field(default_factory=lambda: ["security", "performance", "best_practices"])
    
class CodeReviewIssue(BaseModel):
    """Code review issue"""
    severity: str
    category: str
    line_number: Optional[int]
    description: str
    suggestion: str
    example: Optional[str] = None

class CodeReviewResponse(BaseModel):
    """Code review response"""
    overall_score: int = Field(ge=0, le=100)
    issues: List[CodeReviewIssue]
    strengths: List[str]
    recommendations: List[str]
    refactored_code: Optional[str] = None
    
class OptimizationRequest(BaseModel):
    """Code optimization request"""
    code: str = Field(..., min_length=1, max_length=50000)
    language: CodeLanguage
    optimization_goals: List[str] = Field(default_factory=lambda: ["performance", "readability"])
    
class OptimizationResponse(BaseModel):
    """Code optimization response"""
    original_metrics: PerformanceMetrics
    optimized_code: str
    optimized_metrics: PerformanceMetrics
    improvements: List[str]
    trade_offs: List[str]
    
class ProviderInfo(BaseModel):
    """AI Provider information"""
    name: str
    available: bool
    capabilities: List[str]
    cost_per_1k_tokens: Dict[str, float]
    context_window: int
    supports_streaming: bool
    
class SystemStatus(BaseModel):
    """System status"""
    status: str
    version: str
    uptime: float
    providers_available: int
    current_provider: str
    cache_enabled: bool
    requests_processed: int
    average_response_time: float

class ProviderSwitchRequest(BaseModel):
    """Provider switch request"""
    provider: str
    model: Optional[str] = None

class ProviderSwitchResponse(BaseModel):
    """Provider switch response"""
    success: bool
    message: str
    new_provider: str
    new_model: str
