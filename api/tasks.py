from django.utils import timezone
from .models import Token
from .utils import send_sms_notification

# This is your existing function for prescription reminders, we'll keep it for later.
def send_prescription_reminder_sms(to_number, message_body):
    """
    A simple function to send an SMS reminder.
    """
    print(f"Executing send_prescription_reminder_sms for number: {to_number}")
    send_sms_notification(to_number, message_body)
    return f"SMS reminder task for {to_number} has been executed."

# --- NEW FUNCTION FOR DAILY TOKEN REMINDERS ---
def send_daily_reminders():
    """
    Finds all active tokens for today and sends an SMS reminder.
    """
    today = timezone.now().date()
    # Get all of today's tokens that are still active
    tokens_for_today = Token.objects.filter(
        date=today,
        status__in=['waiting', 'confirmed']
    ).select_related('patient', 'doctor')

    print(f"Found {tokens_for_today.count()} tokens for today's reminders.")

    # Loop through and send the SMS
    for token in tokens_for_today:
        if token.patient and token.patient.phone_number:
            message = (
                f"Hi {token.patient.name}, a reminder for your token #{token.token_number} "
                f"with Dr. {token.doctor.name} today. To cancel, please reply with the number 9."
            )
            send_sms_notification(token.patient.phone_number, message)
            print(f"Sent reminder for token {token.token_number} to {token.patient.name}")
    
    return f"Sent {tokens_for_today.count()} reminders."