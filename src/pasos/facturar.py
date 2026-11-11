"""Invoice adapter; all financial transaction writes live in common.money."""
from common.money import invoice_expired_pending

def facturar_paso(paso_id: str) -> str:
    return invoice_expired_pending(paso_id)
