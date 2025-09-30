from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse

# Initialize API Router
router = APIRouter()


# Step 1: Start the OAuth login flow
@router.get("/login")
async def login(request: Request):
    redirect_uri = request.url_for("auth_callback")
    return await request.app.state.oauth.google.authorize_redirect(
        request,
        redirect_uri,
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )


# Step 2: Handle callback from Google
@router.get("/auth", name="auth_callback")
async def auth_callback(request: Request):
    try:
        token = await request.app.state.oauth.google.authorize_access_token(request)
        user_info = token.get("userinfo")

        # DEBUG: print token info
        print("TOKEN KEYS:", list(token.keys()))
        print("HAS REFRESH?", bool(token.get("refresh_token")))
        print("SCOPES:", token.get("scope"))

        # If no userinfo, auth unsuccessful → back to login
        if not user_info:
            return RedirectResponse(url="/auth_error?reason=missing_userinfo")

        # Save various user info into session to test if it works
        request.session["user"] = {
            "sub": user_info.get("sub"),
            "email": user_info.get("email"),
            "email_verified": user_info.get("email_verified"),
            "name": user_info.get("name"),
            "given_name": user_info.get("given_name"),
            "family_name": user_info.get("family_name"),
            "locale": user_info.get("locale"),
        }

        # Save the token to the session for later access
        request.session["token"] = token
        # Save the scopes for future scope checks
        request.session["granted_scopes"] = token.get("scope")

        return RedirectResponse(url="/success")

    except Exception:
        raise HTTPException(status_code=401, detail="Google authentication failed")


# Success route (after login)
# Displays the user's information
@router.get("/success")
async def success(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login")

    return {
        "message": "Authentication successful!",
        "identity": {
            "sub": user["sub"],
            "email": user["email"],
            "email_verified": user["email_verified"],
        },
        "profile": {
            "name": user["name"],
            "given_name": user["given_name"],
            "family_name": user["family_name"],
            "locale": user["locale"],
        },
    }


# Logout
@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/")


# Temporary Home Page
@router.get("/", response_class=HTMLResponse)
async def home():
    return """
    <html>
        <head>
            <title>LLM Email Assistant</title>
        </head>
        <body>
            <h1>LLM Email Assistant backend is running.</h1>
            <p>
                <a href="/login">
                    <button>Login with Google</button>
                </a>
            </p>
        </body>
    </html>
    """


# Error route (auth related)
@router.get("/auth_error")
async def auth_error(reason: str = "unknown"):
    messages = {
        "missing_userinfo": "Google did not return user information.",
        "invalid_token": "The access token was invalid or expired.",
        "unknown": "An unknown error occurred."
    }
    return {
        "error": "Authentication failed",
        "detail": messages.get(reason, "Unexpected error"),
        "next": "/login"
    }