"""
Subscription management endpoints
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from typing import List
from uuid import UUID

from app.core.database import get_db
from app.api.deps import get_current_active_user
from app.models.user import User
from app.models.organization import Organization, OrganizationMember
from app.models.subscription import Subscription, Plan, SubscriptionStatus
from app.schemas.subscription import (
    SubscriptionCreate,
    SubscriptionUpdate,
    SubscriptionCancel,
    SubscriptionResponse
)
from app.services.stripe_service import StripeService

router = APIRouter()
stripe_service = StripeService()


async def verify_organization_owner(
    organization_id: UUID,
    current_user: User,
    db: AsyncSession
) -> Organization:
    """
    Verify that the current user is the owner or admin of the organization.

    Returns the organization if valid.
    Raises HTTPException if not authorized.
    """
    result = await db.execute(
        select(Organization).where(Organization.id == organization_id)
    )
    organization = result.scalar_one_or_none()

    if not organization or not organization.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found"
        )

    # Check if user is owner or admin
    result = await db.execute(
        select(OrganizationMember)
        .where(OrganizationMember.organization_id == organization_id)
        .where(OrganizationMember.user_id == current_user.id)
    )
    membership = result.scalar_one_or_none()

    if not membership or membership.role not in ["owner", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only organization owners and admins can manage subscriptions"
        )

    return organization


@router.post("/", response_model=SubscriptionResponse, status_code=status.HTTP_201_CREATED)
async def create_subscription(
    subscription_data: SubscriptionCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new subscription for an organization.

    This will:
    1. Verify the organization exists and user has permission
    2. Create a Stripe customer if not exists
    3. Create a Stripe subscription
    4. Store the subscription in the database

    **Note:** The subscription will be in 'incomplete' status until payment is confirmed.
    """
    # Verify organization ownership
    organization = await verify_organization_owner(
        subscription_data.organization_id,
        current_user,
        db
    )

    # Check if organization already has an active subscription
    result = await db.execute(
        select(Subscription)
        .where(Subscription.organization_id == organization.id)
        .where(Subscription.status.in_([
            SubscriptionStatus.ACTIVE.value,
            SubscriptionStatus.TRIALING.value
        ]))
    )
    existing_subscription = result.scalar_one_or_none()

    if existing_subscription:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Organization already has an active subscription"
        )

    # Get plan
    result = await db.execute(
        select(Plan).where(Plan.id == subscription_data.plan_id)
    )
    plan = result.scalar_one_or_none()

    if not plan or not plan.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan not found"
        )

    try:
        # Create Stripe customer if not exists
        if not organization.stripe_customer_id:
            stripe_customer = await stripe_service.create_customer(
                email=current_user.email,
                name=organization.name,
                metadata={
                    "organization_id": str(organization.id),
                    "user_id": str(current_user.id)
                }
            )
            organization.stripe_customer_id = stripe_customer.id
            await db.commit()

        # Create Stripe subscription
        stripe_subscription = await stripe_service.create_subscription(
            customer_id=organization.stripe_customer_id,
            price_id=plan.stripe_price_id,
            trial_period_days=subscription_data.trial_period_days,
            metadata={
                "organization_id": str(organization.id),
                "plan_id": str(plan.id)
            }
        )

        # Extract subscription dates
        dates = stripe_service.extract_subscription_dates(stripe_subscription)

        # Create subscription in database
        new_subscription = Subscription(
            organization_id=organization.id,
            plan_id=plan.id,
            stripe_subscription_id=stripe_subscription.id,
            status=stripe_service.map_stripe_status(stripe_subscription.status),
            current_period_start=dates["current_period_start"],
            current_period_end=dates["current_period_end"],
            cancel_at_period_end=False
        )

        db.add(new_subscription)
        await db.commit()
        await db.refresh(new_subscription)

        # Load plan relationship
        result = await db.execute(
            select(Subscription)
            .options(selectinload(Subscription.plan))
            .where(Subscription.id == new_subscription.id)
        )
        subscription_with_plan = result.scalar_one()

        return subscription_with_plan

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create subscription: {str(e)}"
        )


@router.get("/organization/{organization_id}", response_model=List[SubscriptionResponse])
async def list_organization_subscriptions(
    organization_id: UUID,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    List all subscriptions for an organization.

    Returns both active and historical subscriptions.
    """
    # Verify user is member of organization
    result = await db.execute(
        select(OrganizationMember)
        .where(OrganizationMember.organization_id == organization_id)
        .where(OrganizationMember.user_id == current_user.id)
    )
    membership = result.scalar_one_or_none()

    if not membership:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found or access denied"
        )

    # Get subscriptions
    result = await db.execute(
        select(Subscription)
        .options(selectinload(Subscription.plan))
        .where(Subscription.organization_id == organization_id)
        .order_by(Subscription.created_at.desc())
    )
    subscriptions = result.scalars().all()

    return subscriptions


@router.get("/{subscription_id}", response_model=SubscriptionResponse)
async def get_subscription(
    subscription_id: UUID,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get a specific subscription by ID.

    User must be a member of the organization that owns the subscription.
    """
    result = await db.execute(
        select(Subscription)
        .options(selectinload(Subscription.plan))
        .where(Subscription.id == subscription_id)
    )
    subscription = result.scalar_one_or_none()

    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found"
        )

    # Verify user is member of organization
    result = await db.execute(
        select(OrganizationMember)
        .where(OrganizationMember.organization_id == subscription.organization_id)
        .where(OrganizationMember.user_id == current_user.id)
    )
    membership = result.scalar_one_or_none()

    if not membership:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found or access denied"
        )

    return subscription


