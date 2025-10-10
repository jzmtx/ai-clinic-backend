from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

class State(models.Model):
    name = models.CharField(max_length=100, unique=True)
    def __str__(self):
        return self.name

class District(models.Model):
    name = models.CharField(max_length=100)
    state = models.ForeignKey(State, on_delete=models.CASCADE, related_name='districts')
    def __str__(self):
        return f"{self.name}, {self.state.name}"

class Clinic(models.Model):
    name = models.CharField(max_length=150)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    district = models.ForeignKey(District, on_delete=models.SET_NULL, null=True, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    def __str__(self):
        return self.name

class Doctor(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, null=True, blank=True)
    name = models.CharField(max_length=100)
    specialization = models.CharField(max_length=100)
    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name='doctors', null=True, blank=True)

    def __str__(self):
        if self.clinic:
            return f"{self.name} ({self.clinic.name})"
        return self.name

class Receptionist(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name='receptionists')

    def __str__(self):
        return f"{self.user.username} at {self.clinic.name}"

class Patient(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, null=True, blank=True, related_name='patient')
    name = models.CharField(max_length=100)
    age = models.IntegerField()
    phone_number = models.CharField(max_length=15, null=True, blank=True)

    def __str__(self):
        return self.name

class Token(models.Model):
    STATUS_CHOICES = [
        ('waiting', 'Waiting'), ('confirmed', 'Confirmed'), ('in_consultancy', 'In Consultancy'),
        ('completed', 'Completed'), ('skipped', 'Skipped'), ('cancelled', 'Cancelled'),
    ]
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE)
    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE)
    
    # --- THIS LINE HAS BEEN CHANGED ---
    token_number = models.CharField(max_length=20, blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    date = models.DateField(default=timezone.now)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='waiting')
    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name='tokens', null=True, blank=True)
    appointment_time = models.TimeField(null=True, blank=True)
    distance_km = models.FloatField(null=True, blank=True) # Added from a previous step

    class Meta:
        unique_together = [
            ('token_number', 'clinic', 'date'),
            ('doctor', 'date', 'appointment_time'),
        ]

    def __str__(self):
        if self.appointment_time:
            return f"Appointment for {self.patient.name} at {self.appointment_time.strftime('%I:%M %p')} on {self.date}"
        return f"Token {self.token_number} for {self.patient.name} on {self.date}"

    def save(self, *args, **kwargs):
        if self.doctor and not self.clinic:
            self.clinic = self.doctor.clinic
        
        if not self.pk and not self.appointment_time and self.token_number is None:
            last_token = Token.objects.filter(clinic=self.clinic, date=self.date, appointment_time__isnull=True).order_by('-token_number').first()
            
            # This part needs to handle numeric conversion if the old format was just numbers
            last_token_num = 0
            if last_token and last_token.token_number and last_token.token_number.isdigit():
                last_token_num = int(last_token.token_number)
            self.token_number = str(last_token_num + 1)

        if self.status == 'completed' and self.completed_at is None:
            self.completed_at = timezone.now()
            
        super(Token, self).save(*args, **kwargs)


class Consultation(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE)
    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE)
    date = models.DateTimeField(auto_now_add=True)
    notes = models.TextField()

    def __str__(self):
        return f"Consultation for {self.patient.name} on {self.date.strftime('%Y-%m-%d')}"

class PrescriptionItem(models.Model):
    consultation = models.ForeignKey(Consultation, related_name='prescription_items', on_delete=models.CASCADE)
    medicine_name = models.CharField(max_length=200)
    dosage = models.CharField(max_length=100)
    duration_days = models.IntegerField()
    
    timing_morning = models.BooleanField(default=False)
    timing_afternoon = models.BooleanField(default=False)
    timing_evening = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.medicine_name} for Consultation {self.consultation.id}"
