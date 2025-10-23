from django.utils import timezone
from datetime import timedelta, datetime
from .models import Token, Patient # Make sure Token and Patient are imported
from .utils import send_sms_notification
# --- NEW: Import async_task ---
from django_q.tasks import async_task
import logging

logger = logging.getLogger(__name__)

# --- Function for Daily Morning Reminders ---
def send_daily_appointment_reminders():
    """
    Sends SMS reminders for all appointments scheduled for today.
    """
    today = timezone.now().date()
    todays_tokens = Token.objects.filter(
        date=today,
        status__in=['waiting', 'confirmed']
    ).select_related('patient', 'doctor', 'clinic')

    count = todays_tokens.count()
    logger.info(f"Found {count} active appointments for {today}. Sending reminders...")
    print(f"Found {count} active appointments for {today}. Sending reminders...")

    if count == 0:
        return f"No appointments found for {today}."

    success_count = 0
    failure_count = 0

    for token in todays_tokens:
        patient = token.patient
        doctor = token.doctor
        clinic = token.clinic
        
        if patient.phone_number:
            time_str = token.appointment_time.strftime('%I:%M %p') if token.appointment_time else "your scheduled time"
            message = (
                f"Hi {patient.name}, this is a reminder for your appointment at {clinic.name} "
                f"with Dr. {doctor.name} today around {time_str}. "
            )
            if token.token_number:
                 message += f"Your token is {token.token_number}. "
            
            message += "Please arrive on time."

            try:
                send_sms_notification(patient.phone_number, message)
                logger.info(f"  -> Sent reminder to {patient.name} for Dr. {doctor.name}")
                print(f"  -> Sent reminder to {patient.name} for Dr. {doctor.name}")
                success_count += 1
            except Exception as e:
                logger.error(f"  -> FAILED to send reminder to {patient.name} ({patient.phone_number}): {e}")
                print(f"  -> FAILED to send reminder to {patient.name} ({patient.phone_number}): {e}")
                failure_count += 1
        else:
            logger.warning(f"  -> SKIPPED reminder for {patient.name} - No phone number.")
            print(f"  -> SKIPPED reminder for {patient.name} - No phone number.")
            failure_count += 1

    result_message = f"Finished sending reminders for {today}. Success: {success_count}, Failed/Skipped: {failure_count}."
    logger.info(result_message)
    print(result_message)
    return result_message

# --- Function for Prescription Reminders ---
def send_prescription_reminder_sms(phone_number, message, **kwargs):
    """
    Sends a single prescription dosage reminder SMS.
    Accepts **kwargs to ignore extra arguments like 'schedule'.
    """
    try:
        send_sms_notification(phone_number, message)
        logger.info(f"Sent prescription reminder to {phone_number}")
        print(f"Sent prescription reminder to {phone_number}")
    except Exception as e:
        logger.error(f"Failed to send prescription reminder to {phone_number}: {e}")
        print(f"Failed to send prescription reminder to {phone_number}: {e}")

# --- MODIFIED: Function to automatically CANCEL missed appointments ---
def check_and_cancel_missed_slots():
    """
    Checks for appointments that are past their time + grace period 
    and still in 'waiting' status, then updates them to 'cancelled'.
    """
    now = timezone.now()
    today = now.date()
    grace_period = timedelta(minutes=15) # Using 5 minutes for testing

    missed_tokens = Token.objects.filter(
        date=today,
        appointment_time__isnull=False,
        status='waiting' # Only check for tokens still marked as waiting
    )

    cancelled_count = 0
    for token in missed_tokens:
        appointment_datetime = datetime.combine(today, token.appointment_time)
        appointment_datetime_aware = timezone.make_aware(appointment_datetime, timezone.get_current_timezone())
        
        cutoff_time = appointment_datetime_aware + grace_period

        if now > cutoff_time:
            # --- CHANGE: Set status to 'cancelled' instead of 'skipped' ---
            token.status = 'cancelled' 
            token.save()
            cancelled_count += 1
            # --- CHANGE: Update log message ---
            logger.info(f"Cancelled token {token.id} for patient {token.patient.name} scheduled at {token.appointment_time.strftime('%I:%M %p')} due to no-show.")
            print(f"Cancelled token {token.id} for patient {token.patient.name} scheduled at {token.appointment_time.strftime('%I:%M %p')} due to no-show.")

            # Optional: Send an SMS notification to the patient
            if token.patient.phone_number:
                # --- CHANGE: Update SMS message ---
                message = (f"Hi {token.patient.name}, we noticed you missed your appointment slot "
                           f"at {token.appointment_time.strftime('%I:%M %p')} with Dr. {token.doctor.name}. "
                           f"Your appointment has been automatically cancelled. Please feel free to book again or contact the clinic.")
                try:
                    # --- CHANGE: Call the renamed helper task ---
                    async_task('api.tasks.send_cancelled_notification_sms', token.patient.phone_number, message)
                except Exception as e:
                     logger.error(f"Failed to schedule cancellation notification SMS for token {token.id}: {e}")
                     print(f"Failed to schedule cancellation notification SMS for token {token.id}: {e}")

    # --- CHANGE: Update result message ---
    result_message = f"Checked for missed slots. Cancelled {cancelled_count} tokens automatically."
    logger.info(result_message)
    print(result_message)
    return result_message

# --- RENAMED & UPDATED: Helper task to send the cancelled notification ---
def send_cancelled_notification_sms(phone_number, message):
    """ Sends the SMS notification that an appointment was cancelled due to no-show. """
    try:
        send_sms_notification(phone_number, message)
        logger.info(f"Sent auto-cancellation notification to {phone_number}")
        print(f"Sent auto-cancellation notification to {phone_number}")
    except Exception as e:
        logger.error(f"Failed to send auto-cancellation notification to {phone_number}: {e}")
        print(f"Failed to send auto-cancellation notification to {phone_number}: {e}")

