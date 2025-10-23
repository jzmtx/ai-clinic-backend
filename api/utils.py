# api/utils.py

from django.conf import settings
# REMOVED: from twilio.rest import Client
import logging

logger = logging.getLogger(__name__)

def send_sms_notification(to_number, message):
    """
    Simulates sending an SMS notification by printing it to the console.
    This replaces Twilio to make the app free to use.
    """
    # NOTE: The logic here is simplified to ensure it never crashes the app
    # and only logs the message to the console for monitoring.
    print("=====================================================")
    print(f"SMS SIMULATION: To: {to_number}")
    print(f"Message: {message}")
    print("=====================================================")
    
    # Return success status for the calling functions (reminders, IVR, etc.)
    return True 


### **Step 1.2: Update `settings.py` for Render**

