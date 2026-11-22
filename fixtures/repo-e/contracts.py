# Store-credit contracts (repo-e) - held-out fixture (M3 A2).
# Synthetic client/server function pairs + event schema + shared-table
# constants, intra-repo-e only (parallel to repo-d contracts).


class StoreCreditClient:
    """Client stub for StoreCredit service calls."""

    def get_balance(self, account_id):
        # Stub implementation - returns synthetic balance
        return {"account_id": account_id, "balance": 5000}


class StoreCreditServer:
    """Server stub for store-credit operations."""

    def credit_store_credit(self, account_id, amount):
        # Stub implementation - credits store credit to account
        return {"account_id": account_id, "operation": "credit", "amount": amount}


# Event schema definition for store-credit events
EVENT_SCHEMA = {
    "store_credit_event": {
        "fields": [
            {"name": "account_id", "type": "string"},
            {"name": "transaction_type", "type": "enum", "values": ["issue", "redeem", "expire"]},
            {"name": "amount", "type": "integer"},
            {"name": "reason", "type": "string", "optional": True},
        ]
    }
}


# Shared table name constants (used by both client and server)
SHARED_TABLES = {
    "account_credit": "account_store_credit",
    "credit_ledger": "store_credit_ledger",
    "credit_history": "store_credit_history",
}


# Synthetic client/server call pair (intra-repo-e only)
store_credit_client = StoreCreditClient()
store_credit_server = StoreCreditServer()


def sync_store_credit(account_id):
    """Synthetic sync operation between client and server."""
    balance = store_credit_client.get_balance(account_id)
    if balance["balance"] > 0:
        return store_credit_server.credit_store_credit(account_id, balance["balance"])
    return None
