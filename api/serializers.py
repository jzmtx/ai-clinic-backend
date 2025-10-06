from rest_framework import serializers
from django.contrib.auth.models import User
from .models import Doctor, Patient, Token, Consultation, Clinic, Receptionist, PrescriptionItem

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
    # Correctly include 'id' and 'doctor' id for frontend use
    doctor_id = serializers.ReadOnlyField(source='doctor.id')
    clinic_id = serializers.ReadOnlyField(source='clinic.id')
    
    class Meta:
        model = Token
        fields = ['id', 'token_number', 'patient', 'doctor', 'doctor_id', 'created_at', 'status', 'clinic', 'clinic_id']


# --- THIS IS THE NEW, INTELLIGENT SERIALIZER ---
class PrescriptionItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = PrescriptionItem
        # We list all the fields from our new model
        fields = ['id', 'medicine_name', 'dosage', 'duration_days', 'timing_morning', 'timing_afternoon', 'timing_evening']

# --- THIS SERIALIZER IS NOW UPGRADED ---
class ConsultationSerializer(serializers.ModelSerializer):
    doctor = DoctorSerializer(read_only=True)
    # This line tells the API to include all the detailed prescription items
    # when showing a consultation's history.
    prescription_items = PrescriptionItemSerializer(many=True, read_only=True)

    class Meta:
        model = Consultation
        # The old 'prescription' field is replaced with the new 'prescription_items'
        fields = ['id', 'date', 'notes', 'doctor', 'prescription_items']


# --- Special Purpose Serializers ---

class PatientRegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=True)
    password2 = serializers.CharField(write_only=True, required=True)
    name = serializers.CharField(write_only=True, required=True)
    age = serializers.IntegerField(write_only=True, required=True)
    phone_number = serializers.CharField(write_only=True, required=True)
    class Meta:
        model = User
        fields = ('username', 'password', 'password2', 'name', 'age', 'phone_number')

    def validate(self, attrs):
        if attrs['password'] != attrs['password2']:
            raise serializers.ValidationError({"password": "Password fields didn't match."})
        return attrs

    def create(self, validated_data):
        user = User.objects.create(username=validated_data['username'])
        user.set_password(validated_data['password'])
        user.save()
        Patient.objects.create(
            user=user, name=validated_data['name'], age=validated_data['age'],
            phone_number=validated_data['phone_number']
        )
        return user

class ClinicWithDoctorsSerializer(serializers.ModelSerializer):
    doctors = DoctorSerializer(many=True, read_only=True)
    class Meta:
        model = Clinic
        fields = ['id', 'name', 'address', 'city', 'doctors']

# --- NEW SERIALIZER FOR THE PATIENT'S ANONYMIZED LIVE QUEUE ---
class AnonymizedTokenSerializer(serializers.ModelSerializer):
    """
    A serializer for the Token model that only exposes non-sensitive data
    for the public patient queue.
    """
    class Meta:
        model = Token
        fields = ['id', 'token_number', 'status']