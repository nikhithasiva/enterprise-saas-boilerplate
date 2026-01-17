from typing import Optional, Tuple
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.security import decode_token
from app.models.user import User
from app.models.organization import Organization, OrganizationMember
from app.schemas.auth import TokenData
from app.services.usage_service import UsageService

security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
) -> User:
    """
    Dependency to get the current authenticated user from JWT token.
    """
    token = credentials.credentials

    # Decode and verify token
    payload = decode_token(token, token_type="access")

    user_id: str = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Get user from database
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user"
        )

    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """
    Dependency to ensure user is active.
    """
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user"
        )
    return current_user


async def get_current_verified_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """
    Dependency to ensure user is verified.
    """
    if not current_user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User email not verified"
        )
    return current_user


async def get_current_superuser(
    current_user: User = Depends(get_current_user)
) -> User:
    """
    Dependency to ensure user is a superuser.
    """
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions"
        )
    return current_user


async def get_organization_member(
    organization_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
) -> Tuple[Organization, OrganizationMember]:
    """
    Dependency to get organization and verify user membership.

    Returns tuple of (organization, membership).
    Raises 404 if organization not found or user is not a member.
    """
    # Get organization
    result = await db.execute(
        select(Organization).where(Organization.id == organization_id)
    )
    organization = result.scalar_one_or_none()

    if not organization or not organization.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found"
        )

    # Check membership
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

    return organization, membership


async def require_organization_role(
    required_roles: list[str],
    organization_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
) -> OrganizationMember:
    """
    Dependency to verify user has required role in organization.

    Example: require_organization_role(["owner", "admin"])
    """
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

    if membership.role not in required_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Requires one of these roles: {', '.join(required_roles)}"
        )

    return membership


def require_role(allowed_roles: list[str]):
    """
    Factory function to create role-checking dependency.

    Usage:
    @router.get("/endpoint", dependencies=[Depends(require_role(["owner", "admin"]))])
    """
    async def check_role(
        membership: OrganizationMember = Depends(get_organization_member)
    ):
        if membership.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of these roles: {', '.join(allowed_roles)}"
            )
        return membership

    return check_role


async def check_user_limit_dependency(
    organization_id: str,
    db: AsyncSession = Depends(get_db)
) -> None:
    """
    Dependency to check if organization can add more users.

    Raises HTTPException if limit is reached.

    Usage:
    @router.post("/invite", dependencies=[Depends(check_user_limit_dependency)])
    """
    from uuid import UUID
    can_add, reason = await UsageService.can_add_user(UUID(organization_id), db)

    if not can_add:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=reason
        )


async def check_project_limit_dependency(
    organization_id: str,
    db: AsyncSession = Depends(get_db)
) -> None:
    """
    Dependency to check if organization can add more projects.

    Raises HTTPException if limit is reached.

    Usage:
    @router.post("/projects", dependencies=[Depends(check_project_limit_dependency)])
    """
    from uuid import UUID
    can_add, reason = await UsageService.can_add_project(UUID(organization_id), db)

    if not can_add:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=reason
        )
