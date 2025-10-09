from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

class Clinic(models.Model):
    name = models.CharField(max_length=100)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    def __str__(self):
        return self.name

class Doctor(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, null=True, blank=True)
    name = models.CharField(max_length=100)
    specialization = models.CharField(max_length=100)
    clinic = models.ForeignKey(Clinic, related_name='doctors', on_delete=models.SET_NULL, null=True, blank=True)
    role = models.CharField(max_length=20, default='doctor')

    def __str__(self):
        return f"Dr. {self.name}"

class Receptionist(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    clinic = models.ForeignKey(Clinic, on_delete=models.SET_NULL, null=True, blank=True)
    role = models.CharField(max_length=20, default='receptionist')

    def __str__(self):
        return self.user.username

class Patient(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, null=True, blank=True)
    name = models.CharField(max_length=100)
    age = models.IntegerField()
    phone_number = models.CharField(max_length=15, unique=True, null=True, blank=True)
    
    # --- NEW FIELDS FOR OTP VERIFICATION ---
    is_phone_verified = models.BooleanField(default=False)
    otp = models.CharField(max_length=6, null=True, blank=True)
    otp_expiry = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.name

class Token(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE)
    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE)
    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, null=True, blank=True)
    token_number = models.IntegerField(null=True, blank=True)
    date = models.DateField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    appointment_time = models.TimeField(null=True, blank=True)
    status_choices = [
        ('waiting', 'Waiting'),
        ('confirmed', 'Confirmed'),
        ('in_consultancy', 'In Consultancy'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
        ('skipped', 'Skipped'),
    ]
    status = models.CharField(max_length=20, choices=status_choices, default='waiting')
    distance_km = models.FloatField(null=True, blank=True)

    class Meta:
        unique_together = ('token_number', 'clinic', 'date')

    def save(self, *args, **kwargs):
        if not self.clinic and self.doctor:
            self.clinic = self.doctor.clinic
            
        if self.token_number is None:
            last_token = Token.objects.filter(
                clinic=self.clinic,
                date=timezone.now().date(),
                token_number__isnull=False
            ).order_by('-token_number').first()
            
            if last_token and last_token.token_number is not None:
                self.token_number = last_token.token_number + 1
            else:
                self.token_number = 1
        
        super(Token, self).save(*args, **kwargs)

    def __str__(self):
        return f"Token {self.token_number} for {self.patient.name}"

class Consultation(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE)
    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE)
    date = models.DateTimeField(auto_now_add=True)
    notes = models.TextField()

    def __str__(self):
        return f"Consultation for {self.patient.name} on {self.date.date()}"

class PrescriptionItem(models.Model):
    consultation = models.ForeignKey(Consultation, related_name='prescription_items', on_delete=models.CASCADE)
    medicine_name = models.CharField(max_length=100)
    dosage = models.CharField(max_length=100)
    duration_days = models.IntegerField()
    timing_morning = models.BooleanField(default=False)
    timing_afternoon = models.BooleanField(default=False)
    timing_evening = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.medicine_name} for {self.consultation.patient.name}"