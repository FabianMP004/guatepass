"""Step Functions timeout and scheduled reconciler share one invoice transaction."""
from pasos.facturar import facturar_paso

def vencer(event,context):
    return facturar_paso(event['paso_id'])
