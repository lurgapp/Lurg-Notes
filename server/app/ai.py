from .core.contracts import AIEngine
from .gemini import GeminiAIEngine
from .openrouter import OpenRouterAIEngine
from .config import settings

_ai_engine = OpenRouterAIEngine() if settings.ai_provider == "openrouter" else GeminiAIEngine()

async def get_ai_engine() -> AIEngine:
    return _ai_engine
