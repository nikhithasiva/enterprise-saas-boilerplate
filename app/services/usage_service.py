"""
Usage tracking and limitation enforcement service
"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Dict, Optional
from uuid import UUID
import structlog

from app.models.subscription import Subscription, Plan, SubscriptionStatus
from app.models.organization import OrganizationMember

logger = structlog.get_logger()


class UsageService:
    """Service for tracking usage and enforcing plan limitations"""

    @staticmethod
    async def get_organization_subscription(
        organization_id: UUID,
        db: AsyncSession
    ) -> Optional[Subscription]:
        """
        Get the active subscription for an organization.

        Returns None if no active subscription exists.
        """
        result = await db.execute(
            select(Subscription)
            .where(Subscription.organization_id == organization_id)
            .where(Subscription.status.in_([
                SubscriptionStatus.ACTIVE.value,
                SubscriptionStatus.TRIALING.value
            ]))
            .order_by(Subscription.created_at.desc())
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_organization_plan(
        organization_id: UUID,
        db: AsyncSession
    ) -> Optional[Plan]:
        """
        Get the active plan for an organization.

        Returns None if no active subscription exists.
        """
        subscription = await UsageService.get_organization_subscription(
            organization_id, db
        )

        if not subscription:
            return None

        result = await db.execute(
            select(Plan).where(Plan.id == subscription.plan_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def check_user_limit(
        organization_id: UUID,
        db: AsyncSession
    ) -> Dict[str, any]:
        """
        Check if organization has reached its user limit.

        Returns:
        {
            "allowed": bool,
            "current_count": int,
            "limit": int or None (None = unlimited),
            "remaining": int or None
        }
        """
        # Get plan
        plan = await UsageService.get_organization_plan(organization_id, db)

        # Count current users
        result = await db.execute(
            select(func.count(OrganizationMember.id))
            .where(OrganizationMember.organization_id == organization_id)
        )
        current_count = result.scalar()

        if not plan:
            # No active subscription - use free tier limits
            limit = 1  # Free tier allows only 1 user (owner)
            allowed = current_count < limit
        elif plan.max_users is None:
            # Unlimited users
            limit = None
            allowed = True
        else:
            # Limited users
            limit = plan.max_users
            allowed = current_count < limit

        remaining = None if limit is None else max(0, limit - current_count)

        return {
            "allowed": allowed,
            "current_count": current_count,
            "limit": limit,
            "remaining": remaining,
            "plan_name": plan.name if plan else "Free"
        }

    @staticmethod
    async def check_project_limit(
        organization_id: UUID,
        db: AsyncSession
    ) -> Dict[str, any]:
        """
        Check if organization has reached its project limit.

        Returns:
        {
            "allowed": bool,
            "current_count": int,
            "limit": int or None (None = unlimited),
            "remaining": int or None
        }

        Note: This is a placeholder. Implement project counting when Project model exists.
        """
        # Get plan
        plan = await UsageService.get_organization_plan(organization_id, db)

        # TODO: Count current projects when Project model is implemented
        current_count = 0

        if not plan:
            # No active subscription - use free tier limits
            limit = 1  # Free tier allows only 1 project
            allowed = current_count < limit
        elif plan.max_projects is None:
            # Unlimited projects
            limit = None
            allowed = True
        else:
            # Limited projects
            limit = plan.max_projects
            allowed = current_count < limit

        remaining = None if limit is None else max(0, limit - current_count)

        return {
            "allowed": allowed,
            "current_count": current_count,
            "limit": limit,
            "remaining": remaining,
            "plan_name": plan.name if plan else "Free"
        }

    @staticmethod
    async def get_usage_summary(
        organization_id: UUID,
        db: AsyncSession
    ) -> Dict[str, any]:
        """
        Get comprehensive usage summary for an organization.

        Returns:
        {
            "subscription": {...},
            "plan": {...},
            "usage": {
                "users": {...},
                "projects": {...}
            }
        }
        """
        # Get subscription and plan
        subscription = await UsageService.get_organization_subscription(
            organization_id, db
        )
        plan = await UsageService.get_organization_plan(organization_id, db)

        # Get usage limits
        users_usage = await UsageService.check_user_limit(organization_id, db)
        projects_usage = await UsageService.check_project_limit(organization_id, db)

        return {
            "subscription": {
                "active": subscription is not None,
                "status": subscription.status if subscription else None,
                "current_period_end": subscription.current_period_end if subscription else None
            },
            "plan": {
                "name": plan.name if plan else "Free",
                "price": plan.price_amount if plan else 0,
                "currency": plan.currency if plan else "usd",
                "interval": plan.interval if plan else None
            },
            "usage": {
                "users": users_usage,
                "projects": projects_usage
            }
        }

    @staticmethod
    async def can_add_user(
        organization_id: UUID,
        db: AsyncSession
    ) -> tuple[bool, Optional[str]]:
        """
        Check if an organization can add another user.

        Returns:
        (allowed: bool, reason: str or None)
        """
        usage = await UsageService.check_user_limit(organization_id, db)

        if not usage["allowed"]:
            if usage["limit"] is None:
                # Should never happen, but just in case
                return False, "Unable to determine user limit"
            return False, f"User limit reached ({usage['current_count']}/{usage['limit']}). Please upgrade your plan."

        return True, None

    @staticmethod
    async def can_add_project(
        organization_id: UUID,
        db: AsyncSession
    ) -> tuple[bool, Optional[str]]:
        """
        Check if an organization can add another project.

        Returns:
        (allowed: bool, reason: str or None)
        """
        usage = await UsageService.check_project_limit(organization_id, db)

        if not usage["allowed"]:
            if usage["limit"] is None:
                # Should never happen, but just in case
                return False, "Unable to determine project limit"
            return False, f"Project limit reached ({usage['current_count']}/{usage['limit']}). Please upgrade your plan."

        return True, None

    @staticmethod
    async def log_usage_event(
        organization_id: UUID,
        event_type: str,
        metadata: Optional[Dict] = None
    ):
        """
        Log a usage event for analytics.

        event_type: "user_added", "project_created", "api_call", etc.
        metadata: Additional event data

        Note: This is a placeholder. Implement proper analytics storage when needed.
        """
        logger.info(
            "Usage event",
            organization_id=str(organization_id),
            event_type=event_type,
            metadata=metadata
        )
        # TODO: Store in analytics database or send to analytics service
