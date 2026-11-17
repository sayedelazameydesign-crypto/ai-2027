import os
import json
from browser_use import Agent
from langchain_google_genai import ChatGoogleGenerativeAI
import asyncio

async def run_agent(task_description, api_key):
    os.environ["GOOGLE_API_KEY"] = api_key
    llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash")
    agent = Agent(
        task=task_description,
        llm=llm,
        headless=True,
    )
    result = await agent.run()
    return result

def handler(event, context):
    # الحصول على البيانات من طلب POST
    try:
        body = json.loads(event.get("body", "{}"))
        task = body.get("task")
        # المفتاح يتم قراءته من Environment Variables في Netlify
        api_key = os.environ.get("GOOGLE_API_KEY")

        if not task:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Task description is required"})
            }

        # تشغيل الوكيل بشكل غير متزامن
        result = asyncio.run(run_agent(task, api_key))

        return {
            "statusCode": 200,
            "body": json.dumps({"result": str(result)})
        }
    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }
