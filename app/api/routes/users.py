from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import structlog

from app.core.database import get_db
from app.core.security import get_password_hash, verify_password
from app.models.user import User
from app.schemas.user import UserInDB, UserUpdate
from app.api.deps import get_current_active_user
from pydantic import BaseModel, Field

router = APIRouter()
logger = structlog.get_logger()


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8)


@router.get("/me", response_model=UserInDB)
async def get_current_user_profile(
    current_user: User = Depends(get_current_active_user)
):
    """
    Get current user's profile information.

    Requires valid JWT token in Authorization header.
    """
    return current_user


@router.put("/me", response_model=UserInDB)
async def update_current_user_profile(
    user_update: UserUpdate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Update current user's profile information.

    - **email**: Update email address (must be unique)
    - **full_name**: Update full name
    - **is_active**: Cannot be self-modified (admin only)
    """
    # Check if email is being changed and is already taken
    if user_update.email and user_update.email != current_user.email:
        result = await db.execute(select(User).where(User.email == user_update.email))
        existing_user = result.scalar_one_or_none()

        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already in use"
            )
        current_user.email = user_update.email
        current_user.is_verified = False  # Require re-verification after email change

    # Update other fields
    if user_update.full_name is not None:
        current_user.full_name = user_update.full_name

    # Don't allow users to self-activate/deactivate
    # is_active should only be modified by admins

    await db.commit()
    await db.refresh(current_user)

    logger.info("User profile updated", user_id=str(current_user.id))

    return current_user


@router.post("/me/change-password")
async def change_password(
    password_data: PasswordChange,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Change current user's password.

    - **current_password**: Current password for verification
    - **new_password**: New password (minimum 8 characters)
    """
    # Verify current password
    if not verify_password(password_data.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect current password"
        )

    # Update password
    current_user.hashed_password = get_password_hash(password_data.new_password)
    await db.commit()

    logger.info("User password changed", user_id=str(current_user.id))

    return {"message": "Password changed successfully"}


@router.delete("/me")
async def delete_current_user_account(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Delete current user's account (soft delete by deactivating).

    This will deactivate the account but preserve data for audit purposes.
    """
    current_user.is_active = False
    await db.commit()

    logger.info("User account deactivated", user_id=str(current_user.id))

    return {"message": "Account deactivated successfully"}
