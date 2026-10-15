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
