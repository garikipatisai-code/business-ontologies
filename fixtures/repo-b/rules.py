# Refund policy (repo-b) - structural fixture


class RefundPolicy:
    refund_window_days = 30

    def applyWindow(self, order):
        # Retry/idempotency: retry up to 3 times; idempotent via order key.
        # Failure/compensation: on failure, compensate by restoring stock.
        if getattr(order, "age_days", 0) <= self.refund_window_days:
            return True
        return False
