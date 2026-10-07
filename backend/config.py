import os
from dotenv import load_dotenv

load_dotenv()


GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.6-flash"
)


DEFAULT_GMAIL_SCOPES = (
    "https://www.googleapis.com/auth/gmail.send "
    "https://www.googleapis.com/auth/gmail.readonly "
    "https://www.googleapis.com/auth/userinfo.email "
    "openid"
)

GMAIL_SCOPES = os.getenv(
    "GMAIL_SCOPES",
    DEFAULT_GMAIL_SCOPES
).split()


# Google OAuth *app* credentials only - never a user's Gmail address.
# Falls back to the CLIENT_SECRET_FILE download from Google Cloud Console.
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")

CLIENT_SECRET_FILE = os.getenv(
    "CLIENT_SECRET_FILE",
    "credentials/client_secret.json"
)

# Optional. Defaults to <request origin>/api/auth/callback, which must be an
# authorized redirect URI in the Google Cloud OAuth client.
OAUTH_REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI")

# Fernet key used to encrypt stored Gmail tokens. If unset, one is generated
# and kept in tokens/.encryption_key (git-ignored).
TOKEN_ENCRYPTION_KEY = os.getenv("TOKEN_ENCRYPTION_KEY")
TOKEN_DIRECTORY = os.getenv("TOKEN_DIRECTORY", "tokens")
