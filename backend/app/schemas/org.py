import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import Role


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    industry: str | None
    timezone: str
    currency: str
    plan: str


class OrganizationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    industry: str | None = Field(default=None, max_length=100)


class TerritoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    region: str | None = Field(default=None, max_length=120)


class TerritoryOut(TerritoryIn):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID


class TeamIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class TeamOut(TeamIn):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    email: str
    full_name: str
    role: Role
    phone: str | None
    team_id: uuid.UUID | None
    territory_id: uuid.UUID | None
    is_active: bool
    whatsapp_opt_in: bool = False
    created_at: datetime


class MeOut(BaseModel):
    user: UserOut
    organization: OrganizationOut


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=8, max_length=72)
    role: Role
    phone: str | None = Field(default=None, pattern=r"^\+[1-9]\d{7,14}$")
    team_id: uuid.UUID | None = None
    territory_id: uuid.UUID | None = None


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    role: Role | None = None
    phone: str | None = Field(default=None, pattern=r"^\+[1-9]\d{7,14}$")
    team_id: uuid.UUID | None = None
    territory_id: uuid.UUID | None = None
    is_active: bool | None = None
    whatsapp_opt_in: bool | None = None
