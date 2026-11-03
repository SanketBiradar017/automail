from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.gemini_service import generate_from_template, TEMPLATE_TONES
from backend.gmail_auth import get_connected_email
from backend.models import EmailTemplate, TemplateFavorite
from backend.template_library import (
    ALL_CATEGORIES, BUILTINS, BUILTIN_BY_KEY, CATEGORIES, extract_variables, substitute,
)

router = APIRouter(prefix="/api/templates", tags=["Templates"])


def current_owner() -> str:
    """User identity = the connected Gmail account (same auth as the rest of the app)."""
    email = get_connected_email()
    if not email:
        raise HTTPException(status_code=401, detail="No Gmail account connected. Please connect your Gmail account.")
    return email.lower()


class TemplateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    category: str = "Other"
    subject: str = Field("", max_length=300)
    body: str = Field(..., min_length=1, max_length=8000)

    @field_validator("name", "subject")
    @classmethod
    def _strip(cls, v):
        return v.strip()

    @field_validator("category")
    @classmethod
    def _cat(cls, v):
        if v not in ALL_CATEGORIES:
            raise ValueError(f"category must be one of {ALL_CATEGORIES}")
        return v


class RefIn(BaseModel):
    ref: str = Field(..., max_length=100)


class FavoriteIn(RefIn):
    favorite: bool


class GenerateIn(BaseModel):
    subject: str = Field("", max_length=300)
    body: str = Field(..., min_length=1, max_length=8000)
    tone: str = "professional"
    instructions: str = Field("", max_length=800)
    variables: Dict[str, str] = Field(default_factory=dict)

    @field_validator("variables")
    @classmethod
    def _cap(cls, v):
        if len(v) > 40:
            raise ValueError("Too many variables.")
        return {k[:60]: val[:300] for k, val in v.items()}


def _serialize_builtin(t: dict, favs: set) -> dict:
    ref = f"builtin:{t['key']}"
    return {"ref": ref, "id": None, "is_builtin": True, "name": t["name"], "category": t["category"],
            "description": t["description"], "subject": t["subject"], "body": t["body"],
            "variables": extract_variables(t["subject"], t["body"]), "is_favorite": ref in favs}


def _serialize_custom(t: EmailTemplate, favs: set) -> dict:
    ref = f"custom:{t.id}"
    return {"ref": ref, "id": t.id, "is_builtin": False, "name": t.name, "category": t.category,
            "description": "", "subject": t.subject, "body": t.body,
            "variables": extract_variables(t.subject, t.body), "is_favorite": ref in favs,
            "updated_at": t.updated_at}


def _favs(db: Session, owner: str) -> set:
    return {f.ref for f in db.query(TemplateFavorite).filter(TemplateFavorite.owner_email == owner)}


def _own(db: Session, owner: str, template_id: int) -> EmailTemplate:
    t = db.query(EmailTemplate).filter(
        EmailTemplate.id == template_id, EmailTemplate.owner_email == owner).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    return t


@router.get("")
def list_templates(
    q: str = Query("", max_length=100),
    category: str = Query("", max_length=50),
    favorites_only: bool = False,
    mine_only: bool = False,
    db: Session = Depends(get_db),
):
    owner = current_owner()
    favs = _favs(db, owner)
    items = [] if mine_only else [_serialize_builtin(t, favs) for t in BUILTINS]
    items += [_serialize_custom(t, favs) for t in
              db.query(EmailTemplate).filter(EmailTemplate.owner_email == owner)
              .order_by(EmailTemplate.updated_at.desc())]

    if category:
        items = [i for i in items if i["category"] == category]
    if favorites_only:
        items = [i for i in items if i["is_favorite"]]
    if q.strip():
        needle = q.strip().lower()
        items = [i for i in items if needle in " ".join(
            [i["name"], i["subject"], i["body"], i["description"], i["category"]]).lower()]

    return {"success": True, "templates": items,
            "categories": ALL_CATEGORIES, "builtin_categories": CATEGORIES,
            "tones": list(TEMPLATE_TONES)}


@router.post("")
def create_template(req: TemplateIn, db: Session = Depends(get_db)):
    owner = current_owner()
    t = EmailTemplate(owner_email=owner, name=req.name, category=req.category,
                      subject=req.subject, body=req.body)
    db.add(t)
    db.commit()
    db.refresh(t)
    return {"success": True, "template": _serialize_custom(t, _favs(db, owner))}


@router.put("/{template_id}")
def update_template(template_id: int, req: TemplateIn, db: Session = Depends(get_db)):
    owner = current_owner()
    t = _own(db, owner, template_id)
    t.name, t.category, t.subject, t.body = req.name, req.category, req.subject, req.body
    db.commit()
    db.refresh(t)
    return {"success": True, "template": _serialize_custom(t, _favs(db, owner))}


@router.delete("/{template_id}")
def delete_template(template_id: int, db: Session = Depends(get_db)):
    owner = current_owner()
    t = _own(db, owner, template_id)
    db.query(TemplateFavorite).filter(
        TemplateFavorite.owner_email == owner, TemplateFavorite.ref == f"custom:{t.id}"
    ).delete()
    db.delete(t)
    db.commit()
    return {"success": True, "message": "Template deleted"}


@router.post("/duplicate")
def duplicate_template(req: RefIn, db: Session = Depends(get_db)):
    owner = current_owner()
    kind, _, ident = req.ref.partition(":")
    if kind == "builtin" and ident in BUILTIN_BY_KEY:
        src = BUILTIN_BY_KEY[ident]
        name, cat, subject, body = src["name"], src["category"], src["subject"], src["body"]
    elif kind == "custom" and ident.isdigit():
        t = _own(db, owner, int(ident))
        name, cat, subject, body = t.name, t.category, t.subject, t.body
    else:
        raise HTTPException(status_code=404, detail="Template not found")

    copy = EmailTemplate(owner_email=owner, name=f"{name} (copy)"[:120], category=cat,
                         subject=subject, body=body)
    db.add(copy)
    db.commit()
    db.refresh(copy)
    return {"success": True, "template": _serialize_custom(copy, _favs(db, owner))}


@router.post("/favorite")
def set_favorite(req: FavoriteIn, db: Session = Depends(get_db)):
    owner = current_owner()
    kind, _, ident = req.ref.partition(":")
    valid = (kind == "builtin" and ident in BUILTIN_BY_KEY) or (
        kind == "custom" and ident.isdigit() and
        db.query(EmailTemplate).filter(EmailTemplate.id == int(ident),
                                       EmailTemplate.owner_email == owner).first() is not None)
    if not valid:
        raise HTTPException(status_code=404, detail="Template not found")

    q = db.query(TemplateFavorite).filter(
        TemplateFavorite.owner_email == owner, TemplateFavorite.ref == req.ref)
    existing = q.first()
    if req.favorite and not existing:
        db.add(TemplateFavorite(owner_email=owner, ref=req.ref))
    elif not req.favorite and existing:
        q.delete()
    db.commit()
    return {"success": True, "ref": req.ref, "is_favorite": req.favorite}


@router.post("/generate")
def generate(req: GenerateIn):
    """Personalises the template exactly as edited in the UI. Sending is a
    separate, explicit step through the existing /api/email/send endpoint."""
    current_owner()
    if req.tone not in TEMPLATE_TONES:
        raise HTTPException(status_code=422, detail=f"tone must be one of {sorted(TEMPLATE_TONES)}")

    subject = substitute(req.subject, req.variables)
    body = substitute(req.body, req.variables)
    try:
        result = generate_from_template(subject, body, req.tone, req.instructions, req.variables)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"success": True, **result}
