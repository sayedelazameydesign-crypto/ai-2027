#!/usr/bin/env python3
"""
Quick start script for AI Providers System

© 2026 SAYED_ELAZAMEY - All Rights Reserved
PROPRIETARY SOFTWARE - UNAUTHORIZED USE PROHIBITED

This script provides a simple way to start the system with basic configuration.

This software is protected by copyright law. Unauthorized reproduction,
distribution, or modification is strictly prohibited.
"""

import os
import sys
import subprocess
from pathlib import Path

def check_requirements():
    """Check if required packages are installed."""
    try:
        import fastapi
        import uvicorn
        import pydantic
        print("✓ Core dependencies found")
        return True
    except ImportError as e:
        print(f"✗ Missing dependencies: {e}")
        print("Please install requirements: pip install -r requirements.txt")
        return False

def setup_basic_env():
    """Setup basic environment variables for demo."""
    env_vars = {
        "HOST": "0.0.0.0",
        "PORT": "8000", 
        "RELOAD": "true",
        "LOGGING_LEVEL": "INFO",
        "FALLBACK_ENABLED": "true",
        "ENABLE_SECURITY_SCANNING": "true",
        "PII_REDACTION_ENABLED": "true",
        "SEMANTIC_ROUTING_ENABLED": "true",
        "COST_MANAGEMENT_ENABLED": "true",
        "SPECULATIVE_EXECUTION_ENABLED": "true"
    }
    
    for key, value in env_vars.items():
        if key not in os.environ:
            os.environ[key] = value
    
    print("✓ Basic environment configured")

def main():
    """Main function to start the system."""
    print("🚀 AI Providers System - Quick Start")
    print("© 2026 SAYED_ELAZAMEY - All Rights Reserved")
    print("PROPRIETARY SOFTWARE - UNAUTHORIZED USE PROHIBITED")
    print("=" * 50)
    
    # Check requirements
    if not check_requirements():
        sys.exit(1)
    
    # Setup environment
    setup_basic_env()
    
    # Check for .env file
    env_file = Path(".env")
    if not env_file.exists():
        print("⚠️  No .env file found. Using basic configuration.")
        print("   Copy .env.example to .env and add your API keys for full functionality.")
    else:
        print("✓ Found .env file")
    
    print("\n🎯 Starting system with features:")
    print("   ✓ PII Redaction")
    print("   ✓ Semantic Routing")
    print("   ✓ Cost Management") 
    print("   ✓ Speculative Execution")
    print("   ✓ Circuit Breakers")
    print("   ✓ Comprehensive Telemetry")
    
    print(f"\n🌐 Server will start at: http://localhost:{os.getenv('PORT', '8000')}")
    print("📚 API Documentation: http://localhost:8000/docs")
    print("❤️  Health Check: http://localhost:8000/v1/health")
    
    print("\n" + "=" * 50)
    
    try:
        # Start the server using uvicorn
        import uvicorn
        uvicorn.run(
            "app.api.endpoints:app",
            host=os.getenv("HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "8000")),
            reload=os.getenv("RELOAD", "true").lower() == "true",
            log_level=os.getenv("LOGGING_LEVEL", "info").lower()
        )
    except KeyboardInterrupt:
        print("\n\n👋 Shutting down AI Providers System...")
    except Exception as e:
        print(f"\n❌ Error starting system: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()