#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
🚀 مبدِّئ النظام الموحد - Unified System Launcher
تشغيل المشروع كنظام واحد متكامل
"""

import sys
import os
import json
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from unified_system import (
    UnifiedIntegrationSystem,
    UnifiedSystemConfig,
    SmartLinkingEngine
)
from github_integration import GitHubConfig
from social_media_integration import (
    SocialMediaCredentials,
    SocialPlatform
)

def print_banner():
    """طباعة بانر البدء"""
    print("""
╔═══════════════════════════════════════════════════════════════════════════════╗
║                                                                               ║
║          🚀 مرحباً بك في النظام المتكامل - Unified System v2.0.0           ║
║                                                                               ║
║     المساعد الذكي + وسائل التواصل الاجتماعي + GitHub Integration            ║
║                                                                               ║
║                   جميع المكونات متكاملة في نظام واحد!                        ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
    """)

def show_main_menu():
    """عرض القائمة الرئيسية"""
    print("""
╔═══════════════════════════════════════════════════════════════════════════════╗
║                          🎯 القائمة الرئيسية                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝

1️⃣  تشغيل المساعد الذكي       (Smart Assistant)
2️⃣  إعدادات وسائل التواصل     (Social Media Settings)
3️⃣  إعدادات GitHub            (GitHub Configuration)
4️⃣  معالجة وتشارك آني         (Process & Share)
5️⃣  عرض حالة النظام            (System Status)
6️⃣  إنشاء تقرير شامل          (Generate Report)
7️⃣  تفعيل المزامنة التلقائية (Enable Auto Sync)
8️⃣  إدارة المشروع              (Project Management)
0️⃣  خروج                       (Exit)

═══════════════════════════════════════════════════════════════════════════════
    """)

def setup_system():
    """إعداد النظام"""
    print("\n⚙️ جاري إعداد النظام الموحد...\n")
    
    # إنشاء التكوين
    config = UnifiedSystemConfig(
        project_name="Ultimate BitNet Smart System",
        project_version="2.0.0",
        enable_smart_assistant=True,
        enable_social_media=True,
        enable_github=True,
        enable_learning=True
    )
    
    # إنشاء النظام
    system = UnifiedIntegrationSystem(config)
    
    # تهيئة المساعد الذكي
    system.initialize_smart_assistant()
    
    # تهيئة وسائل التواصل
    system.initialize_social_media()
    
    print("\n✅ تم إعداد النظام بنجاح!")
    
    return system

def configure_social_media(system: UnifiedIntegrationSystem):
    """إعداد وسائل التواصل الاجتماعي"""
    print("""
╔════════════════════════════════════════════════════════════════════════════════╗
║                      ⚙️ إعداد وسائل التواصل الاجتماعي                       ║
╚════════════════════════════════════════════════════════════════════════════════╝

هذا القسم يسمح لك بربط حساباتك على منصات التواصل المختلفة.

المنصات المدعومة:
  • Twitter (تويتر)
  • Facebook (فيسبوك)
  • Telegram (تليجرام)
  • Instagram (إنستجرام) - قريباً
  • LinkedIn (لينكد إن) - قريباً

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    """)
    
    choice = input("👉 اختر منصة للإعداد (1=Twitter, 2=Facebook, 3=Telegram, 0=رجوع): ")
    
    if choice == '1':
        print("\n🐦 إعداد Twitter:")
        token = input("أدخل Access Token: ").strip()
        username = input("أدخل اسم المستخدم: ").strip()
        
        if system.social_manager:
            creds = SocialMediaCredentials(
                platform=SocialPlatform.TWITTER,
                api_key="",
                access_token=token
            )
            system.social_manager.register_platform(creds)
            print("✓ تم إعداد Twitter بنجاح!")
    
    elif choice == '2':
        print("\n📘 إعداد Facebook:")
        token = input("أدخل Access Token: ").strip()
        user_id = input("أدخل User ID: ").strip()
        
        if system.social_manager:
            creds = SocialMediaCredentials(
                platform=SocialPlatform.FACEBOOK,
                api_key="",
                access_token=token,
                user_id=user_id
            )
            system.social_manager.register_platform(creds)
            print("✓ تم إعداد Facebook بنجاح!")
    
    elif choice == '3':
        print("\n📱 إعداد Telegram:")
        token = input("أدخل Bot Token: ").strip()
        
        if system.social_manager:
            creds = SocialMediaCredentials(
                platform=SocialPlatform.TELEGRAM,
                api_key=token
            )
            system.social_manager.register_platform(creds)
            print("✓ تم إعداد Telegram بنجاح!")

def configure_github(system: UnifiedIntegrationSystem):
    """إعداد GitHub"""
    print("""
╔════════════════════════════════════════════════════════════════════════════════╗
║                      🔗 إعداد GitHub Integration                             ║
╚════════════════════════════════════════════════════════════════════════════════╝

