import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    store_name: str = Field(min_length=2, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    store_id: uuid.UUID | None = None


class UserOut(BaseModel):
    id: uuid.UUID
    name: str
    email: EmailStr
    model_config = ConfigDict(from_attributes=True)


class StoreOut(BaseModel):
    id: uuid.UUID
    name: str
    role: str
    model_config = ConfigDict(from_attributes=True)


class AuthOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
    store: StoreOut
    available_stores: list[StoreOut] = Field(default_factory=list)


class MemberCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    password: str | None = Field(default=None, min_length=8, max_length=128)
    role: Literal["Manager", "Agent"] = "Agent"


class MemberOut(BaseModel):
    id: uuid.UUID
    name: str
    email: EmailStr
    role: str
    active: bool


class CaseUpdate(BaseModel):
    status: Literal["Open", "In progress", "Awaiting customer", "Escalated", "Resolved"] | None = None
    assigned_user_id: uuid.UUID | None = None
    human_takeover: bool | None = None


class CaseCreate(BaseModel):
    customer_name: str = Field(min_length=2, max_length=200)
    customer_email: EmailStr | None = None
    body: str = Field(min_length=1, max_length=5000)
    category: str = Field(default="General inquiry", min_length=2, max_length=100)
    urgency: Literal["Low", "Medium", "High"] = "Medium"


class MessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


class CaseMessageOut(BaseModel):
    id: uuid.UUID
    body: str
    incoming: bool
    sender_name: str | None = None
    sent_at: str
    delivery_status: str


class AuditOut(BaseModel):
    id: uuid.UUID
    action: str
    change_summary: dict
    actor_name: str | None = None
    occurred_at: str


class CaseOut(BaseModel):
    id: uuid.UUID
    case_ref: str
    customer: str
    email: str | None
    subject: str
    preview: str
    category: str
    urgency: str
    sentiment: str
    confidence: int
    status: str
    assigned: str
    assigned_user_id: uuid.UUID | None
    order_id: str | None
    language: str
    updated: str
    unread: bool
    takeover: bool
    messages: list[CaseMessageOut]
    audit: list[AuditOut]
