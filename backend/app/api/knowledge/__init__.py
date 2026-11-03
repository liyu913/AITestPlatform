from app.api.knowledge.knowledge import router as knowledge_router
from app.api.knowledge.rag_search import router as rag_router

routers = [knowledge_router, rag_router]
