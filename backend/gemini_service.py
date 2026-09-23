import os

from dotenv import load_dotenv
from google.genai.types import AutomaticFunctionCallingConfig
from langchain_google_genai import ChatGoogleGenerativeAI


# Load variables from .env
load_dotenv()

# We never pass tools/functions to Gemini here, so there is nothing for
# Automatic Function Calling to do - it only adds an unneeded warning
# ("Direct use of AFC in Models.generate_content is not recommended").
# Disabling it explicitly skips that code path instead of silently
# tripping it on every plain-text generation call.
_NO_AFC = AutomaticFunctionCallingConfig(disable=True)


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
        response = llm.invoke(prompt, automatic_function_calling=_NO_AFC)

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


def generate_followup_email(
    recipient_name: str,
    original_subject: str,
    original_body: str,
    previous_followups: list,
    followup_number: int,
    max_follow_ups: int
) -> str:

    previous_section = ""
    if previous_followups:
        numbered = "\n\n".join(
            f"Follow-up #{i + 1}:\n{text}" for i, text in enumerate(previous_followups)
        )
        previous_section = f"\nPrevious follow-ups already sent (do not repeat their wording):\n{numbered}\n"

    is_last = followup_number >= max_follow_ups

    prompt = f"""
You are a professional email writing assistant.

The recipient below has not replied to an earlier email. Write a short,
polite follow-up (this is follow-up #{followup_number} of {max_follow_ups}).

Recipient Name: {recipient_name}
Original Subject: {original_subject}
Original Email:
{original_body}
{previous_section}
Requirements:
- Start with "Hi {recipient_name},"
- Keep it brief: 2-4 sentences.
- Politely reference that you haven't heard back, without sounding pushy.
- Do not repeat the full original email - just a short reminder of its gist.
- {"Mention this is a final follow-up." if is_last else "Keep the tone light, not urgent."}
- End with:
Best regards,
Sanket
- Do not add a subject line.
- Do not use markdown.
- Do not add explanations.
- Return ONLY the email body.
"""

    try:
        response = llm.invoke(prompt, automatic_function_calling=_NO_AFC)

    except Exception as exc:
        raise RuntimeError(f"Gemini request failed: {exc}") from exc

    content = response.content

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