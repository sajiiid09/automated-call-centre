import csv
import io
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Contact
from app.schemas import ContactCreate, ContactOut, ContactUpdate, ImportResult

router = APIRouter(prefix="/api/contacts", tags=["contacts"])


def _get_or_404(db: Session, contact_id: uuid.UUID) -> Contact:
    contact = db.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(404, "Contact not found")
    return contact


@router.get("", response_model=list[ContactOut])
def list_contacts(search: str = "", db: Session = Depends(get_db)):
    stmt = select(Contact).order_by(Contact.created_at.desc())
    if search:
        like = f"%{search}%"
        stmt = stmt.where(or_(Contact.name.ilike(like), Contact.phone.ilike(like)))
    return db.scalars(stmt).all()


@router.post("", response_model=ContactOut, status_code=201)
def create_contact(payload: ContactCreate, db: Session = Depends(get_db)):
    contact = Contact(**payload.model_dump())
    db.add(contact)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A contact with this phone number already exists")
    return contact


@router.get("/{contact_id}", response_model=ContactOut)
def get_contact(contact_id: uuid.UUID, db: Session = Depends(get_db)):
    return _get_or_404(db, contact_id)


@router.patch("/{contact_id}", response_model=ContactOut)
def update_contact(contact_id: uuid.UUID, payload: ContactUpdate, db: Session = Depends(get_db)):
    contact = _get_or_404(db, contact_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(contact, key, value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A contact with this phone number already exists")
    return contact


@router.delete("/{contact_id}", status_code=204)
def delete_contact(contact_id: uuid.UUID, db: Session = Depends(get_db)):
    db.delete(_get_or_404(db, contact_id))
    db.commit()


@router.post("/import", response_model=ImportResult)
async def import_contacts(file: UploadFile, db: Session = Depends(get_db)):
    raw = (await file.read()).decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(raw))
    if reader.fieldnames is None or "phone" not in [f.lower() for f in reader.fieldnames]:
        raise HTTPException(400, "CSV must have a header row including 'name' and 'phone'")

    imported, skipped, errors = 0, 0, []
    existing = set(db.scalars(select(Contact.phone)).all())
    for i, row in enumerate(reader, start=2):
        row = {k.lower().strip(): (v or "").strip() for k, v in row.items() if k}
        name, phone = row.get("name", ""), row.get("phone", "")
        if not name or not phone:
            errors.append(f"row {i}: missing name or phone")
            continue
        if phone in existing:
            skipped += 1
            continue
        db.add(Contact(name=name, phone=phone, notes=row.get("notes") or None))
        existing.add(phone)
        imported += 1
    db.commit()
    return ImportResult(imported=imported, skipped=skipped, errors=errors)
