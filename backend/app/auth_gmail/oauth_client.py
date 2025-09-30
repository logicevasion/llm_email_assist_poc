from authlib.integrations.starlette_client import OAuth
from backend.app.config import settings


# Create a new OAuth instance for FastAPI
oauth = OAuth()


# Register the Google OAuth provider
oauth.register(
    name='google',
    client_id=settings.GOOGLE_CLIENT_ID,
    client_secret=settings.GOOGLE_CLIENT_SECRET,
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',

    client_kwargs={
        'scope': (
            'openid email profile '
            'https://www.googleapis.com/auth/gmail.readonly'
        )
    }
)