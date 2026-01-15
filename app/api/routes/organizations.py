from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
import structlog
import re

from app.core.database import get_db
from app.models.user import User
from app.models.organization import Organization, OrganizationMember
from app.schemas.organization import (
    OrganizationCreate,
    OrganizationUpdate,
    OrganizationResponse,
    OrganizationMemberResponse
)
from app.api.deps import get_current_active_user

router = APIRouter()
logger = structlog.get_logger()


def generate_slug(name: str) -> str:
    """Generate a URL-safe slug from organization name"""
    slug = name.lower()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    return slug


@router.post("/", response_model=OrganizationResponse, status_code=status.HTTP_201_CREATED)
async def create_organization(
    org_data: OrganizationCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new organization.

    The current user becomes the owner and is automatically added as an admin member.
    - **name**: Organization name
    - **slug**: Optional URL-safe slug (auto-generated if not provided)
    - **description**: Optional description
    """
    # Generate slug if not provided
    slug = org_data.slug or generate_slug(org_data.name)

    # Check if slug is already taken
    result = await db.execute(select(Organization).where(Organization.slug == slug))
    existing_org = result.scalar_one_or_none()

    if existing_org:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Organization with slug '{slug}' already exists"
        )

    # Create organization
    new_org = Organization(
        name=org_data.name,
        slug=slug,
        description=org_data.description,
        owner_id=current_user.id,
        is_active=True
    )

    db.add(new_org)
    await db.flush()

    # Add creator as owner member
    owner_member = OrganizationMember(
        organization_id=new_org.id,
        user_id=current_user.id,
        role="owner"
    )

    db.add(owner_member)
    await db.commit()
    await db.refresh(new_org)

    logger.info(
        "Organization created",
        org_id=str(new_org.id),
        org_name=new_org.name,
        owner_id=str(current_user.id)
    )

    return new_org


@router.get("/", response_model=List[OrganizationResponse])
async def list_organizations(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    List all organizations the current user is a member of.

    Returns organizations where the user has any membership role.
    """
    # Get all organization memberships for the user
    result = await db.execute(
        select(Organization)
        .join(OrganizationMember)
        .where(OrganizationMember.user_id == current_user.id)
        .where(Organization.is_active == True)
    )

    organizations = result.scalars().all()

    return organizations


@router.get("/{organization_id}", response_model=OrganizationResponse)
async def get_organization(
    organization_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get a specific organization by ID.

    User must be a member of the organization to view it.
    """
    # Check if user is a member
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

    # Get organization
    result = await db.execute(
        select(Organization).where(Organization.id == organization_id)
    )

    organization = result.scalar_one_or_none()

    if not organization:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found"
        )

    return organization


@router.put("/{organization_id}", response_model=OrganizationResponse)
async def update_organization(
    organization_id: str,
    org_update: OrganizationUpdate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Update an organization.

    Only owners and admins can update organization details.
    """
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
            detail="Only owners and admins can update organization"
        )

    # Get organization
    result = await db.execute(
        select(Organization).where(Organization.id == organization_id)
    )

    organization = result.scalar_one_or_none()

    if not organization:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found"
        )

    # Update fields
    if org_update.name is not None:
        organization.name = org_update.name

    if org_update.description is not None:
        organization.description = org_update.description

    await db.commit()
    await db.refresh(organization)

    logger.info(
        "Organization updated",
        org_id=str(organization.id),
        updated_by=str(current_user.id)
    )

    return organization


