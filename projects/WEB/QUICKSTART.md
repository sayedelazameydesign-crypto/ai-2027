# 🚀 Quick Start Guide

**© 2026 SAYED_ELAZAMEY - All Rights Reserved**
**PROPRIETARY SOFTWARE - UNAUTHORIZED USE PROHIBITED**

---

## ⚡ Super Quick Start (Windows)

### 1. Install Dependencies
```bash
# Double-click this file or run in command prompt:
INSTALL_DEPENDENCIES.bat
```

### 2. Start Server
```bash
# Double-click this file or run in command prompt:
START_SERVER.bat
```

### 3. Access System
- **API Documentation**: http://localhost:8000/docs
- **Health Check**: http://localhost:8000/health
- **System Info**: http://localhost:8000/

---

## 🐍 Manual Installation (All Platforms)

### 1. Install Python Dependencies
```bash
pip install fastapi uvicorn pydantic pydantic-settings python-dotenv aiohttp httpx requests
```

### 2. Start the Server
```bash
python main.py
```

### 3. Test the System
```bash
python test_system.py
```

---

## 🔧 Configuration (Optional)

### 1. Copy Environment File
```bash
copy .env.example .env
```

### 2. Add Your API Keys
Edit `.env` file and add your API keys:
```bash
OPENAI_API_KEY=sk-your-openai-key
ANTHROPIC_API_KEY=sk-ant-your-anthropic-key
GOOGLE_API_KEY=your-google-key
# ... add other keys as needed
```

---

## 📡 API Usage Examples

### Basic Health Check
```bash
curl http://localhost:8000/health
```

### System Information
```bash
curl http://localhost:8000/
```

### Code Generation (Example)
```bash
curl -X POST "http://localhost:8000/api/generate" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Create a Python function to calculate fibonacci",
    "language": "python"
  }'
```

---

## 🛠️ Available Endpoints

- `GET /` - System information
- `GET /health` - Health check
- `GET /docs` - Interactive API documentation
- `GET /providers` - List available providers
- `POST /api/generate` - Generate code
- `POST /api/analyze-error` - Analyze errors
- `POST /api/code-review` - Review code
- `POST /api/optimize` - Optimize code

---

## 🆘 Troubleshooting

### Common Issues:

1. **Port 8000 already in use**
   - Change port: `set PORT=8001` then run server

2. **Python not found**
   - Install Python 3.8+ from python.org
   - Add Python to PATH

3. **Dependencies missing**
   - Run: `pip install -r requirements.txt`

4. **Permission errors**
   - Run command prompt as Administrator

---

## 📞 Support

**Developer**: SAYED_ELAZAMEY
- 📧 **Email**: sayedelazameydesign@gmail.com
- 📱 **Phone**: 01126212452

---

## ⚖️ Legal Notice

This software is proprietary and protected by copyright law. 
Unauthorized use, distribution, or modification is strictly prohibited.

**© 2026 SAYED_ELAZAMEY - All Rights Reserved**