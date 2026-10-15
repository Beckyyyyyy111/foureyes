"""Stripe test-mode money movement. Authorise first, capture only after the Checker approves."""
import stripe

from .config import STRIPE_SECRET_KEY

stripe.api_key = STRIPE_SECRET_KEY
assert not STRIPE_SECRET_KEY.startswith("sk_live"), "Test mode only"


def authorize(amount_gbp: float, description: str, metadata: dict) -> stripe.PaymentIntent:
    """Places a hold on the company card. No money moves until capture()."""
    return stripe.PaymentIntent.create(
        amount=round(amount_gbp * 100),
        currency="gbp",
        payment_method="pm_card_visa",
        automatic_payment_methods={"enabled": True, "allow_redirects": "never"},
        capture_method="manual",
        confirm=True,
        description=description,
        metadata={k: str(v)[:500] for k, v in metadata.items()},
    )


def capture(pi_id: str) -> stripe.PaymentIntent:
    return stripe.PaymentIntent.capture(pi_id)


def cancel(pi_id: str, reason: str = "fraudulent") -> stripe.PaymentIntent:
    return stripe.PaymentIntent.cancel(pi_id, cancellation_reason=reason)


def dashboard_url(pi_id: str) -> str:
    return f"https://dashboard.stripe.com/test/payments/{pi_id}"
