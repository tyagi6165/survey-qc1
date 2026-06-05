"""
payment_service.py — Razorpay integration for SurveyQC.

Public API:
    create_order(plan)              → dict with id, amount, currency, key_id
    verify_payment(order_id, payment_id, signature) → bool
    PLANS                           → dict of plan metadata
"""

import hmac
import hashlib
import logging
import razorpay

log = logging.getLogger(__name__)

RAZORPAY_KEY_ID     = "rzp_live_SxrB6nqyDMGeFw"
RAZORPAY_KEY_SECRET = "87XAme3iFTMjfA2uqj2rEX7j"

client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

# Amount in paise (1 INR = 100 paise)
PLANS = {
    "Pro": {
        "display_name": "Pro",
        "amount":       249900,   # ₹2499
        "currency":     "INR",
        "description":  "SurveyQC Pro — 25 reports/month",
    },
    "Business": {
        "display_name": "Business",
        "amount":       2499900,  # ₹24999
        "currency":     "INR",
        "description":  "SurveyQC Business — Unlimited reports",
    },
}


def create_order(plan: str) -> dict:
    """
    Create a Razorpay order for the given plan.
    Returns a dict with order_id, amount, currency, and key_id for the frontend.
    Raises ValueError for unknown plans, RuntimeError on API failure.
    """
    if plan not in PLANS:
        raise ValueError(f"Unknown plan: {plan!r}")

    p = PLANS[plan]
    try:
        order = client.order.create({
            "amount":   p["amount"],
            "currency": p["currency"],
            "notes":    {"plan": plan},
        })
        log.info("Razorpay order created: id=%s plan=%s amount=%s",
                 order["id"], plan, p["amount"])
        return {
            "order_id":   order["id"],
            "amount":     order["amount"],
            "currency":   order["currency"],
            "key_id":     RAZORPAY_KEY_ID,
            "plan":       plan,
            "description": p["description"],
        }
    except Exception as exc:
        log.error("Razorpay create_order failed: plan=%s err=%s", plan, exc)
        raise RuntimeError(f"Could not create payment order: {exc}") from exc


def verify_payment(order_id: str, payment_id: str, signature: str) -> bool:
    """
    Verify Razorpay payment signature.
    Returns True if the signature is valid, False otherwise.
    """
    try:
        msg = f"{order_id}|{payment_id}".encode()
        expected = hmac.new(
            RAZORPAY_KEY_SECRET.encode(), msg, hashlib.sha256
        ).hexdigest()
        valid = hmac.compare_digest(expected, signature)
        if valid:
            log.info("Payment verified: order=%s payment=%s", order_id, payment_id)
        else:
            log.warning("Payment signature mismatch: order=%s payment=%s", order_id, payment_id)
        return valid
    except Exception as exc:
        log.error("verify_payment error: %s", exc)
        return False
