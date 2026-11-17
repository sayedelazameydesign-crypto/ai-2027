"""
🚀 نظام التكامل الموحد - Unified Integration System
المشروع الكامل كنظام واحد متكامل

يتضمن:
- المساعد الذكي (Smart Assistant)
- وسائل التواصل الاجتماعي (Social Media)
- GitHub Integration
- قاعدة المعرفة والتوصيات
- نظام التعلم
"""

import sys
import os
import json
import logging
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# إضافة مسار src للاستيراد
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# استيراد المكونات الأساسية
from smart_assistant import SmartAssistant, TaskType, AssistantResponse
from enhanced_assistant import EnhancedSmartAssistant
from social_media_integration import (
    SocialMediaIntegrationManager, 
    SocialMediaPost, 
    SocialPlatform,
    SocialMediaCredentials
)
from github_integration import GitHubManager, GitHubConfig, GitHubFile, GitHubIssue

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# 1. تكوين النظام الموحد (Unified System Config)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class UnifiedSystemConfig:
    """تكوين النظام الموحد"""
    
    # معلومات المشروع
    project_name: str = "Ultimate BitNet Smart System"
    project_version: str = "2.0.0"
    author: str = "GitHub Copilot"
    
    # المساعد الذكي
    enable_smart_assistant: bool = True
    enable_learning: bool = True
    
    # وسائل التواصل الاجتماعي
    enable_social_media: bool = True
    social_platforms: List[str] = field(default_factory=lambda: [
        'twitter', 'facebook', 'telegram'
    ])
    
    # GitHub
    enable_github: bool = True
    github_auto_sync: bool = True
    
    # قاعدة المعرفة
    enable_knowledge_base: bool = True
    enable_recommendations: bool = True
    
    # مسارات الملفات
    config_dir: str = "config"
    data_dir: str = "data"
    log_dir: str = "logs"
    
    # الإعدادات الإضافية
    metadata: Dict = field(default_factory=dict)
    
    def to_dict(self) -> Dict:
        """تحويل إلى قاموس"""
        return {
            'project_name': self.project_name,
            'project_version': self.project_version,
            'author': self.author,
            'enable_smart_assistant': self.enable_smart_assistant,
            'enable_learning': self.enable_learning,
            'enable_social_media': self.enable_social_media,
            'social_platforms': self.social_platforms,
            'enable_github': self.enable_github,
            'github_auto_sync': self.github_auto_sync,
            'enable_knowledge_base': self.enable_knowledge_base,
            'enable_recommendations': self.enable_recommendations
        }

# ═══════════════════════════════════════════════════════════════════════════════
# 2. نظام التكامل الموحد (Unified Integration System)
# ═══════════════════════════════════════════════════════════════════════════════

