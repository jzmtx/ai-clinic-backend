# api/utils.py

from django.conf import settings
from twilio.rest import Client

def send_sms_notification(to_number, message_body):
    """
    Sends an SMS notification using Twilio.
    """
    if not to_number:
        print("--- SMS NOT SENT: No 'to_number' provided. ---")
        return False

    # Ensure the phone number is in E.164 format for Twilio
    if not to_number.startswith('+'):
        to_number = f"+91{to_number}"

    try:
        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        message = client.messages.create(
            body=message_body,
            from_=settings.TWILIO_PHONE_NUMBER,
            to=to_number
        )
        print(f"SMS sent successfully to {to_number}, SID: {message.sid}")
        return True
    except Exception as e:
        print(f"--- FAILED TO SEND SMS to {to_number}: {e} ---")
        return False