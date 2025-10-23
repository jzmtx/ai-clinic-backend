from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.db.models import Count, Avg, F
# --- REMOVED: models import ---
from datetime import timedelta
# --- REMOVED: transaction import ---
# from django.db import transaction # No longer needed here
from .models import Doctor, Patient, Token, Consultation, Clinic, Receptionist, PrescriptionItem

User = get_user_model()

# --- Base Serializers ---

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username']

class ClinicSerializer(serializers.ModelSerializer):
    class Meta:
        model = Clinic
        fields = ['id', 'name', 'address', 'city']

class DoctorSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    class Meta:
        model = Doctor
        fields = ['id', 'name', 'specialization', 'user']

class PatientSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    class Meta:
        model = Patient
        fields = ['id', 'name', 'age', 'user', 'phone_number']

class TokenSerializer(serializers.ModelSerializer):
    patient = PatientSerializer(read_only=True)
    doctor = serializers.StringRelatedField(read_only=True)
    clinic = serializers.StringRelatedField(read_only=True)
    doctor_id = serializers.ReadOnlyField(source='doctor.id')
    clinic_id = serializers.ReadOnlyField(source='clinic.id')
    
    class Meta:
        model = Token
        token_number = serializers.CharField(read_only=True) 
        fields = ['id', 'token_number', 'patient', 'doctor', 'doctor_id', 'created_at', 'status', 'clinic', 'clinic_id', 'appointment_time']


class PrescriptionItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = PrescriptionItem
        fields = ['id', 'medicine_name', 'dosage', 'duration_days', 'timing_morning', 'timing_afternoon', 'timing_evening']

class ConsultationSerializer(serializers.ModelSerializer):
    doctor = DoctorSerializer(read_only=True)
    prescription_items = PrescriptionItemSerializer(many=True, read_only=True)

    class Meta:
        model = Consultation
        fields = ['id', 'date', 'notes', 'doctor', 'prescription_items']


# --- Special Purpose Serializers ---

# --- REVERTED: PatientRegisterSerializer (Simple, Active User) ---
class PatientRegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=True, style={'input_type': 'password'})
    password2 = serializers.CharField(write_only=True, required=True, style={'input_type': 'password'})
    name = serializers.CharField(write_only=True, required=True)
    age = serializers.IntegerField(write_only=True, required=True)
    phone_number = serializers.CharField(write_only=True, required=True)

    class Meta:
        model = User
        fields = ('username', 'password', 'password2', 'name', 'age', 'phone_number')
        extra_kwargs = {
            'username': {'required': True},
            # Basic phone validation moved here for simplicity, can be more robust
             'phone_number': {'validators': []}, 
        }

    def validate_phone_number(self, value):
        """ Basic check for format and uniqueness """
        if Patient.objects.filter(phone_number=value, user__is_active=True).exists():
            raise serializers.ValidationError("This phone number is already registered.")
        if not value.startswith('+'):
             raise serializers.ValidationError("Phone number must start with '+' and include country code.")
        return value

    def validate_username(self, value):
        """ Check if username is taken """
        if User.objects.filter(username=value, is_active=True).exists():
             raise serializers.ValidationError("This username is already taken.")
        # Allow reusing username if user is inactive (optional, adjust if needed)
        # if User.objects.filter(username=value).exists():
        #    raise serializers.ValidationError("This username is already taken.")
        return value

    def validate(self, attrs):
        if attrs['password'] != attrs['password2']:
            raise serializers.ValidationError({"password": "Password fields didn't match."})
        attrs.pop('password2')
        return attrs

    # Reverted create method - uses create_user which makes user active by default
    def create(self, validated_data):
        user = User.objects.create_user(
            username=validated_data['username'],
            password=validated_data['password']
            # is_active=True is the default for create_user
        )
        Patient.objects.create(
            user=user,
            name=validated_data['name'],
            age=validated_data['age'],
            phone_number=validated_data['phone_number']
        )
        print(f"Created new ACTIVE user: {user.username}") # Log change
        return user


class ClinicWithDoctorsSerializer(serializers.ModelSerializer):
    doctors = DoctorSerializer(many=True, read_only=True)
    average_wait_time = serializers.SerializerMethodField()
    total_tokens = serializers.SerializerMethodField()

    class Meta:
        model = Clinic
        fields = ['id', 'name', 'address', 'city', 'doctors', 'average_wait_time', 'total_tokens']

    def get_total_tokens(self, obj):
        today = timezone.now().date()
        return Token.objects.filter(clinic=obj, date=today).count()

    def get_average_wait_time(self, obj):
        today = timezone.now().date()
        completed_tokens = Token.objects.filter(clinic=obj, date=today, status='completed', completed_at__isnull=False)
        if not completed_tokens.exists(): return 0
        avg_wait_data = completed_tokens.aggregate(avg_duration=Avg(F('completed_at') - F('created_at')))
        if avg_wait_data['avg_duration']:
            return round(avg_wait_data['avg_duration'].total_seconds() / 60)
        return 0


class AnonymizedTokenSerializer(serializers.ModelSerializer):
    token_number = serializers.CharField(read_only=True) 
    class Meta:
        model = Token
        fields = ['id', 'token_number', 'status', 'appointment_time']

