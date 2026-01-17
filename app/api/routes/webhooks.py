"""
Stripe webhook handlers for subscription and billing events
"""

from fastapi import APIRouter, Request, HTTPException, status, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import structlog

from app.core.database import get_db
from app.core.config import settings
from app.models.subscription import Subscription, SubscriptionStatus
from app.services.stripe_service import StripeService

router = APIRouter()
stripe_service = StripeService()
logger = structlog.get_logger()


@router.post("/stripe")
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Handle Stripe webhook events.

    This endpoint receives events from Stripe when subscription status changes,
    payments are processed, or other billing events occur.

    Important webhook events handled:
    - customer.subscription.created
    - customer.subscription.updated
    - customer.subscription.deleted
    - invoice.paid
    - invoice.payment_failed
    - customer.subscription.trial_will_end

    Setup:
    1. Configure webhook URL in Stripe Dashboard: https://your-domain.com/api/webhooks/stripe
    2. Copy the webhook signing secret to .env as STRIPE_WEBHOOK_SECRET
    """
    # Get raw body and signature
    payload = await request.body()
    signature = request.headers.get("stripe-signature")

    if not signature:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing stripe-signature header"
        )

    # Verify webhook signature
    try:
        event = await stripe_service.construct_webhook_event(
            payload=payload,
            signature=signature,
            webhook_secret=settings.STRIPE_WEBHOOK_SECRET
        )
    except ValueError as e:
        logger.error("Invalid webhook signature", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

    # Log event
    logger.info("Stripe webhook received", event_type=event.type, event_id=event.id)

    # Handle different event types
    try:
        if event.type.startswith("customer.subscription."):
            await handle_subscription_event(event, db)
        elif event.type.startswith("invoice."):
            await handle_invoice_event(event, db)
        else:
            logger.info("Unhandled webhook event type", event_type=event.type)

        return {"status": "success"}

    except Exception as e:
        logger.error("Error processing webhook", event_type=event.type, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process webhook: {str(e)}"
        )


async def handle_subscription_event(event, db: AsyncSession):
    """
    Handle subscription-related webhook events.

    Events:
    - customer.subscription.created: New subscription created
    - customer.subscription.updated: Subscription updated (plan change, status change, etc.)
    - customer.subscription.deleted: Subscription canceled
    - customer.subscription.trial_will_end: Trial ending soon (3 days before)
    """
    stripe_subscription = event.data.object
    stripe_subscription_id = stripe_subscription.id

    logger.info(
        "Processing subscription event",
        event_type=event.type,
        subscription_id=stripe_subscription_id,
        status=stripe_subscription.status
    )

    # Find subscription in database
    result = await db.execute(
        select(Subscription).where(
            Subscription.stripe_subscription_id == stripe_subscription_id
        )
    )
    subscription = result.scalar_one_or_none()

    if not subscription:
        logger.warning(
            "Subscription not found in database",
            stripe_subscription_id=stripe_subscription_id
        )
        return

    # Update subscription status and dates
    old_status = subscription.status
    subscription.status = stripe_service.map_stripe_status(stripe_subscription.status)
    subscription.cancel_at_period_end = stripe_subscription.cancel_at_period_end

    # Extract and update dates
    dates = stripe_service.extract_subscription_dates(stripe_subscription)
    subscription.current_period_start = dates["current_period_start"]
    subscription.current_period_end = dates["current_period_end"]

    if dates["canceled_at"]:
        subscription.canceled_at = dates["canceled_at"]

    await db.commit()

    logger.info(
        "Subscription updated",
        subscription_id=str(subscription.id),
        old_status=old_status,
        new_status=subscription.status,
        event_type=event.type
    )

    # Handle specific events
    if event.type == "customer.subscription.deleted":
        logger.info(
            "Subscription canceled via webhook",
            subscription_id=str(subscription.id),
            organization_id=str(subscription.organization_id)
        )
    elif event.type == "customer.subscription.trial_will_end":
        logger.info(
            "Trial ending soon",
            subscription_id=str(subscription.id),
            trial_end=subscription.current_period_end
        )
        # TODO: Send notification email to organization owner


async def handle_invoice_event(event, db: AsyncSession):
    """
    Handle invoice-related webhook events.

    Events:
    - invoice.paid: Invoice payment succeeded
    - invoice.payment_failed: Invoice payment failed
    - invoice.payment_action_required: Requires additional action (3D Secure, etc.)
    """
    invoice = event.data.object
    stripe_subscription_id = invoice.subscription

    if not stripe_subscription_id:
        # One-time invoice, not related to subscription
        logger.info("Invoice not related to subscription", invoice_id=invoice.id)
        return

    logger.info(
        "Processing invoice event",
        event_type=event.type,
        invoice_id=invoice.id,
        subscription_id=stripe_subscription_id,
        amount=invoice.amount_paid,
        status=invoice.status
    )

    # Find subscription in database
    result = await db.execute(
        select(Subscription).where(
            Subscription.stripe_subscription_id == stripe_subscription_id
        )
    )
    subscription = result.scalar_one_or_none()

    if not subscription:
        logger.warning(
            "Subscription not found for invoice",
            stripe_subscription_id=stripe_subscription_id
        )
        return

    if event.type == "invoice.paid":
        # Payment succeeded
        logger.info(
            "Invoice paid",
            subscription_id=str(subscription.id),
            organization_id=str(subscription.organization_id),
            amount=invoice.amount_paid / 100,
            currency=invoice.currency
        )

        # Update subscription status to active if it was incomplete
        if subscription.status in [
            SubscriptionStatus.INCOMPLETE.value,
            SubscriptionStatus.PAST_DUE.value
        ]:
            subscription.status = SubscriptionStatus.ACTIVE.value
            await db.commit()
            logger.info(
                "Subscription activated after payment",
                subscription_id=str(subscription.id)
            )

        # TODO: Send invoice receipt email

    elif event.type == "invoice.payment_failed":
        # Payment failed
        logger.warning(
            "Invoice payment failed",
            subscription_id=str(subscription.id),
            organization_id=str(subscription.organization_id),
            attempt_count=invoice.attempt_count
        )

        # Update subscription status
        if subscription.status == SubscriptionStatus.ACTIVE.value:
            subscription.status = SubscriptionStatus.PAST_DUE.value
            await db.commit()

        # TODO: Send payment failure notification email

    elif event.type == "invoice.payment_action_required":
        # Additional action required (3D Secure, etc.)
        logger.info(
            "Invoice requires payment action",
            subscription_id=str(subscription.id),
            organization_id=str(subscription.organization_id)
        )
        # TODO: Send notification to complete payment action
