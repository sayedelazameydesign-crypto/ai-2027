import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from core.router import RouterAgent
from core.planner import DAGPlanner
from utils.gemini_client import GeminiClient
from memory.sqlite_memory import SQLiteMemory
from core.agents.research_agent import ResearchAgent
from core.agents.coder_agent import CoderAgent
from core.agents.critic_agent import CriticAgent
from core.agents.executor_agent import ExecutorAgent

load_dotenv()

gemini = GeminiClient(os.getenv("GEMINI_API_KEY"))
router = RouterAgent(gemini)
planner = DAGPlanner(gemini)
memory = SQLiteMemory()

research_agent = ResearchAgent("Research", gemini)
coder_agent = CoderAgent("Coder", gemini)
critic_agent = CriticAgent("Critic", gemini)
executor_agent = ExecutorAgent("Executor", gemini)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text
    user_id = str(update.effective_user.id)
    
    memory.save(user_id, user_input, "")
    
    route_result = router.route(user_input)
    
    if "planner" in route_result.lower():
        plan = planner.create_plan(user_input)
        response = f"تم إنشاء خطة:\n{plan}"
    elif "research" in route_result.lower():
        response = research_agent.research(user_input)
    elif "coder" in route_result.lower():
        response = coder_agent.code(user_input)
    else:
        response = gemini.generate(f"نفذ: {user_input}")
    
    await update.message.reply_text(response)

def main():
    app = Application.builder().token(os.getenv("TELEGRAM_TOKEN")).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("🚀 Agentic AI System 2026 يعمل...")
    app.run_polling()

if __name__ == "__main__":
    main()
