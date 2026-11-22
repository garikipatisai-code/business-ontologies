# Store-credit exchange (repo-e) - held-out fixture (M3 A2).
# Pure structural fixture: parsed, never imported or executed. No repo imports,
# no secrets, no network.


# Exchange: store-credit issuance/redemption.
# Control shape: dispatch-table driven over an action queue (loop),
# structurally unlike the refund path's single-comparison boolean method.


class StoreCreditLedger:
    """Store-credit ledger: issue/redeem/expire actions applied through a
    dispatch table. Not a refund; no refund symbols are reused here."""

    _DISPATCH = {
        "issue": "_issue",
        "redeem": "_redeem",
        "expire": "_expire",
    }

    def __init__(self):
        self.entries = []

    def process(self, actions):
        """Apply a queue of credit actions; loop + dispatch-table driven."""
        results = []
        for action in actions:  # loop-driven control shape
            op = action.get("op") if isinstance(action, dict) else None
            handler = (
                getattr(self, self._DISPATCH[op], None) if op in self._DISPATCH else None
            )
            if handler is None:
                continue
            results.append(handler(action))
        return results

    def _issue(self, action):
        self.entries.append({"op": "issue", "amount": action.get("amount", 0)})
        return {"ok": True, "op": "issue"}

    def _redeem(self, action):
        self.entries.append({"op": "redeem", "amount": action.get("amount", 0)})
        return {"ok": True, "op": "redeem"}

    def _expire(self, action):
        self.entries.append({"op": "expire", "amount": action.get("amount", 0)})
        return {"ok": True, "op": "expire"}


# Small non-refund process: store-credit lifecycle issue -> redeem -> expire.


class StoreCreditAccount:
    """Store-credit lifecycle: issue, then redeem, then expire."""

    def __init__(self, account_id):
        self.account_id = account_id
        self.balance = 0
        self.open = True

    def issue(self, amount):
        if not self.open or amount <= 0:
            return None
        self.balance += amount
        return {
            "account": self.account_id,
            "op": "issue",
            "amount": amount,
            "balance": self.balance,
        }

    def redeem(self, amount):
        if not self.open or amount <= 0 or amount > self.balance:
            return None
        self.balance -= amount
        return {
            "account": self.account_id,
            "op": "redeem",
            "amount": amount,
            "balance": self.balance,
        }

    def expire(self):
        if not self.open:
            return None
        balance = self.balance
        self.balance = 0
        self.open = False
        return {"account": self.account_id, "op": "expire", "amount": balance, "balance": 0}


# Same-name / different-meaning probe (shape 3).
# Name collides with repo-b's refund policy but is unrelated: a UI refund
# ticket *label* helper. Methods differ (ui_label / ticket_code); no
# applyWindow, no window logic. Excludable by repo + method shape.


class RefundPolicy:
    """Unrelated meaning: UI display policy for refund tickets (not repo-b's
    refund policy; no applyWindow, no window semantics)."""

    def ui_label(self):
        return "REFUND_TICKET_LABEL"

    def ticket_code(self):
        return "RT-000"


# Plausible-but-unsupported relation probe (shape 5).
# Looks like a cross-repo call to a credit gateway, but the method name is a
# variable resolved via getattr at runtime: nothing resolves statically, so
# the extractor must ABSTAIN and never infer an invokes relation.


class CreditGatewayClient:
    def call(self, target, method_name, payload):
        # Resembles cross-repo: CreditGateway.applyCredit(payload)
        # (dynamic dispatch via variable method name; statically unresolvable)
        method = getattr(target, method_name)
        return method(payload)
