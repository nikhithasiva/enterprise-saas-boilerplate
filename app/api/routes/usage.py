"""
Usage tracking and analytics endpoints
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Dict, Any
from uuid import UUID

from app.core.database import get_db
from app.api.deps import get_current_active_user
from app.models.user import User
from app.models.organization import OrganizationMember
from app.services.usage_service import UsageService

router = APIRouter()


class UsageLimitCheck(BaseModel):
    """Response model for usage limit checks"""
    allowed: bool
    current_count: int
    limit: int | None
    remaining: int | None
    plan_name: str


class UsageSummaryResponse(BaseModel):
    """Response model for usage summary"""
    subscription: Dict[str, Any]
    plan: Dict[str, Any]
    usage: Dict[str, Any]


@router.get("/organization/{organization_id}/summary", response_model=UsageSummaryResponse)
async def get_usage_summary(
    organization_id: UUID,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get comprehensive usage summary for an organization.

    Returns:
    - Subscription status
    - Current plan details
    - Usage metrics (users, projects)
    - Remaining limits

    User must be a member of the organization.
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

    # Get usage summary
    summary = await UsageService.get_usage_summary(organization_id, db)

    return UsageSummaryResponse(**summary)


@router.get("/organization/{organization_id}/users/limit", response_model=UsageLimitCheck)
async def check_user_limit(
    organization_id: UUID,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Check user limit for an organization.

    Returns current usage and remaining capacity.

    Useful before attempting to add a new user to verify if it's allowed.
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

    # Check user limit
    usage = await UsageService.check_user_limit(organization_id, db)

    return UsageLimitCheck(**usage)


@router.get("/organization/{organization_id}/projects/limit", response_model=UsageLimitCheck)
async def check_project_limit(
    organization_id: UUID,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Check project limit for an organization.

    Returns current usage and remaining capacity.

    Useful before attempting to create a new project to verify if it's allowed.
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

    # Check project limit
    usage = await UsageService.check_project_limit(organization_id, db)

    return UsageLimitCheck(**usage)
