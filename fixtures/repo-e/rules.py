# Store-credit policy (repo-e) - held-out fixture (M3 A2).
# Pure structural fixture: parsed, never imported or executed.


class StoreCreditPolicy:
    """Store-credit policy thresholds."""

    credit_limit = 5000      # threshold: max store-credit balance per account
    max_redeem_per_day = 10  # redemptions allowed per account per day


# Different-name / related-meaning pair (shape 4).
# MAX_CREDIT_BALANCE names the same threshold concept as StoreCreditPolicy
# credit_limit under a different name (plan/display view of the same bound),
# not a separate policy value.
MAX_CREDIT_BALANCE = 5000

# Related window expressed under a different name (credit expiry vs stale
# after): same concept window, different symbol name.
CREDIT_EXPIRY_DAYS = 90
CREDIT_STALE_AFTER = "90 days"
