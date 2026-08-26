import os

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI


# Load variables from .env
load_dotenv()


# Get Gemini API key
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")


# Check API key
if not GOOGLE_API_KEY:
    raise ValueError(
        "GOOGLE_API_KEY is missing. "
        "Please add GOOGLE_API_KEY to your .env file."
    )


# Initialize Gemini
llm = ChatGoogleGenerativeAI(
    model="gemini-3.6-flash",
    google_api_key=GOOGLE_API_KEY,
)


def generate_email(
    recipient_name: str,
    subject: str,
    context: str
) -> str:

    prompt = f"""
You are a professional email writing assistant.

Write a clear, professional, natural email using the information below.

Recipient Name: {recipient_name}
Subject: {subject}
Context: {context}

Requirements:
- Start with "Hi {recipient_name},"
- Write 2-3 concise paragraphs.
- Include the date/context naturally.
- End with:
Best regards,
Sanket
- Do not add a subject line.
- Do not use markdown.
- Do not add explanations.
- Return ONLY the email body.

Example format:

Hi {recipient_name},

I am writing to follow up regarding {subject}.

Please let me know if you need any further information.

Best regards,
Sanket
"""

    try:
        response = llm.invoke(prompt)

    except Exception as exc:
        raise RuntimeError(f"Gemini request failed: {exc}") from exc

    content = response.content

    # Handle Gemini/LangChain response formats
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        text_parts = []

        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    text_parts.append(text)
            elif isinstance(item, str):
                text_parts.append(item)

        return "".join(text_parts).strip()

    return str(content).strip()