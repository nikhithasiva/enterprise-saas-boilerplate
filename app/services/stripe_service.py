"""Stripe integration service for subscription and billing management"""

import stripe
from typing import Optional, Dict, Any
from datetime import datetime

from app.core.config import settings
from app.models.subscription import SubscriptionStatus

# Initialize Stripe with secret key
stripe.api_key = settings.STRIPE_SECRET_KEY


class StripeService:
    """Service class for handling Stripe API operations"""

    @staticmethod
    async def create_customer(
        email: str,
        name: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> stripe.Customer:
        """
        Create a Stripe customer

        Args:
            email: Customer email address
            name: Customer name
            metadata: Additional metadata (e.g., organization_id)

        Returns:
            Stripe Customer object
        """
        try:
            customer = stripe.Customer.create(
                email=email,
                name=name,
                metadata=metadata or {}
            )
            return customer
        except stripe.error.StripeError as e:
            raise Exception(f"Failed to create Stripe customer: {str(e)}")

    @staticmethod
    async def get_customer(customer_id: str) -> stripe.Customer:
        """Retrieve a Stripe customer by ID"""
        try:
            return stripe.Customer.retrieve(customer_id)
        except stripe.error.StripeError as e:
            raise Exception(f"Failed to retrieve Stripe customer: {str(e)}")

    @staticmethod
    async def update_customer(
        customer_id: str,
        email: Optional[str] = None,
        name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> stripe.Customer:
        """Update a Stripe customer"""
        try:
            update_data = {}
            if email:
                update_data["email"] = email
            if name:
                update_data["name"] = name
            if metadata:
                update_data["metadata"] = metadata

            return stripe.Customer.modify(customer_id, **update_data)
        except stripe.error.StripeError as e:
            raise Exception(f"Failed to update Stripe customer: {str(e)}")

    @staticmethod
    async def create_product(
        name: str,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> stripe.Product:
        """Create a Stripe product"""
        try:
            return stripe.Product.create(
                name=name,
                description=description,
                metadata=metadata or {}
            )
        except stripe.error.StripeError as e:
            raise Exception(f"Failed to create Stripe product: {str(e)}")

    @staticmethod
    async def create_price(
        product_id: str,
        amount: int,
        currency: str = "usd",
        interval: str = "month",
        metadata: Optional[Dict[str, Any]] = None
    ) -> stripe.Price:
        """
        Create a Stripe price for a product

        Args:
            product_id: Stripe product ID
            amount: Price amount in cents
            currency: Currency code (default: usd)
            interval: Billing interval (month or year)
            metadata: Additional metadata

        Returns:
            Stripe Price object
        """
        try:
            return stripe.Price.create(
                product=product_id,
                unit_amount=amount,
                currency=currency,
                recurring={"interval": interval},
                metadata=metadata or {}
            )
        except stripe.error.StripeError as e:
            raise Exception(f"Failed to create Stripe price: {str(e)}")

    @staticmethod
    async def create_subscription(
        customer_id: str,
        price_id: str,
        trial_period_days: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> stripe.Subscription:
        """
        Create a Stripe subscription

        Args:
            customer_id: Stripe customer ID
            price_id: Stripe price ID
            trial_period_days: Number of trial days (optional)
            metadata: Additional metadata (e.g., organization_id, plan_id)

        Returns:
            Stripe Subscription object
        """
        try:
            subscription_data = {
                "customer": customer_id,
                "items": [{"price": price_id}],
                "metadata": metadata or {},
                "payment_behavior": "default_incomplete",
                "payment_settings": {"save_default_payment_method": "on_subscription"},
                "expand": ["latest_invoice.payment_intent"]
            }

            if trial_period_days:
                subscription_data["trial_period_days"] = trial_period_days

            return stripe.Subscription.create(**subscription_data)
        except stripe.error.StripeError as e:
            raise Exception(f"Failed to create Stripe subscription: {str(e)}")

    @staticmethod
    async def get_subscription(subscription_id: str) -> stripe.Subscription:
        """Retrieve a Stripe subscription by ID"""
        try:
            return stripe.Subscription.retrieve(subscription_id)
        except stripe.error.StripeError as e:
            raise Exception(f"Failed to retrieve Stripe subscription: {str(e)}")

    @staticmethod
    async def update_subscription(
        subscription_id: str,
        price_id: Optional[str] = None,
        cancel_at_period_end: Optional[bool] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> stripe.Subscription:
        """Update a Stripe subscription"""
        try:
            update_data = {}

            if price_id:
                # Get the subscription first to get the item ID
                subscription = stripe.Subscription.retrieve(subscription_id)
                update_data["items"] = [{
                    "id": subscription["items"]["data"][0].id,
                    "price": price_id
                }]

            if cancel_at_period_end is not None:
                update_data["cancel_at_period_end"] = cancel_at_period_end

            if metadata:
                update_data["metadata"] = metadata

            return stripe.Subscription.modify(subscription_id, **update_data)
        except stripe.error.StripeError as e:
            raise Exception(f"Failed to update Stripe subscription: {str(e)}")

    @staticmethod
    async def cancel_subscription(
        subscription_id: str,
        immediately: bool = False
    ) -> stripe.Subscription:
        """
        Cancel a Stripe subscription

        Args:
            subscription_id: Stripe subscription ID
            immediately: If True, cancel immediately. If False, cancel at period end.

        Returns:
            Stripe Subscription object
        """
        try:
            if immediately:
                return stripe.Subscription.delete(subscription_id)
            else:
                return stripe.Subscription.modify(
                    subscription_id,
                    cancel_at_period_end=True
                )
        except stripe.error.StripeError as e:
            raise Exception(f"Failed to cancel Stripe subscription: {str(e)}")

    @staticmethod
    async def construct_webhook_event(
        payload: bytes,
        signature: str,
        webhook_secret: str
    ) -> stripe.Event:
        """
        Construct and verify a Stripe webhook event

        Args:
            payload: Raw request body
            signature: Stripe-Signature header value
            webhook_secret: Webhook signing secret

        Returns:
            Verified Stripe Event object

        Raises:
            ValueError: If signature verification fails
        """
        try:
            return stripe.Webhook.construct_event(
                payload, signature, webhook_secret
            )
        except ValueError as e:
            raise ValueError(f"Invalid webhook payload: {str(e)}")
        except stripe.error.SignatureVerificationError as e:
            raise ValueError(f"Invalid webhook signature: {str(e)}")

    @staticmethod
    def map_stripe_status(stripe_status: str) -> str:
        """
        Map Stripe subscription status to internal status

        Args:
            stripe_status: Stripe subscription status

        Returns:
            Internal subscription status
        """
        status_mapping = {
            "active": SubscriptionStatus.ACTIVE.value,
            "canceled": SubscriptionStatus.CANCELED.value,
            "incomplete": SubscriptionStatus.INCOMPLETE.value,
            "incomplete_expired": SubscriptionStatus.INCOMPLETE_EXPIRED.value,
            "past_due": SubscriptionStatus.PAST_DUE.value,
            "trialing": SubscriptionStatus.TRIALING.value,
            "unpaid": SubscriptionStatus.UNPAID.value,
            "paused": SubscriptionStatus.PAUSED.value,
        }
        return status_mapping.get(stripe_status, SubscriptionStatus.INCOMPLETE.value)

    @staticmethod
    def extract_subscription_dates(
        stripe_subscription: stripe.Subscription
    ) -> Dict[str, Optional[datetime]]:
        """
        Extract billing dates from Stripe subscription

        Args:
            stripe_subscription: Stripe Subscription object

        Returns:
            Dictionary with current_period_start, current_period_end, canceled_at
        """
        return {
            "current_period_start": datetime.fromtimestamp(
                stripe_subscription.current_period_start
            ) if stripe_subscription.current_period_start else None,
            "current_period_end": datetime.fromtimestamp(
                stripe_subscription.current_period_end
            ) if stripe_subscription.current_period_end else None,
            "canceled_at": datetime.fromtimestamp(
                stripe_subscription.canceled_at
            ) if stripe_subscription.canceled_at else None,
        }
