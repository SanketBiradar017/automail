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
    "https://www.googleapis.com/auth/userinfo.email "
    "openid"
)

GMAIL_SCOPES = os.getenv(
    "GMAIL_SCOPES",
    DEFAULT_GMAIL_SCOPES
).split()


CLIENT_SECRET_FILE = os.getenv(
    "CLIENT_SECRET_FILE",
    "credentials/client_secret.json"
)


TOKEN_DIRECTORY = os.getenv(
    "TOKEN_DIRECTORY",
    "tokens"
)


SENDER_EMAIL = os.getenv("SENDER_EMAIL")