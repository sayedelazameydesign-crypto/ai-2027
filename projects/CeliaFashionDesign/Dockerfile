# استخدام صورة بايثون خفيفة
FROM python:3.10-slim

# تثبيت الأدوات الأساسية و Ollama لتشغيل الذكاء الاصطناعي محلياً
RUN apt-get update && apt-get install -y \
    curl \
    && curl -fsSL https://ollama.com/install.sh | sh \
    && apt-get clean

# إعداد مجلد العمل
WORKDIR /app

# نسخ متطلبات المشروع وتثبيتها
COPY ai-core/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# نسخ كود الـ AI Core وباقي الملفات الضرورية
COPY ai-core/ ./ai-core/
COPY . .

# إعداد سكريبت التشغيل المدمج
RUN echo '#!/bin/bash\n\
ollama serve & \n\
sleep 10 \n\
echo "Pulling Phi-3 model..." \n\
ollama pull phi3:mini \n\
echo "Starting FastAPI App..." \n\
python ai-core/src/main.py' > /app/start.sh

RUN chmod +x /app/start.sh

# المنفذ الذي يستخدمه Hugging Face
EXPOSE 8000

# تشغيل السكريبت
CMD ["/app/start.sh"]