class UnifiedIntegrationSystem:
    """نظام تكامل موحد يربط جميع المكونات"""
    
    def __init__(self, config: UnifiedSystemConfig):
        """تهيئة النظام"""
        self.config = config
        self.initialized_at = datetime.now()
        
        # المكونات الأساسية
        self.smart_assistant: Optional[EnhancedSmartAssistant] = None
        self.social_manager: Optional[SocialMediaIntegrationManager] = None
        self.github_manager: Optional[GitHubManager] = None
        
        # سجل العمليات
        self.operation_log: List[Dict] = []
        self.performance_metrics: Dict = {}
        
        logger.info(f"🚀 جاري بدء النظام المتكامل: {config.project_name} v{config.project_version}")
    
    # ───────────────────────────────────────────────────────────────────────────
    # تهيئة المكونات
    # ───────────────────────────────────────────────────────────────────────────
    
    def initialize_smart_assistant(self, knowledge_base_path: Optional[str] = None):
        """تهيئة المساعد الذكي"""
        try:
            # Use default paths if model files don't exist
            model_path = "models/model.pt"
            vocab_path = "models/vocab.txt"
            
            self.smart_assistant = EnhancedSmartAssistant(
                model_path=model_path,
                vocab_path=vocab_path,
                kb_path=knowledge_base_path
            )
            
            if knowledge_base_path and Path(knowledge_base_path).exists():
                # Load knowledge base if method exists
                if hasattr(self.smart_assistant, 'load_knowledge_base'):
                    load_kb = getattr(self.smart_assistant, 'load_knowledge_base', None)
                    if load_kb and callable(load_kb):
                        load_kb(knowledge_base_path)
                elif hasattr(self.smart_assistant, 'knowledge_base'):
                    kb = getattr(self.smart_assistant, 'knowledge_base', None)
                    if kb and hasattr(kb, 'load_from_file'):
                        kb.load_from_file(knowledge_base_path)
            
            logger.info("✓ تم تهيئة المساعد الذكي بنجاح")
            self._log_operation("initialize_smart_assistant", True)
            return True
        
        except Exception as e:
            logger.error(f"✗ خطأ في تهيئة المساعد الذكي: {e}")
            self._log_operation("initialize_smart_assistant", False, str(e))
            return False
    
    def initialize_social_media(self, credentials_path: Optional[str] = None):
        """تهيئة واجهة وسائل التواصل الاجتماعي"""
        try:
            self.social_manager = SocialMediaIntegrationManager()
            
            if credentials_path and Path(credentials_path).exists():
                self.social_manager.load_credentials(credentials_path)
            
            logger.info("✓ تم تهيئة وسائل التواصل الاجتماعي")
            self._log_operation("initialize_social_media", True)
            return True
        
        except Exception as e:
            logger.error(f"✗ خطأ في تهيئة وسائل التواصل: {e}")
            self._log_operation("initialize_social_media", False, str(e))
            return False
    
    def initialize_github(self, config: GitHubConfig):
        """تهيئة GitHub"""
        try:
            self.github_manager = GitHubManager(config)
            
            logger.info("✓ تم تهيئة GitHub")
            self._log_operation("initialize_github", True)
            return True
        
        except Exception as e:
            logger.error(f"✗ خطأ في تهيئة GitHub: {e}")
            self._log_operation("initialize_github", False, str(e))
            return False
    
    # ───────────────────────────────────────────────────────────────────────────
    # المعالجة الموحدة
    # ───────────────────────────────────────────────────────────────────────────
    
    def process_and_share(self, 
                         query: str, 
                         share_to_platforms: Optional[List[SocialPlatform]] = None,
                         save_to_github: bool = False) -> Dict:
        """معالجة الاستعلام ومشاركته على المنصات"""
        
        results = {
            'query': query,
            'timestamp': datetime.now().isoformat(),
            'assistant_response': None,
            'social_shares': [],
            'github_status': None
        }
        
        try:
            # 1. معالجة بواسطة المساعد الذكي
            if self.smart_assistant:
                # Try different method names for compatibility
                if hasattr(self.smart_assistant, 'process_input'):
                    process_method = getattr(self.smart_assistant, 'process_input')
                    response = process_method(query)
                elif hasattr(self.smart_assistant, 'process'):
                    process_method = getattr(self.smart_assistant, 'process')
                    response = process_method(query)
                else:
                    response = {'prediction': 'Unable to process', 'confidence': 0, 'explanation': ''}
                
                if isinstance(response, dict):
                    results['assistant_response'] = {
                        'prediction': response.get('prediction'),
                        'confidence': response.get('confidence'),
                        'explanation': response.get('explanation')
                    }
                else:
                    results['assistant_response'] = {'prediction': str(response), 'confidence': 0, 'explanation': ''}
            
            # 2. المشاركة على وسائل التواصل
            if share_to_platforms and self.social_manager:
                # Ensure we have a valid prediction to share
                prediction = results.get('assistant_response', {}).get('prediction', 'No response')
                if prediction is None:
                    prediction = 'No response'
                
                post = SocialMediaPost(
                    content=str(prediction),
                    platform=share_to_platforms[0],
                    title=f"جواب AI - {query[:30]}",
                    hashtags=['AI', 'SmartAssistant', 'BitNet'],
                    metadata={'source': 'unified_system', 'query': query}
                )
                
                for platform in share_to_platforms:
                    share_result = self.social_manager.post_to_platform(post, platform)
                    results['social_shares'].append({
                        'platform': platform.value,
                        'success': share_result.success,
                        'post_id': share_result.post_id,
                        'url': share_result.url
                    })
            
            # 3. حفظ على GitHub
            if save_to_github and self.github_manager:
                github_result = self._save_to_github(query, results)
                results['github_status'] = github_result
            
            self._log_operation("process_and_share", True, json.dumps(results, ensure_ascii=False))
            logger.info(f"✓ تمت معالجة الاستعلام: {query}")
            
        except Exception as e:
            logger.error(f"✗ خطأ في المعالجة الموحدة: {e}")
            self._log_operation("process_and_share", False, str(e))
        
        return results
    
    def _save_to_github(self, query: str, results: Dict) -> Dict:
        """حفظ النتائج على GitHub"""
        try:
            if not self.github_manager:
                return {'success': False, 'error': 'GitHub manager not initialized'}
            
            filename = f"results/{datetime.now().strftime('%Y%m%d_%H%M%S')}_response.json"
            
            github_file = GitHubFile(
                path=filename,
                content=json.dumps(results, ensure_ascii=False, indent=2),
                message=f"AI Response: {query[:50]}",
                branch="main"
            )
            
            result = self.github_manager.create_file(github_file)
            
            return {
                'success': result['success'],
                'file_path': result.get('file_path'),
                'url': result.get('url')
            }
        
        except Exception as e:
            logger.error(f"خطأ في حفظ GitHub: {e}")
            return {'success': False, 'error': str(e)}
    
    # ───────────────────────────────────────────────────────────────────────────
    # التقارير والإحصائيات
    # ───────────────────────────────────────────────────────────────────────────
    
    def get_system_status(self) -> Dict:
        """الحصول على حالة النظام"""
        status = {
            'system_name': self.config.project_name,
            'version': self.config.project_version,
            'uptime': (datetime.now() - self.initialized_at).total_seconds(),
            'components': {
                'smart_assistant': self.smart_assistant is not None,
                'social_media': self.social_manager is not None,
                'github': self.github_manager is not None
            },
            'operations': len(self.operation_log),
            'last_operation': self.operation_log[-1] if self.operation_log else None
        }
        
        # إحصائيات وسائل التواصل
        if self.social_manager:
            status['social_media_stats'] = self.social_manager.get_analytics()
        
        return status
    
    def generate_report(self, output_path: str = "system_report.json"):
        """إنشاء تقرير شامل"""
        report = {
            'generated_at': datetime.now().isoformat(),
            'system_config': self.config.to_dict(),
            'system_status': self.get_system_status(),
            'operation_log': self.operation_log[-100:],  # آخر 100 عملية
            'performance_metrics': self.performance_metrics
        }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        
        logger.info(f"✓ تم إنشاء التقرير: {output_path}")
        return report
    
    # ───────────────────────────────────────────────────────────────────────────
    # الأدوات المساعدة
    # ───────────────────────────────────────────────────────────────────────────
    
    def _log_operation(self, operation: str, success: bool, details: Optional[str] = None):
        """تسجيل العملية"""
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'operation': operation,
            'success': success,
            'details': details
        }
        
        self.operation_log.append(log_entry)
    
    def save_config(self, filepath: str = "system_config.json"):
        """حفظ التكوين"""
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.config.to_dict(), f, ensure_ascii=False, indent=2)
        
        logger.info(f"✓ تم حفظ التكوين: {filepath}")
    
    def load_config(self, filepath: str = "system_config.json"):
        """تحميل التكوين"""
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        for key, value in data.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)
        
        logger.info(f"✓ تم تحميل التكوين: {filepath}")

