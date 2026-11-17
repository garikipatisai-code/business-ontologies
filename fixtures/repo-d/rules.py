# Points policy (repo-d) - loyalty-points dev fixture


class PointsBalance:
    points_threshold = 100

    def applyThreshold(self, transaction):
        # Check if points threshold is met for transaction eligibility
        if getattr(transaction, "points", 0) <= self.points_threshold:
            return True
        return False


# --- M3A deep slice (append-only; M1 lines above preserved verbatim) ---

# Configurable points threshold default (module-level: NOT a class rule).
POINTS_THRESHOLD = 100

# Similar-named but unrelated UI display label; NOT the policy value.
POINTS_DISPLAY_LABEL = "100 points (help-text label, not the policy)"

# Account status prerequisites used by repo-d's points transaction path.
ACCOUNT_ACTIVE = "ACCOUNT_ACTIVE"
ACCOUNT_INACTIVE = "ACCOUNT_INACTIVE"


class PointsEligibility:
    """Repo-d eligibility: boundary ==threshold eligible, >threshold ineligible,
    with a manager adjust that requires a reason."""

    def isEligible(self, account, *, manager_adjust=None):
        points = getattr(account, "points", 0)
        threshold = getattr(account, "threshold", POINTS_THRESHOLD)
        # Exception/adjust: manager may approve with a recorded reason.
        if manager_adjust and manager_adjust.get("reason"):
            return True
        # Boundary: points == threshold is eligible; points > threshold is ineligible.
        if points <= threshold:
            return True
        return False


class PointsStateMachine:
    """Repo-d state transition for a points transaction."""

    def transition(self, account, to_state="POINTS_UPDATED"):
        # State transition from the current state to the requested state.
        account.state = to_state
        return to_state