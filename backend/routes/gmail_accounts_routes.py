from fastapi import APIRouter, HTTPException

from backend.gmail_auth import (
    list_gmail_accounts,
    connect_new_gmail_account,
    set_active_gmail_account,
    disconnect_gmail_account,
    get_gmail_service,
    get_sender_status,
    SenderMismatchError,
    GmailVerificationError,
)


router = APIRouter(
    prefix="/api/gmail-accounts",
    tags=["Gmail Accounts"]
)


def _serialize(account: dict) -> dict:
    # Defensive allow-list - never let a token_json or other internal field
    # leak through even if the caller passes a raw ORM-derived dict.
    return {
        "id": account["id"],
        "email": account["email"],
        "display_name": account.get("display_name"),
        "is_active": account["is_active"],
        "status": account["status"],
        "connected_at": account["connected_at"],
        "last_verified_at": account.get("last_verified_at"),
        "last_error": account.get("last_error"),
    }


@router.get("")
def list_accounts():
    accounts = list_gmail_accounts()
    return {"success": True, "accounts": [_serialize(a) for a in accounts]}


@router.post("/connect")
def connect_account():
    """+ Add Gmail: opens the OAuth consent screen for a new (or
    re-authorized) account. This call blocks while the user completes
    consent in their browser."""
    try:
        account_id, email, display_name = connect_new_gmail_account()

    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    except GmailVerificationError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Gmail OAuth authorization failed: {exc}")

    return {
        "success": True,
        "account": {"id": account_id, "email": email, "display_name": display_name},
        "message": f"Connected {email}."
    }


@router.post("/{account_id}/activate")
def activate_account(account_id: int):
    try:
        account = set_active_gmail_account(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return {"success": True, "account": account}


@router.post("/{account_id}/reconnect")
def reconnect_account(account_id: int):
    """Re-authorizes a specific, already-known account (e.g. after it shows
    'needs_reconnect'). Unlike /connect, this enforces that the account the
    user re-consents with is the SAME email as this row."""
    accounts = {a["id"]: a for a in list_gmail_accounts()}
    account = accounts.get(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Gmail account not found.")

    try:
        _, authenticated_email = get_gmail_service(account["email"], allow_oauth=True)

    except SenderMismatchError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    except GmailVerificationError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Gmail OAuth authorization failed: {exc}")

    return {"success": True, "message": f"Reconnected {authenticated_email}."}


@router.get("/{account_id}/status")
def account_status(account_id: int):
    accounts = {a["id"]: a for a in list_gmail_accounts()}
    account = accounts.get(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Gmail account not found.")

    matches, _ = get_sender_status(account["email"])
    return {"success": True, "connected": matches}


@router.delete("/{account_id}")
def disconnect_account(account_id: int):
    try:
        disconnect_gmail_account(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return {"success": True, "message": "Gmail account disconnected."}