# ═══════════════════════════════════════════════════════════════════════════════
# 3. محرك الربط الذكي (Smart Linking Engine)
# ═══════════════════════════════════════════════════════════════════════════════

class SmartLinkingEngine:
    """محرك ربط ذكي يتحكم في تبادل البيانات بين المكونات"""
    
    def __init__(self, system: UnifiedIntegrationSystem):
        self.system = system
        self.link_rules: List[Dict] = []
    
    def add_link_rule(self, 
                     source: str, 
                     target: str, 
                     condition: Optional[Callable] = None,
                     transformer: Optional[Callable] = None):
        """إضافة قاعدة ربط"""
        self.link_rules.append({
            'source': source,
            'target': target,
            'condition': condition,
            'transformer': transformer,
            'active': True
        })
        
        logger.info(f"✓ تم إضافة قاعدة ربط: {source} -> {target}")
    
    def enable_auto_sync(self):
        """تفعيل المزامنة التلقائية"""
        # المساعد -> السوشيال ميديا
        self.add_link_rule(
            source='smart_assistant',
            target='social_media',
            condition=lambda r: r.get('confidence', 0) > 0.85,
            transformer=lambda r: SocialMediaPost(
                content=r.get('prediction', ''),
                platform=SocialPlatform.TWITTER,
                hashtags=['AI', 'SmartResponse']
            )
        )
        
        # المساعد -> GitHub
        self.add_link_rule(
            source='smart_assistant',
            target='github',
            condition=lambda r: r.get('confidence', 0) > 0.9,
            transformer=lambda r: {
                'content': json.dumps(r, ensure_ascii=False, indent=2),
                'message': f"AI Analysis: {r.get('prediction', '')[:50]}"
            }
        )
        
        logger.info("✓ تم تفعيل المزامنة التلقائية")
