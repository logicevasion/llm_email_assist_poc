from fastapi import FastAPI
from openai import OpenAI
from starlette.middleware.sessions import SessionMiddleware

from backend.app.auth_gmail.oauth_client import oauth
from backend.app.auth_gmail import router as auth_router
from backend.app.llm import router as llm_router
from backend.app.config import settings


# Create the FastAPI app instance
app = FastAPI()


# Add LLM client
app.state.llm = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=settings.OPENROUTER_API_KEY,
)


# Add session middleware
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SESSION_SECRET_KEY,
    max_age=86400,
    same_site="lax",
    https_only=False
)


# Attach the OAuth client to the app state
app.state.oauth = oauth


# Attach the router from the auth_gmail module
app.include_router(auth_router, tags=["Auth"])


# Attach the router from the llm module
app.include_router(llm_router)