from pydantic import BaseModel
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
    id: UUID
    user_id: UUID
    organization_id: UUID
    role: str
    joined_at: datetime
    user: Optional[UserInDB] = None

    class Config:
        from_attributes = True


class OrganizationResponse(OrganizationBase):
    id: UUID
    owner_id: UUID
    stripe_customer_id: Optional[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime
    members: Optional[List[OrganizationMemberResponse]] = []

    class Config:
        from_attributes = True
