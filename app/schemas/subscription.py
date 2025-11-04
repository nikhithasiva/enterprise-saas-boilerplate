from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from uuid import UUID


class PlanBase(BaseModel):
    name: str
    slug: str
    description: Optional[str] = None
    price_amount: int
    currency: str = "usd"
    interval: str
    max_users: Optional[int] = None
    max_projects: Optional[int] = None


class PlanResponse(PlanBase):
    id: UUID
    stripe_product_id: Optional[str]
    stripe_price_id: Optional[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SubscriptionBase(BaseModel):
    plan_id: UUID


class SubscriptionCreate(SubscriptionBase):
    pass


class SubscriptionResponse(SubscriptionBase):
    id: UUID
    organization_id: UUID
    stripe_subscription_id: Optional[str]
    status: str
    current_period_start: Optional[datetime]
    current_period_end: Optional[datetime]
    cancel_at_period_end: bool
    created_at: datetime
    updated_at: datetime
    canceled_at: Optional[datetime]
    plan: Optional[PlanResponse] = None

    class Config:
        from_attributes = True
