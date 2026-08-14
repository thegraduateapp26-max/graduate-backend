"""
One-off setup: creates the "Graduate Premium" product/price ($3/month recurring) and a
webhook endpoint pointing at this backend's /api/stripe/webhook, using STRIPE_SECRET_KEY.
Prints the price ID and webhook signing secret to store as STRIPE_PRICE_ID and
STRIPE_WEBHOOK_SECRET (Railway env vars on graduate-backend) - the webhook secret can only
be read back at creation time, so save it immediately.

Idempotent for the product/price (reuses an existing "Graduate Premium" product/$3 price if
one is found) but NOT for the webhook endpoint - re-running creates a second one, since Stripe
doesn't expose a way to look the existing signing secret back up. Delete the old endpoint in
the Stripe dashboard first if you need to re-run this.

Usage:
    STRIPE_SECRET_KEY=sk_test_... python3 scripts/setup_stripe.py
"""
import os

import stripe

WEBHOOK_URL = "https://graduate-backend-production.up.railway.app/api/stripe/webhook"
WEBHOOK_EVENTS = [
    "checkout.session.completed",
    "customer.subscription.updated",
    "customer.subscription.deleted",
]


def get_or_create_product():
    for product in stripe.Product.list(active=True, limit=100).auto_paging_iter():
        if product.name == "Graduate Premium":
            return product
    return stripe.Product.create(
        name="Graduate Premium",
        description="Profile view insights, Spotlight uploads, message anyone, applicant counts, and member discounts.",
    )


def get_or_create_price(product_id):
    for price in stripe.Price.list(product=product_id, active=True, limit=100).auto_paging_iter():
        if price.unit_amount == 300 and price.currency == "usd" and price.recurring and price.recurring.interval == "month":
            return price
    return stripe.Price.create(
        product=product_id,
        unit_amount=300,
        currency="usd",
        recurring={"interval": "month"},
    )


def main():
    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]

    product = get_or_create_product()
    print(f"Product: {product.id} ({product.name})")

    price = get_or_create_price(product.id)
    print(f"Price: {price.id} (${price.unit_amount / 100}/{price.recurring.interval})")

    webhook = stripe.WebhookEndpoint.create(
        url=WEBHOOK_URL,
        enabled_events=WEBHOOK_EVENTS,
        description="Graduate Premium subscription events",
    )
    print(f"Webhook endpoint: {webhook.id} -> {WEBHOOK_URL}")

    print("\nSave these as Railway env vars on graduate-backend:")
    print(f"  STRIPE_PRICE_ID={price.id}")
    print(f"  STRIPE_WEBHOOK_SECRET={webhook.secret}")


if __name__ == "__main__":
    main()
