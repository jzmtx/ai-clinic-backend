from django.contrib import admin
from .models import Doctor, Patient, Token, Consultation, Clinic, Receptionist

class PatientAdmin(admin.ModelAdmin):
    list_display = ('name', 'age', 'user', 'phone_number') 
    search_fields = ('name', 'user__username', 'phone_number')

class ClinicAdmin(admin.ModelAdmin):
    list_display = ('name', 'city', 'latitude', 'longitude')
    list_editable = ('latitude', 'longitude') 

admin.site.register(Doctor)
admin.site.register(Patient, PatientAdmin) 
admin.site.register(Token)
admin.site.register(Consultation)
admin.site.register(Clinic, ClinicAdmin) 
admin.site.register(Receptionist)

