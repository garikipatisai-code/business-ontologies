# Refund policy (repo-b) - structural fixture


class RefundPolicy:
    refund_window_days = 30

    def applyWindow(self, order):
        # Retry/idempotency: retry up to 3 times; idempotent via order key.
        # Failure/compensation: on failure, compensate by restoring stock.
        if getattr(order, "age_days", 0) <= self.refund_window_days:
            return True
        return False


# --- M2 deep slice (append-only; M1 lines above preserved verbatim) ---

# Configurable refund-window default (module-level: NOT a class rule).
REFUND_WINDOW_DAYS = 30

# Similar-named but unrelated UI display label; NOT the policy value.
REFUND_WINDOW_DISPLAY = "30 days (help-text label, not the policy)"

# Order/payment status prerequisites used by repo-a's refund request path.
ORDER_PAID = "ORDER_PAID"
PAYMENT_SETTLED = "PAYMENT_SETTLED"


class RefundEligibility:
    """Repo-b eligibility: boundary ==window eligible, >window ineligible,
    with a manager override that requires a reason."""

    def isEligible(self, order, *, manager_override=None):
        age = getattr(order, "age_days", 0)
        window = getattr(order, "window_days", REFUND_WINDOW_DAYS)
        # Exception/override: manager may approve with a recorded reason.
        if manager_override and manager_override.get("reason"):
            return True
        # Boundary: age == window is eligible; age > window is ineligible.
        if age <= window:
            return True
        return False


class RefundStateMachine:
    """Repo-b state transition for a refund request."""

    def transition(self, order, to_state="REFUND_REQUESTED"):
        # State transition from the current state to the requested state.
        order.state = to_state
        return to_state
