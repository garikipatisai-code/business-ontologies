# repo-c - duplicate symbol names with different meanings (collision fixture).
# These OrderService/RefundPolicy are unrelated to repo-a/repo-b; no cross-repo
# relation should be inferred from a bare name match.


class OrderService:
    """Unrelated meaning: a CRM order-label formatter (not repo-a's service)."""

    def formatLabel(self, order):
        return "CRM-ORDER-LABEL"


class RefundPolicy:
    """Unrelated meaning: a UI display-policy helper (not repo-b's policy)."""

    def displayLabel(self):
        return "REFUND_WINDOW_DISPLAY"
