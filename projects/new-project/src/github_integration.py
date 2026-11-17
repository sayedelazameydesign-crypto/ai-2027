"""
🔗 نظام تكامل GitHub
GitHub Integration System

تفاعل مع مستودعات GitHub وإدارة المشاريع
"""

import requests
import json
import logging
import base64
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# 1. معالج GitHub (GitHub Handler)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class GitHubConfig:
    """تكوين GitHub"""
    token: str
    username: str
    email: str
    base_url: str = "https://api.github.com"
    repo_owner: Optional[str] = None
    repo_name: Optional[str] = None

@dataclass
class GitHubFile:
    """ملف على GitHub"""
    path: str
    content: str
    message: str
    branch: str = "main"
    metadata: Dict = field(default_factory=dict)

@dataclass
class GitHubIssue:
    """مشكلة (Issue) على GitHub"""
    title: str
    body: str
    labels: List[str] = field(default_factory=list)
    assignees: List[str] = field(default_factory=list)
    milestone: Optional[str] = None

@dataclass
class GitHubPullRequest:
    """طلب دمج (Pull Request) على GitHub"""
    title: str
    body: str
    head: str
    base: str = "main"
    draft: bool = False
    labels: List[str] = field(default_factory=list)

class GitHubManager:
    """مدير GitHub المتقدم"""
    
    def __init__(self, config: GitHubConfig):
        self.config = config
        self.headers = self._build_headers()
        self.api_base = config.base_url
    
    def _build_headers(self) -> Dict:
        """بناء رؤوس الطلب"""
        return {
            "Authorization": f"token {self.config.token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Media-Type": "github.v3"
        }
    
    # ───────────────────────────────────────────────────────────────────────────
    # إدارة الملفات
    # ───────────────────────────────────────────────────────────────────────────
    
    def create_file(self, github_file: GitHubFile) -> Dict:
        """إنشاء ملف على GitHub"""
        try:
            owner = self.config.repo_owner or self.config.username
            repo = self.config.repo_name
            
            # تشفير المحتوى بـ Base64
            encoded_content = base64.b64encode(github_file.content.encode()).decode()
            
            payload = {
                "message": github_file.message,
                "content": encoded_content,
                "branch": github_file.branch,
                "committer": {
                    "name": self.config.username,
                    "email": self.config.email
                }
            }
            
            response = requests.put(
                f"{self.api_base}/repos/{owner}/{repo}/contents/{github_file.path}",
                json=payload,
                headers=self.headers,
                timeout=10
            )
            
            if response.status_code in [201, 200]:
                data = response.json()
                return {
                    'success': True,
                    'message': 'تم إنشاء الملف بنجاح',
                    'file_path': data['content']['path'],
                    'sha': data['content']['sha'],
                    'url': data['content']['html_url']
                }
            else:
                return {
                    'success': False,
                    'message': f'خطأ: {response.text}'
                }
        
        except Exception as e:
            logger.error(f"خطأ في إنشاء الملف: {e}")
            return {'success': False, 'message': str(e)}
    
    def update_file(self, github_file: GitHubFile, sha: str) -> Dict:
        """تحديث ملف على GitHub"""
        try:
            owner = self.config.repo_owner or self.config.username
            repo = self.config.repo_name
            
            encoded_content = base64.b64encode(github_file.content.encode()).decode()
            
            payload = {
                "message": github_file.message,
                "content": encoded_content,
                "sha": sha,
                "branch": github_file.branch,
                "committer": {
                    "name": self.config.username,
                    "email": self.config.email
                }
            }
            
            response = requests.put(
                f"{self.api_base}/repos/{owner}/{repo}/contents/{github_file.path}",
                json=payload,
                headers=self.headers,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                return {
                    'success': True,
                    'message': 'تم تحديث الملف بنجاح',
                    'sha': data['content']['sha'],
                    'url': data['content']['html_url']
                }
            else:
                return {
                    'success': False,
                    'message': f'خطأ: {response.text}'
                }
        
        except Exception as e:
            logger.error(f"خطأ في تحديث الملف: {e}")
            return {'success': False, 'message': str(e)}
    
    def get_file(self, path: str, branch: str = "main") -> Dict:
        """الحصول على محتوى ملف"""
        try:
            owner = self.config.repo_owner or self.config.username
            repo = self.config.repo_name
            
            response = requests.get(
                f"{self.api_base}/repos/{owner}/{repo}/contents/{path}",
                params={'ref': branch},
                headers=self.headers,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                content = base64.b64decode(data['content']).decode('utf-8')
                return {
                    'success': True,
                    'content': content,
                    'sha': data['sha'],
                    'url': data['html_url']
                }
            else:
                return {'success': False, 'message': f'الملف غير موجود'}
        
        except Exception as e:
            logger.error(f"خطأ في الحصول على الملف: {e}")
            return {'success': False, 'message': str(e)}
    
    # ───────────────────────────────────────────────────────────────────────────
    # إدارة المشاكل (Issues)
    # ───────────────────────────────────────────────────────────────────────────
    
    def create_issue(self, issue: GitHubIssue) -> Dict:
        """إنشاء مشكلة (Issue)"""
        try:
            owner = self.config.repo_owner or self.config.username
            repo = self.config.repo_name
            
            payload = {
                "title": issue.title,
                "body": issue.body,
                "labels": issue.labels,
                "assignees": issue.assignees
            }
            
            response = requests.post(
                f"{self.api_base}/repos/{owner}/{repo}/issues",
                json=payload,
                headers=self.headers,
                timeout=10
            )
            
            if response.status_code == 201:
                data = response.json()
                return {
                    'success': True,
                    'message': 'تم إنشاء المشكلة بنجاح',
                    'issue_number': data['number'],
                    'url': data['html_url']
                }
            else:
                return {'success': False, 'message': f'خطأ: {response.text}'}
        
        except Exception as e:
            logger.error(f"خطأ في إنشاء المشكلة: {e}")
            return {'success': False, 'message': str(e)}
    
    def get_issues(self, state: str = "open") -> Dict:
        """الحصول على قائمة المشاكل"""
        try:
            owner = self.config.repo_owner or self.config.username
            repo = self.config.repo_name
            
            response = requests.get(
                f"{self.api_base}/repos/{owner}/{repo}/issues",
                params={'state': state, 'per_page': 100},
                headers=self.headers,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                return {
                    'success': True,
                    'issues': [
                        {
                            'number': issue['number'],
                            'title': issue['title'],
                            'body': issue['body'],
                            'state': issue['state'],
                            'url': issue['html_url'],
                            'created_at': issue['created_at']
                        }
                        for issue in data
                    ]
                }
            else:
                return {'success': False, 'message': 'خطأ في الحصول على المشاكل'}
        
        except Exception as e:
            logger.error(f"خطأ في الحصول على المشاكل: {e}")
            return {'success': False, 'message': str(e)}
    
    # ───────────────────────────────────────────────────────────────────────────
    # إدارة طلبات الدمج (Pull Requests)
    # ───────────────────────────────────────────────────────────────────────────
    
    def create_pull_request(self, pr: GitHubPullRequest) -> Dict:
        """إنشاء طلب دمج"""
        try:
            owner = self.config.repo_owner or self.config.username
            repo = self.config.repo_name
            
            payload = {
                "title": pr.title,
                "body": pr.body,
                "head": pr.head,
                "base": pr.base,
                "draft": pr.draft,
                "labels": pr.labels
            }
            
            response = requests.post(
                f"{self.api_base}/repos/{owner}/{repo}/pulls",
                json=payload,
                headers=self.headers,
                timeout=10
            )
            
            if response.status_code == 201:
                data = response.json()
                return {
                    'success': True,
                    'message': 'تم إنشاء طلب الدمج بنجاح',
                    'pr_number': data['number'],
                    'url': data['html_url']
                }
            else:
                return {'success': False, 'message': f'خطأ: {response.text}'}
        
        except Exception as e:
            logger.error(f"خطأ في إنشاء طلب الدمج: {e}")
            return {'success': False, 'message': str(e)}
    
    # ───────────────────────────────────────────────────────────────────────────
    # إحصائيات المستودع
    # ───────────────────────────────────────────────────────────────────────────
    
    def get_repo_stats(self) -> Dict:
        """الحصول على إحصائيات المستودع"""
        try:
            owner = self.config.repo_owner or self.config.username
            repo = self.config.repo_name
            
            response = requests.get(
                f"{self.api_base}/repos/{owner}/{repo}",
                headers=self.headers,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                return {
                    'success': True,
                    'name': data['name'],
                    'description': data['description'],
                    'stars': data['stargazers_count'],
                    'forks': data['forks_count'],
                    'watchers': data['watchers_count'],
                    'open_issues': data['open_issues_count'],
                    'language': data['language'],
                    'url': data['html_url'],
                    'created_at': data['created_at'],
                    'updated_at': data['updated_at'],
                    'pushed_at': data['pushed_at']
                }
            else:
                return {'success': False, 'message': 'خطأ في الحصول على الإحصائيات'}
        
        except Exception as e:
            logger.error(f"خطأ في الحصول على الإحصائيات: {e}")
            return {'success': False, 'message': str(e)}
    
    def get_contributors(self) -> Dict:
        """الحصول على قائمة المساهمين"""
        try:
            owner = self.config.repo_owner or self.config.username
            repo = self.config.repo_name
            
            response = requests.get(
                f"{self.api_base}/repos/{owner}/{repo}/contributors",
                params={'per_page': 100},
                headers=self.headers,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                return {
                    'success': True,
                    'contributors': [
                        {
                            'name': c['login'],
                            'avatar': c['avatar_url'],
                            'contributions': c['contributions'],
                            'profile_url': c['html_url']
                        }
                        for c in data
                    ]
                }
            else:
                return {'success': False, 'message': 'خطأ في الحصول على المساهمين'}
        
        except Exception as e:
            logger.error(f"خطأ في الحصول على المساهمين: {e}")
            return {'success': False, 'message': str(e)}
    
    # ───────────────────────────────────────────────────────────────────────────
    # الـ Webhooks
    # ───────────────────────────────────────────────────────────────────────────
    
    def create_webhook(self, webhook_url: str, events: List[str]) -> Dict:
        """إنشاء webhook"""
        try:
            owner = self.config.repo_owner or self.config.username
            repo = self.config.repo_name
            
            payload = {
                "name": "web",
                "active": True,
                "events": events,
                "config": {
                    "url": webhook_url,
                    "content_type": "json",
                    "insecure_ssl": "0"
                }
            }
            
            response = requests.post(
                f"{self.api_base}/repos/{owner}/{repo}/hooks",
                json=payload,
                headers=self.headers,
                timeout=10
            )
            
            if response.status_code == 201:
                data = response.json()
                return {
                    'success': True,
                    'message': 'تم إنشاء Webhook بنجاح',
                    'hook_id': data['id']
                }
            else:
                return {'success': False, 'message': f'خطأ: {response.text}'}
        
        except Exception as e:
            logger.error(f"خطأ في إنشاء Webhook: {e}")
            return {'success': False, 'message': str(e)}
