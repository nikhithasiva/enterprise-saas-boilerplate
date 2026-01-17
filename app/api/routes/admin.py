"""
Admin dashboard endpoints for superusers
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from typing import Dict, Any, List
from datetime import datetime, timedelta
from pydantic import BaseModel

from app.core.database import get_db
from app.api.deps import get_current_superuser
from app.models.user import User
from app.models.organization import Organization, OrganizationMember
from app.models.subscription import Subscription, Plan, SubscriptionStatus

router = APIRouter()


class DashboardStats(BaseModel):
    """Admin dashboard statistics"""
    total_users: int
    active_users: int
    total_organizations: int
    active_organizations: int
    total_subscriptions: int
    active_subscriptions: int
    trialing_subscriptions: int
    revenue_metrics: Dict[str, Any]
    recent_signups: int  # Last 7 days
    recent_subscriptions: int  # Last 7 days


class OrganizationStats(BaseModel):
    """Organization statistics"""
    id: str
    name: str
    owner_email: str
    member_count: int
    created_at: datetime
    subscription_status: str
    plan_name: str | None
    mrr: int  # Monthly Recurring Revenue in cents


@router.get("/dashboard", response_model=DashboardStats)
async def get_dashboard_stats(
    current_user: User = Depends(get_current_superuser),
    db: AsyncSession = Depends(get_db)
):
    """
    Get comprehensive dashboard statistics for admins.

    **Admin only** - requires superuser permissions.

    Returns:
    - User metrics (total, active, recent signups)
    - Organization metrics (total, active)
    - Subscription metrics (total, active, trialing)
    - Revenue metrics (MRR, ARR)
    """
    # Calculate date for "recent" (last 7 days)
    seven_days_ago = datetime.utcnow() - timedelta(days=7)

    # User metrics
    result = await db.execute(select(func.count(User.id)))
    total_users = result.scalar()

    result = await db.execute(
        select(func.count(User.id)).where(User.is_active == True)
    )
    active_users = result.scalar()

    result = await db.execute(
        select(func.count(User.id)).where(User.created_at >= seven_days_ago)
    )
    recent_signups = result.scalar()

    # Organization metrics
    result = await db.execute(select(func.count(Organization.id)))
    total_organizations = result.scalar()

    result = await db.execute(
        select(func.count(Organization.id)).where(Organization.is_active == True)
    )
    active_organizations = result.scalar()

    # Subscription metrics
    result = await db.execute(select(func.count(Subscription.id)))
    total_subscriptions = result.scalar()

    result = await db.execute(
        select(func.count(Subscription.id))
        .where(Subscription.status == SubscriptionStatus.ACTIVE.value)
    )
    active_subscriptions = result.scalar()

    result = await db.execute(
        select(func.count(Subscription.id))
        .where(Subscription.status == SubscriptionStatus.TRIALING.value)
    )
    trialing_subscriptions = result.scalar()

    result = await db.execute(
        select(func.count(Subscription.id))
        .where(Subscription.created_at >= seven_days_ago)
    )
    recent_subscriptions = result.scalar()

    # Revenue metrics (MRR and ARR)
    result = await db.execute(
        select(Plan.price_amount, Plan.interval, func.count(Subscription.id))
        .join(Subscription, Subscription.plan_id == Plan.id)
        .where(Subscription.status == SubscriptionStatus.ACTIVE.value)
        .group_by(Plan.id, Plan.price_amount, Plan.interval)
    )
    subscription_data = result.all()

    # Calculate MRR (Monthly Recurring Revenue)
    mrr = 0
    for price, interval, count in subscription_data:
        if interval == "month":
            mrr += price * count
        elif interval == "year":
            mrr += (price / 12) * count

    mrr = int(mrr)  # Convert to int (cents)
    arr = mrr * 12  # Annual Recurring Revenue

    revenue_metrics = {
        "mrr": mrr,
        "mrr_formatted": f"${mrr / 100:.2f}",
        "arr": arr,
        "arr_formatted": f"${arr / 100:.2f}",
        "average_revenue_per_customer": int(mrr / active_subscriptions) if active_subscriptions > 0 else 0
    }

    return DashboardStats(
        total_users=total_users,
        active_users=active_users,
        total_organizations=total_organizations,
        active_organizations=active_organizations,
        total_subscriptions=total_subscriptions,
        active_subscriptions=active_subscriptions,
        trialing_subscriptions=trialing_subscriptions,
        revenue_metrics=revenue_metrics,
        recent_signups=recent_signups,
        recent_subscriptions=recent_subscriptions
    )


@router.get("/organizations", response_model=List[OrganizationStats])
async def list_organizations_admin(
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(get_current_superuser),
    db: AsyncSession = Depends(get_db)
):
    """
    List all organizations with detailed statistics.

    **Admin only** - requires superuser permissions.

    Returns organization details including:
    - Basic info (name, owner, member count)
    - Subscription status
    - Revenue contribution (MRR)
    """
    # Get organizations with owner info
    result = await db.execute(
        select(Organization, User.email)
        .join(User, Organization.owner_id == User.id)
        .offset(skip)
        .limit(limit)
        .order_by(desc(Organization.created_at))
    )
    org_data = result.all()

    stats_list = []

    for org, owner_email in org_data:
        # Count members
        result = await db.execute(
            select(func.count(OrganizationMember.id))
            .where(OrganizationMember.organization_id == org.id)
        )
        member_count = result.scalar()

        # Get active subscription
        result = await db.execute(
            select(Subscription, Plan.name, Plan.price_amount, Plan.interval)
            .join(Plan, Subscription.plan_id == Plan.id)
            .where(Subscription.organization_id == org.id)
            .where(Subscription.status.in_([
                SubscriptionStatus.ACTIVE.value,
                SubscriptionStatus.TRIALING.value
            ]))
            .order_by(desc(Subscription.created_at))
        )
        sub_data = result.first()

        if sub_data:
            subscription, plan_name, price_amount, interval = sub_data
            subscription_status = subscription.status
            # Calculate MRR for this organization
            if interval == "month":
                mrr = price_amount
            elif interval == "year":
                mrr = int(price_amount / 12)
            else:
                mrr = 0
        else:
            subscription_status = "none"
            plan_name = None
            mrr = 0

        stats_list.append(
            OrganizationStats(
                id=str(org.id),
                name=org.name,
                owner_email=owner_email,
                member_count=member_count,
                created_at=org.created_at,
                subscription_status=subscription_status,
                plan_name=plan_name,
                mrr=mrr
            )
        )

    return stats_list


@router.get("/users/{user_id}/details")
async def get_user_details_admin(
    user_id: str,
    current_user: User = Depends(get_current_superuser),
    db: AsyncSession = Depends(get_db)
):
    """
    Get detailed information about a specific user.

    **Admin only** - requires superuser permissions.

    Returns:
    - User profile
    - Organizations owned
    - Organization memberships
    - Activity history
    """
    # Get user
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    # Get owned organizations
    result = await db.execute(
        select(Organization).where(Organization.owner_id == user_id)
    )
    owned_organizations = result.scalars().all()

    # Get organization memberships
    result = await db.execute(
        select(OrganizationMember, Organization.name)
        .join(Organization, OrganizationMember.organization_id == Organization.id)
        .where(OrganizationMember.user_id == user_id)
    )
    memberships = result.all()

    return {
        "user": {
            "id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            "is_active": user.is_active,
            "is_verified": user.is_verified,
            "is_superuser": user.is_superuser,
            "created_at": user.created_at,
            "last_login": user.last_login
        },
        "owned_organizations": [
            {
                "id": str(org.id),
                "name": org.name,
                "created_at": org.created_at,
                "is_active": org.is_active
            }
            for org in owned_organizations
        ],
        "memberships": [
            {
                "organization_name": org_name,
                "role": membership.role,
                "joined_at": membership.joined_at
            }
            for membership, org_name in memberships
        ]
    }


@router.get("/subscriptions/expiring")
async def get_expiring_subscriptions(
    days: int = 7,
    current_user: User = Depends(get_current_superuser),
    db: AsyncSession = Depends(get_db)
):
    """
    Get subscriptions that are expiring soon.

    **Admin only** - requires superuser permissions.

    Query Parameters:
    - days: Number of days ahead to check (default: 7)

    Useful for proactive customer retention.
    """
    future_date = datetime.utcnow() + timedelta(days=days)

    result = await db.execute(
        select(Subscription, Organization.name, User.email, Plan.name)
        .join(Organization, Subscription.organization_id == Organization.id)
        .join(User, Organization.owner_id == User.id)
        .join(Plan, Subscription.plan_id == Plan.id)
        .where(Subscription.status == SubscriptionStatus.ACTIVE.value)
        .where(Subscription.current_period_end <= future_date)
        .where(Subscription.cancel_at_period_end == True)
        .order_by(Subscription.current_period_end)
    )
    expiring_subs = result.all()

    return [
        {
            "subscription_id": str(subscription.id),
            "organization_name": org_name,
            "owner_email": owner_email,
            "plan_name": plan_name,
            "expires_at": subscription.current_period_end,
            "days_remaining": (subscription.current_period_end - datetime.utcnow()).days
        }
        for subscription, org_name, owner_email, plan_name in expiring_subs
    ]


@router.get("/subscriptions/failed-payments")
async def get_failed_payments(
    current_user: User = Depends(get_current_superuser),
    db: AsyncSession = Depends(get_db)
):
    """
    Get subscriptions with failed payment status.

    **Admin only** - requires superuser permissions.

    Returns subscriptions in past_due or unpaid status that need attention.
    """
    result = await db.execute(
        select(Subscription, Organization.name, User.email, Plan.name)
        .join(Organization, Subscription.organization_id == Organization.id)
        .join(User, Organization.owner_id == User.id)
        .join(Plan, Subscription.plan_id == Plan.id)
        .where(Subscription.status.in_([
            SubscriptionStatus.PAST_DUE.value,
            SubscriptionStatus.UNPAID.value
        ]))
        .order_by(desc(Subscription.updated_at))
    )
    failed_subs = result.all()

    return [
        {
            "subscription_id": str(subscription.id),
            "organization_name": org_name,
            "owner_email": owner_email,
            "plan_name": plan_name,
            "status": subscription.status,
            "last_updated": subscription.updated_at
        }
        for subscription, org_name, owner_email, plan_name in failed_subs
    ]
