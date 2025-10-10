from django.utils import timezone
from datetime import date
from .models import Token
from .utils import send_sms_notification
import logging

# Set up a logger to see output in your qcluster terminal
logger = logging.getLogger(__name__)

def send_daily_appointment_reminders():
    """
    Finds all appointments for today and sends an SMS reminder to each patient.
    This task is meant to be run once every morning.
    """
    today = timezone.now().date()
    # Find all tokens for today that are still active
    todays_tokens = Token.objects.filter(
        date=today,
        status__in=['waiting', 'confirmed']
    ).select_related('patient', 'doctor')

    if not todays_tokens.exists():
        logger.info(f"No active appointments found for {today}. No reminders sent.")
        return

    logger.info(f"Found {todays_tokens.count()} active appointments for {today}. Sending reminders...")
    
    for token in todays_tokens:
        patient = token.patient
        if patient.phone_number:
            time_str = token.appointment_time.strftime('%I:%M %p') if token.appointment_time else 'today'
            message = (
                f"Hi {patient.name}, this is a reminder for your appointment with "
                f"Dr. {token.doctor.name} at {time_str}. "
                f"Your token is #{token.token_number}."
            )
            try:
                send_sms_notification(patient.phone_number, message)
                logger.info(f"  -> Sent reminder to {patient.name} for Dr. {token.doctor.name}")
            except Exception as e:
                logger.error(f"  -> FAILED to send reminder to {patient.name}. Error: {e}")

    logger.info("Finished sending all daily reminders.")


# --- THIS FUNCTION SIGNATURE HAS BEEN MODIFIED ---
def send_prescription_reminder_sms(phone_number, message, **kwargs):
    """
    A simple task that sends a prescription reminder SMS.
    This task is scheduled by the ConsultationCreateView.
    The **kwargs is added to accept any extra arguments from the task broker.
    """
    try:
        logger.info(f"Sending prescription reminder to {phone_number}: '{message}'")
        send_sms_notification(phone_number, message)
        logger.info("  -> Prescription reminder sent successfully.")
    except Exception as e:
        logger.error(f"  -> FAILED to send prescription reminder to {phone_number}. Error: {e}")

