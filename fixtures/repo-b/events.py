# Refund events (repo-b) - structural fixture for C2 (construction != publication).


class RefundRequested:
    """Event declaration. Constructing this object is NOT a publication."""

    def __init__(self, order_id, reason=None):
        self.order_id = order_id
        self.reason = reason


def build_refund_requested(order_id, reason=None):
    # Construction site only. No publish()/emit()/dispatch() call:
    # construction is not publication (C2).
    event = RefundRequested(order_id=order_id, reason=reason)
    return event
