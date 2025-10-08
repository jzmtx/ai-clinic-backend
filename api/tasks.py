from .utils import send_sms_notification

def send_prescription_reminder_sms(to_number, message_body):
    """
    A simple function to send an SMS reminder.
    """
    print(f"Executing send_prescription_reminder_sms for number: {to_number}")
    send_sms_notification(to_number, message_body)
    return f"SMS reminder task for {to_number} has been executed."