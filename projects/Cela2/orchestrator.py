import os
from browser_use import Agent
from langchain_google_genai import ChatGoogleGenerativeAI

def run_agent_task(task_description, api_key):
    os.environ["GOOGLE_API_KEY"] = api_key
    llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash")
    
    # تعريف المتصفح للعمل في بيئة Streamlit Cloud
    agent = Agent(
        task=task_description,
        llm=llm,
        headless=True,  # ضروري جداً للسحابة
    )
    return agent.run()
