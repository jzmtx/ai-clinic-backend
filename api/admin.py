from django.contrib import admin
from .models import Clinic, Doctor, Patient, Token, Consultation, Receptionist, State, District, PrescriptionItem

# Import the models and admin classes from django-q
from django_q.models import Schedule
from django_q.admin import ScheduleAdmin as DjangoQ_ScheduleAdmin

# Unregister the default Schedule admin provided by django-q
# This is necessary so we can register our custom version
if admin.site.is_registered(Schedule):
    admin.site.unregister(Schedule)

# Define our own custom admin class for the Schedule model
@admin.register(Schedule)
class CustomScheduleAdmin(DjangoQ_ScheduleAdmin):
    """
    This custom admin class extends the default django-q ScheduleAdmin
    to ensure that custom actions, like 'run_selected', are available.
    """
    # By defining the actions here, we ensure they appear in the dropdown
    actions = [
        'run_selected', 
        'delete_selected'
    ]

# --- Your existing model registrations ---
# (It's good practice to keep them here as well)
admin.site.register(Clinic)
admin.site.register(Doctor)
admin.site.register(Patient)
admin.site.register(Token)
admin.site.register(Consultation)
admin.site.register(Receptionist)
admin.site.register(State)
admin.site.register(District)
admin.site.register(PrescriptionItem)