@router.put("/{subscription_id}", response_model=SubscriptionResponse)
async def update_subscription(
    subscription_id: UUID,
    subscription_data: SubscriptionUpdate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Update an existing subscription.

    Allows:
    - Changing the plan (upgrade/downgrade)
    - Setting cancel_at_period_end

    **Note:** Plan changes take effect immediately and are prorated by Stripe.
    """
    # Get subscription
    result = await db.execute(
        select(Subscription).where(Subscription.id == subscription_id)
    )
    subscription = result.scalar_one_or_none()

    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found"
        )

    # Verify organization ownership
    await verify_organization_owner(
        subscription.organization_id,
        current_user,
        db
    )

    try:
        # Prepare Stripe update
        stripe_updates = {}

        # Handle plan change
        if subscription_data.plan_id:
            result = await db.execute(
                select(Plan).where(Plan.id == subscription_data.plan_id)
            )
            new_plan = result.scalar_one_or_none()

            if not new_plan or not new_plan.is_active:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Plan not found"
                )

            stripe_updates["price_id"] = new_plan.stripe_price_id
            subscription.plan_id = new_plan.id

        # Handle cancel_at_period_end
        if subscription_data.cancel_at_period_end is not None:
            stripe_updates["cancel_at_period_end"] = subscription_data.cancel_at_period_end
            subscription.cancel_at_period_end = subscription_data.cancel_at_period_end

        # Update Stripe subscription if there are changes
        if stripe_updates and subscription.stripe_subscription_id:
            stripe_subscription = await stripe_service.update_subscription(
                subscription.stripe_subscription_id,
                **stripe_updates
            )

            # Update status and dates from Stripe
            subscription.status = stripe_service.map_stripe_status(stripe_subscription.status)
            dates = stripe_service.extract_subscription_dates(stripe_subscription)
            subscription.current_period_start = dates["current_period_start"]
            subscription.current_period_end = dates["current_period_end"]

        await db.commit()
        await db.refresh(subscription)

        # Load plan relationship
        result = await db.execute(
            select(Subscription)
            .options(selectinload(Subscription.plan))
            .where(Subscription.id == subscription.id)
        )
        subscription_with_plan = result.scalar_one()

        return subscription_with_plan

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update subscription: {str(e)}"
        )


@router.post("/{subscription_id}/cancel", response_model=SubscriptionResponse)
async def cancel_subscription(
    subscription_id: UUID,
    cancel_data: SubscriptionCancel,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Cancel a subscription.

    Options:
    - immediately: Cancel immediately (default: False)
    - If not immediate, subscription will remain active until period end

    **Note:** Immediate cancellation is non-refundable.
    """
    # Get subscription
    result = await db.execute(
        select(Subscription).where(Subscription.id == subscription_id)
    )
    subscription = result.scalar_one_or_none()

    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found"
        )

    # Verify organization ownership
    await verify_organization_owner(
        subscription.organization_id,
        current_user,
        db
    )

    # Check if already canceled
    if subscription.status == SubscriptionStatus.CANCELED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Subscription is already canceled"
        )

    try:
        # Cancel in Stripe
        if subscription.stripe_subscription_id:
            stripe_subscription = await stripe_service.cancel_subscription(
                subscription.stripe_subscription_id,
                immediately=cancel_data.immediately
            )

            # Update subscription
            subscription.status = stripe_service.map_stripe_status(stripe_subscription.status)
            subscription.cancel_at_period_end = stripe_subscription.cancel_at_period_end
            dates = stripe_service.extract_subscription_dates(stripe_subscription)
            subscription.canceled_at = dates["canceled_at"]

        else:
            # No Stripe subscription, just update locally
            subscription.status = SubscriptionStatus.CANCELED.value
            subscription.cancel_at_period_end = False

        await db.commit()
        await db.refresh(subscription)

        # Load plan relationship
        result = await db.execute(
            select(Subscription)
            .options(selectinload(Subscription.plan))
            .where(Subscription.id == subscription.id)
        )
        subscription_with_plan = result.scalar_one()

        return subscription_with_plan

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cancel subscription: {str(e)}"
        )
