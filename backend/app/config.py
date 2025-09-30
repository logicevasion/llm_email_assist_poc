from pydantic_settings import BaseSettings

# manual environment variable loading is needed for dev when using uvicorn --reload
from dotenv import load_dotenv
import os


load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))


class Settings(BaseSettings):
    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: str
    SESSION_SECRET_KEY: str = "changeme-please"
    OPENROUTER_API_KEY: str


settings = Settings()
