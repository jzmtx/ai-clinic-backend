# api/tasks.py

from celery import shared_task
from .utils import send_sms_notification

@shared_task
def send_prescription_reminder_sms(to_number, message_body):
    """
    A Celery task to send a single SMS reminder.
    """
    print(f"Executing send_prescription_reminder_sms for number: {to_number}")
    send_sms_notification(to_number, message_body)
    return f"SMS reminder task for {to_number} has been executed."