@router.delete("/{organization_id}")
async def delete_organization(
    organization_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Delete an organization (soft delete by deactivating).

    Only the owner can delete an organization.
    This will deactivate the organization but preserve data for audit purposes.
    """
    # Check if user is owner
    result = await db.execute(
        select(Organization)
        .where(Organization.id == organization_id)
        .where(Organization.owner_id == current_user.id)
    )

    organization = result.scalar_one_or_none()

    if not organization:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found or you are not the owner"
        )

    # Soft delete
    organization.is_active = False
    await db.commit()

    logger.info(
        "Organization deactivated",
        org_id=str(organization.id),
        owner_id=str(current_user.id)
    )

    return {"message": "Organization deactivated successfully"}


@router.get("/{organization_id}/members", response_model=List[OrganizationMemberResponse])
async def list_organization_members(
    organization_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    List all members of an organization.

    User must be a member of the organization to view members.
    """
    # Check if user is a member
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

    # Get all members
    result = await db.execute(
        select(OrganizationMember)
        .where(OrganizationMember.organization_id == organization_id)
    )

    members = result.scalars().all()

    return members


@router.post("/{organization_id}/members", response_model=OrganizationMemberResponse)
async def add_organization_member(
    organization_id: str,
    user_email: str,
    role: str = "member",
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Add a new member to an organization.

    Only owners and admins can add members.
    Valid roles: owner, admin, member, viewer
    """
    # Validate role
    valid_roles = ["owner", "admin", "member", "viewer"]
    if role not in valid_roles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role. Must be one of: {', '.join(valid_roles)}"
        )

    # Check if current user is owner or admin
    result = await db.execute(
        select(OrganizationMember)
        .where(OrganizationMember.organization_id == organization_id)
        .where(OrganizationMember.user_id == current_user.id)
    )

    membership = result.scalar_one_or_none()

    if not membership or membership.role not in ["owner", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only owners and admins can add members"
        )

    # Find user by email
    result = await db.execute(select(User).where(User.email == user_email))
    user_to_add = result.scalar_one_or_none()

    if not user_to_add:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with email '{user_email}' not found"
        )

    # Check if user is already a member
    result = await db.execute(
        select(OrganizationMember)
        .where(OrganizationMember.organization_id == organization_id)
        .where(OrganizationMember.user_id == user_to_add.id)
    )

    existing_member = result.scalar_one_or_none()

    if existing_member:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is already a member of this organization"
        )

    # Add member
    new_member = OrganizationMember(
        organization_id=organization_id,
        user_id=user_to_add.id,
        role=role
    )

    db.add(new_member)
    await db.commit()
    await db.refresh(new_member)

    logger.info(
        "Member added to organization",
        org_id=organization_id,
        new_member_id=str(user_to_add.id),
        role=role,
        added_by=str(current_user.id)
    )

    return new_member


@router.put("/{organization_id}/members/{member_id}", response_model=OrganizationMemberResponse)
async def update_member_role(
    organization_id: str,
    member_id: str,
    new_role: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Update a member's role in an organization.

    Only owners can update member roles.
    Valid roles: owner, admin, member, viewer
    """
    # Validate role
    valid_roles = ["owner", "admin", "member", "viewer"]
    if new_role not in valid_roles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role. Must be one of: {', '.join(valid_roles)}"
        )

    # Check if current user is owner
    result = await db.execute(
        select(OrganizationMember)
        .where(OrganizationMember.organization_id == organization_id)
        .where(OrganizationMember.user_id == current_user.id)
    )

    current_membership = result.scalar_one_or_none()

    if not current_membership or current_membership.role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only owners can update member roles"
        )

    # Get member to update
    result = await db.execute(
        select(OrganizationMember)
        .where(OrganizationMember.id == member_id)
        .where(OrganizationMember.organization_id == organization_id)
    )

    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found"
        )

    # Don't allow owner to change their own role
    if member.user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change your own role"
        )

    # Update role
    member.role = new_role
    await db.commit()
    await db.refresh(member)

    logger.info(
        "Member role updated",
        org_id=organization_id,
        member_id=member_id,
        new_role=new_role,
        updated_by=str(current_user.id)
    )

    return member


@router.delete("/{organization_id}/members/{member_id}")
async def remove_organization_member(
    organization_id: str,
    member_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Remove a member from an organization.

    Only owners and admins can remove members.
    Members can remove themselves (leave organization).
    """
    # Get member to remove
    result = await db.execute(
        select(OrganizationMember)
        .where(OrganizationMember.id == member_id)
        .where(OrganizationMember.organization_id == organization_id)
    )

    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found"
        )

    # Check permissions
    if member.user_id == current_user.id:
        # User is removing themselves (leaving)
        if member.role == "owner":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Owner cannot leave organization. Transfer ownership first."
            )
    else:
        # User is removing someone else
        result = await db.execute(
            select(OrganizationMember)
            .where(OrganizationMember.organization_id == organization_id)
            .where(OrganizationMember.user_id == current_user.id)
        )

        current_membership = result.scalar_one_or_none()

        if not current_membership or current_membership.role not in ["owner", "admin"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only owners and admins can remove members"
            )

        # Admins cannot remove owners
        if current_membership.role == "admin" and member.role == "owner":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admins cannot remove owners"
            )

    # Remove member
    await db.delete(member)
    await db.commit()

    logger.info(
        "Member removed from organization",
        org_id=organization_id,
        removed_member_id=str(member.user_id),
        removed_by=str(current_user.id)
    )

    return {"message": "Member removed successfully"}