هذا يسمح بربط مشروعك بـ GitHub وحفظ النتائج تلقائياً.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    """)
    
    token = input("👉 أدخل GitHub Personal Access Token: ").strip()
    username = input("👉 أدخل اسم المستخدم: ").strip()
    email = input("👉 أدخل بريدك الإلكتروني: ").strip()
    repo_name = input("👉 أدخل اسم المستودع (اتركه فارغاً للاستخدام الحالي): ").strip()
    
    config = GitHubConfig(
        token=token,
        username=username,
        email=email,
        repo_name=repo_name or "ultimate-bitnet"
    )
    
    if system.initialize_github(config):
        print("✓ تم إعداد GitHub بنجاح!")
    else:
        print("✗ فشل إعداد GitHub")

def run_smart_assistant(system: UnifiedIntegrationSystem):
    """تشغيل المساعد الذكي"""
    print("""
╔════════════════════════════════════════════════════════════════════════════════╗
║                      🤖 المساعد الذكي المتفاعل                              ║
╚════════════════════════════════════════════════════════════════════════════════╝

اكتب أسئلتك هنا. اترك فارغاً للرجوع للقائمة الرئيسية.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    """)
    
    while True:
        user_input = input("\n👉 اسأل: ").strip()
        
        if not user_input:
            break
        
        if user_input.lower() == 'exit':
            break
        
        # معالجة الاستعلام
        result = system.process_and_share(
            query=user_input,
            share_to_platforms=[SocialPlatform.TWITTER] if system.social_manager else []
        )
        
        if result['assistant_response']:
            response = result['assistant_response']
            print(f"\n🤖 الإجابة: {response.get('prediction', 'لا توجد إجابة')}")
            print(f"📊 الثقة: {response.get('confidence', 0):.1%}")
        
        # عرض حالة المشاركة
        if result['social_shares']:
            for share in result['social_shares']:
                status = "✓" if share['success'] else "✗"
                print(f"{status} {share['platform']}: {share.get('url', 'لم يتم النشر')}")

def show_system_status(system: UnifiedIntegrationSystem):
    """عرض حالة النظام"""
    status = system.get_system_status()
    
    print(f"""
╔════════════════════════════════════════════════════════════════════════════════╗
║                  📊 حالة النظام - System Status                              ║
╚════════════════════════════════════════════════════════════════════════════════╝

📌 معلومات النظام:
   • الاسم: {status['system_name']}
   • الإصدار: {status['version']}
   • وقت التشغيل: {status['uptime']:.1f} ثانية

🔧 المكونات:
   • المساعد الذكي: {'✓ فعال' if status['components']['smart_assistant'] else '✗ معطل'}
   • وسائل التواصل: {'✓ فعال' if status['components']['social_media'] else '✗ معطل'}
   • GitHub: {'✓ فعال' if status['components']['github'] else '✗ معطل'}

📈 الإحصائيات:
   • عدد العمليات: {status['operations']}

    """)

def enable_auto_sync(system: UnifiedIntegrationSystem):
    """تفعيل المزامنة التلقائية"""
    print("\n⚙️ جاري تفعيل المزامنة التلقائية...\n")
    
    engine = SmartLinkingEngine(system)
    engine.enable_auto_sync()
    
    print("""
✅ تم تفعيل المزامنة التلقائية!

سيتم الآن:
  ✓ مشاركة الردود على Twitter تلقائياً
  ✓ حفظ النتائج على GitHub تلقائياً
  ✓ تحديث الإحصائيات بشكل فوري
    """)

def main():
    """الدالة الرئيسية"""
    print_banner()
    
    # إعداد النظام
    system = setup_system()
    
    # القائمة الرئيسية
    while True:
        show_main_menu()
        choice = input("👉 اختر خياراً (0-8): ").strip()
        
        if choice == '1':
            run_smart_assistant(system)
        
        elif choice == '2':
            configure_social_media(system)
        
        elif choice == '3':
            configure_github(system)
        
        elif choice == '4':
            query = input("👉 أدخل الاستعلام: ").strip()
            if query:
                system.process_and_share(
                    query=query,
                    share_to_platforms=[SocialPlatform.TWITTER],
                    save_to_github=True
                )
                print("✓ تم المعالجة والمشاركة!")
        
        elif choice == '5':
            show_system_status(system)
        
        elif choice == '6':
            system.generate_report()
            print("✓ تم إنشاء التقرير!")
        
        elif choice == '7':
            enable_auto_sync(system)
        
        elif choice == '8':
            print("""
╔════════════════════════════════════════════════════════════════════════════════╗
║                      ⚙️ إدارة المشروع                                       ║
╚════════════════════════════════════════════════════════════════════════════════╝

1️⃣  حفظ التكوين
2️⃣  تحميل التكوين
3️⃣  إنشاء نسخة احتياطية
4️⃣  استعادة نسخة
0️⃣  رجوع
            """)
            sub_choice = input("اختر: ").strip()
            
            if sub_choice == '1':
                system.save_config()
                print("✓ تم حفظ التكوين!")
            
            elif sub_choice == '2':
                system.load_config()
                print("✓ تم تحميل التكوين!")
        
        elif choice == '0':
            print("""
╔════════════════════════════════════════════════════════════════════════════════╗
║                                                                               ║
║          👋 شكراً لاستخدام النظام المتكامل - Unified System v2.0.0         ║
║                                                                               ║
║                            وداعاً! 🚀                                        ║
║                                                                               ║
╚════════════════════════════════════════════════════════════════════════════════╝
            """)
            break
        
        else:
            print("❌ اختيار غير صحيح!")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 تم إيقاف البرنامج.")
    except Exception as e:
        print(f"\n❌ خطأ: {e}")
