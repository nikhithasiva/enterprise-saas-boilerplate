from pydantic import BaseModel, ConfigDict
from typing import Optional, List
from datetime import datetime
from uuid import UUID
from app.schemas.user import UserInDB


class OrganizationBase(BaseModel):
    name: str
    slug: Optional[str] = None
    description: Optional[str] = None


class OrganizationCreate(OrganizationBase):
    pass


class OrganizationUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class OrganizationMemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    organization_id: UUID
    role: str
    joined_at: datetime
    user: Optional[UserInDB] = None


class OrganizationResponse(OrganizationBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_id: UUID
    stripe_customer_id: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    members: Optional[List[OrganizationMemberResponse]] = None
