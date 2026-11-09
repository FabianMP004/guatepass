from common.http import response

def handler(event,context):
    # API Gateway enforces x-api-key before invocation. Never acknowledge a pass
    # until the durable ingestion implementation exists (task 5).
    return response(event,501,message='Recepción durable de pasos pendiente de tarea 5.',code='NOT_IMPLEMENTED')
