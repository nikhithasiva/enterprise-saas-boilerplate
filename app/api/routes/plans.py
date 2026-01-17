"""
Plan management endpoints (Admin only)
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from uuid import UUID

from app.core.database import get_db
from app.api.deps import get_current_superuser
from app.models.user import User
from app.models.subscription import Plan
from app.schemas.subscription import PlanCreate, PlanUpdate, PlanResponse
from app.services.stripe_service import StripeService

router = APIRouter()
stripe_service = StripeService()


@router.get("/", response_model=List[PlanResponse])
async def list_plans(
    skip: int = 0,
    limit: int = 100,
    active_only: bool = True,
    db: AsyncSession = Depends(get_db)
):
    """
    List all available plans.

    Public endpoint - anyone can view available plans.

    Query Parameters:
    - skip: Number of records to skip (pagination)
    - limit: Maximum number of records to return
    - active_only: Only return active plans (default: True)
    """
    query = select(Plan).offset(skip).limit(limit).order_by(Plan.price_amount)

    if active_only:
        query = query.where(Plan.is_active == True)

    result = await db.execute(query)
    plans = result.scalars().all()

    return plans


@router.get("/{plan_id}", response_model=PlanResponse)
async def get_plan(
    plan_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    """
    Get a specific plan by ID.

    Public endpoint - anyone can view plan details.
    """
    result = await db.execute(
        select(Plan).where(Plan.id == plan_id)
    )
    plan = result.scalar_one_or_none()

    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan not found"
        )

    return plan


@router.post("/", response_model=PlanResponse, status_code=status.HTTP_201_CREATED)
async def create_plan(
    plan_data: PlanCreate,
    current_user: User = Depends(get_current_superuser),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new subscription plan and corresponding Stripe product/price.

    **Admin only** - requires superuser permissions.

    This will:
    1. Create a Stripe product
    2. Create a Stripe price for the product
    3. Store the plan in the database with Stripe IDs
    """
    # Check if plan with slug already exists
    result = await db.execute(
        select(Plan).where(Plan.slug == plan_data.slug)
    )
    existing_plan = result.scalar_one_or_none()

    if existing_plan:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Plan with this slug already exists"
        )

    try:
        # Create Stripe product
        stripe_product = await stripe_service.create_product(
            name=plan_data.name,
            description=plan_data.description,
            metadata={
                "slug": plan_data.slug,
                "max_users": str(plan_data.max_users) if plan_data.max_users else "unlimited",
                "max_projects": str(plan_data.max_projects) if plan_data.max_projects else "unlimited"
            }
        )

        # Create Stripe price
        stripe_price = await stripe_service.create_price(
            product_id=stripe_product.id,
            amount=plan_data.price_amount,
            currency=plan_data.currency,
            interval=plan_data.interval,
            metadata={"slug": plan_data.slug}
        )

        # Create plan in database
        new_plan = Plan(
            name=plan_data.name,
            slug=plan_data.slug,
            description=plan_data.description,
            stripe_product_id=stripe_product.id,
            stripe_price_id=stripe_price.id,
            price_amount=plan_data.price_amount,
            currency=plan_data.currency,
            interval=plan_data.interval,
            max_users=plan_data.max_users,
            max_projects=plan_data.max_projects,
            features=plan_data.features,
            is_active=True
        )

        db.add(new_plan)
        await db.commit()
        await db.refresh(new_plan)

        return new_plan

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create plan: {str(e)}"
        )


@router.put("/{plan_id}", response_model=PlanResponse)
async def update_plan(
    plan_id: UUID,
    plan_data: PlanUpdate,
    current_user: User = Depends(get_current_superuser),
    db: AsyncSession = Depends(get_db)
):
    """
    Update an existing plan.

    **Admin only** - requires superuser permissions.

    Note: Stripe product/price cannot be modified.
    To change pricing, create a new plan and deprecate the old one.
    """
    # Get existing plan
    result = await db.execute(
        select(Plan).where(Plan.id == plan_id)
    )
    plan = result.scalar_one_or_none()

    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan not found"
        )

    # Update fields
    update_data = plan_data.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(plan, field, value)

    try:
        await db.commit()
        await db.refresh(plan)
        return plan
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update plan: {str(e)}"
        )


@router.delete("/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_plan(
    plan_id: UUID,
    current_user: User = Depends(get_current_superuser),
    db: AsyncSession = Depends(get_db)
):
    """
    Soft delete a plan (set is_active to False).

    **Admin only** - requires superuser permissions.

    Note: Plans are soft-deleted to preserve historical data for existing subscriptions.
    """
    result = await db.execute(
        select(Plan).where(Plan.id == plan_id)
    )
    plan = result.scalar_one_or_none()

    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan not found"
        )

    # Soft delete
    plan.is_active = False

    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete plan: {str(e)}"
        )
