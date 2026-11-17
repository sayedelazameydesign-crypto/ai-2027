"""
Main FastAPI Application for AI Providers System

© 2026 SAYED_ELAZAMEY - All Rights Reserved
PROPRIETARY SOFTWARE - UNAUTHORIZED USE PROHIBITED

This is the main entry point for the advanced AI provider orchestration system
with intelligent routing, cost management, and comprehensive monitoring.

Developer: SAYED_ELAZAMEY
Email: sayedelazameydesign@gmail.com
Phone: 01126212452

This software is protected by copyright law. Unauthorized reproduction,
distribution, or modification is strictly prohibited and may result in
severe civil and criminal penalties.
"""

import os
import sys
import logging
from pathlib import Path

# Add the app directory to Python path
app_dir = Path(__file__).parent
sys.path.insert(0, str(app_dir))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

def main():
    """Main entry point for the application."""
    try:
        # Configuration
        host = os.getenv("HOST", "0.0.0.0")
        port = int(os.getenv("PORT", "8000"))
        reload = os.getenv("RELOAD", "false").lower() == "true"
        
        # Start the server using the enhanced endpoints
        from app.api.endpoints import app
        import uvicorn
        
        logger.info(f"Starting AI Providers System on {host}:{port}")
        logger.info("Basic features enabled:")
        logger.info("  ✓ Multi-Provider Support")
        logger.info("  ✓ Code Generation") 
        logger.info("  ✓ Error Analysis")
        logger.info("  ✓ Code Review")
        logger.info("  ✓ Performance Optimization")
        
        # Run the server
        uvicorn.run(
            app,
            host=host,
            port=port,
            reload=reload,
            log_level="info"
        )
        
    except Exception as e:
        logger.error(f"Failed to start application: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()