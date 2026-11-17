#!/usr/bin/env python3
"""
Test script for AI Providers System

© 2026 SAYED_ELAZAMEY - All Rights Reserved
PROPRIETARY SOFTWARE - UNAUTHORIZED USE PROHIBITED

This software is protected by copyright law. Unauthorized reproduction,
distribution, or modification is strictly prohibited.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

def test_basic_imports():
    """Test basic imports."""
    print("🧪 Testing basic imports...")
    
    try:
        import fastapi
        import uvicorn
        import pydantic
        print("✓ Core dependencies available")
    except ImportError as e:
        print(f"✗ Core dependency missing: {e}")
        return False
    
    return True

def test_app_imports():
    """Test application imports."""
    print("🧪 Testing application imports...")
    
    try:
        from app.providers.base import BaseProvider, ChatMessage
        print("✓ Base provider classes imported")
    except ImportError as e:
        print(f"✗ Base provider import failed: {e}")
        return False
    
    try:
        from app.providers.factory import ProviderFactory
        print("✓ Provider factory imported")
    except ImportError as e:
        print(f"✗ Provider factory import failed: {e}")
        return False
    
    try:
        from app.core.config_manager import ConfigurationManager
        print("✓ Configuration manager imported")
    except ImportError as e:
        print(f"✗ Configuration manager import failed: {e}")
        return False
    
    return True

def test_main_app():
    """Test main FastAPI app."""
    print("🧪 Testing main FastAPI app...")
    
    try:
        from app.main import app
        print("✓ Main FastAPI app imported")
        return True
    except ImportError as e:
        print(f"✗ Main app import failed: {e}")
        return False

def test_health_endpoint():
    """Test health endpoint."""
    print("🧪 Testing health endpoint...")
    
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        
        client = TestClient(app)
        response = client.get("/health")
        
        if response.status_code == 200:
            print("✓ Health endpoint working")
            print(f"  Response: {response.json()}")
            return True
        else:
            print(f"✗ Health endpoint failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"✗ Health endpoint test failed: {e}")
        return False

def main():
    """Run all tests."""
    print("🚀 AI Providers System - Test Suite")
    print("© 2026 SAYED_ELAZAMEY - All Rights Reserved")
    print("PROPRIETARY SOFTWARE - UNAUTHORIZED USE PROHIBITED")
    print("=" * 50)
    
    tests = [
        test_basic_imports,
        test_app_imports,
        test_main_app,
        test_health_endpoint
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        try:
            if test():
                passed += 1
            print()
        except Exception as e:
            print(f"✗ Test failed with exception: {e}")
            print()
    
    print("=" * 50)
    print(f"📊 Test Results: {passed}/{total} passed")
    
    if passed == total:
        print("🎉 All tests passed! System is ready.")
        return True
    else:
        print("⚠️  Some tests failed. Check the output above.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)