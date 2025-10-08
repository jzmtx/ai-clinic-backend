from django.shortcuts import render
from rest_framework import generics, status, permissions
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.authtoken.models import Token as AuthToken
from rest_framework.views import APIView
from .models import Token, Doctor, Patient, Consultation, Receptionist, Clinic, PrescriptionItem
from .serializers import (
    TokenSerializer,
    DoctorSerializer,
    ConsultationSerializer,
    PatientRegisterSerializer,
    ClinicWithDoctorsSerializer,
    PatientSerializer,
    AnonymizedTokenSerializer
)
from django.db.models import Count, Avg, F, Q
from django.utils import timezone
from math import radians, sin, cos, sqrt, atan2
from django.contrib.auth import authenticate
from django.views.decorators.csrf import csrf_exempt
from twilio.twiml.voice_response import VoiceResponse
from django.http import HttpResponse
from django.db import transaction

# --- Core App Imports ---
from .utils import send_sms_notification
# --- Imports for Django-Q Scheduling ---
from django_q.tasks import async_task
from datetime import datetime, timedelta, time

# --- Helper Functions ---
def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371.0
    lat1_rad, lon1_rad, lat2_rad, lon2_rad = map(radians, [lat1, lon1, lat2, lon2])
    dlon, dlat = lon2_rad - lon1_rad, lat2_rad - lat1_rad
    a = sin(dlat / 2)**2 + cos(lat1_rad) * cos(lat2_rad) * sin(dlon / 2)**2
    return R * (2 * atan2(sqrt(a), sqrt(1 - a)))

# --- Public & Private Views ---
class PublicClinicListView(generics.ListAPIView):
    queryset = Clinic.objects.prefetch_related('doctors').all()
    serializer_class = ClinicWithDoctorsSerializer
    permission_classes = [permissions.AllowAny] # Explicitly public

class ClinicAnalyticsView(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request, *args, **kwargs):
        user = request.user
        clinic = None
        if hasattr(user, 'doctor'): clinic = user.doctor.clinic
        elif hasattr(user, 'receptionist'): clinic = user.receptionist.clinic
        if not clinic: return Response({'error': 'User is not associated with a clinic.'}, status=status.HTTP_403_FORBIDDEN)
        
        today = timezone.now().date()
        todays_tokens = Token.objects.filter(clinic=clinic, date=today)
        completed_tokens = todays_tokens.filter(status='completed', completed_at__isnull=False)
        avg_wait_data = completed_tokens.aggregate(avg_duration=Avg(F('completed_at') - F('created_at')))
        avg_wait_minutes = round(avg_wait_data['avg_duration'].total_seconds() / 60, 1) if avg_wait_data['avg_duration'] else 0
        
        stats = {
            'clinic_name': clinic.name, 'date': today.strftime("%B %d, %Y"),
            'total_patients': todays_tokens.count(),
            'average_wait_time_minutes': avg_wait_minutes,
            'doctor_workload': list(todays_tokens.values('doctor__name').annotate(count=Count('id')).order_by('-count')),
            'patient_status_breakdown': { 
                'waiting': todays_tokens.filter(status='waiting').count(), 
                'confirmed': todays_tokens.filter(status='confirmed').count(), 
                'completed': completed_tokens.count() 
            }
        }
        return Response(stats, status=status.HTTP_200_OK)

class PatientRegisterView(generics.CreateAPIView):
    serializer_class = PatientRegisterSerializer
    permission_classes = [permissions.AllowAny] # Explicitly public
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            token, _ = AuthToken.objects.get_or_create(user=user)
            patient = user.patient
            if patient.phone_number:
                message = f"Welcome to the Clinic Portal, {patient.name}! Your registration was successful."
                send_sms_notification(patient.phone_number, message)
            
            user_data = { 'username': user.username, 'name': patient.name, 'age': patient.age, 'role': 'patient', 'phone_number': patient.phone_number }
            return Response({"message": "Patient registered successfully.", "token": token.key, "user": user_data}, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class ConfirmArrivalView(APIView):
    permission_classes = [IsAuthenticated]
    def post(self, request, *args, **kwargs):
        user = request.user
        user_lat, user_lon = request.data.get('latitude'), request.data.get('longitude')
        if not all([user_lat, user_lon]): return Response({'error': 'Latitude and longitude are required.'}, status=status.HTTP_400_BAD_REQUEST)
        if not hasattr(user, 'patient'): return Response({'error': 'No patient profile found.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            token = Token.objects.filter(patient=user.patient, date=timezone.now().date(), status='waiting').latest('created_at')
            clinic = token.clinic
            if not all([clinic.latitude, clinic.longitude]): return Response({'error': 'Clinic location not configured.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            distance = haversine_distance(float(user_lat), float(user_lon), clinic.latitude, clinic.longitude)
            if distance > 1.0:
                return Response({'error': f'You are approximately {distance:.2f} km away. You must be within 1.0 km of the clinic to confirm.'}, status=status.HTTP_400_BAD_REQUEST)
            token.status = 'confirmed'
            token.save()
            return Response({"message": "Arrival confirmed successfully.", "token": TokenSerializer(token).data}, status=status.HTTP_200_OK)
        except Token.DoesNotExist: return Response({'error': 'No active token found to confirm.'}, status=status.HTTP_404_NOT_FOUND)

class PatientCancelTokenView(APIView):
    permission_classes = [IsAuthenticated]
    def post(self, request, *args, **kwargs):
        user = request.user
        if not hasattr(user, 'patient'): return Response({'error': 'No patient profile found.'}, status=status.HTTP_400_BAD_REQUEST)
        today = timezone.now().date()
        try:
            token = Token.objects.filter(patient=user.patient, date=today, status__in=['waiting', 'confirmed']).latest('created_at')
            token.status = 'cancelled'
            token.save()
            return Response({'message': 'Your token has been successfully cancelled.'}, status=status.HTTP_200_OK)
        except Token.DoesNotExist: return Response({'error': 'You do not have an active token to cancel.'}, status=status.HTTP_404_NOT_FOUND)

class GetPatientTokenView(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request, *args, **kwargs):
        user = request.user
        if not hasattr(user, 'patient'): return Response({'error': 'No patient profile found.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            token = Token.objects.filter(patient=user.patient, date=timezone.now().date()).exclude(status__in=['completed', 'cancelled']).latest('created_at')
            return Response(TokenSerializer(token).data)
        except Token.DoesNotExist: return Response({'error': 'No active token found for today.'}, status=status.HTTP_404_NOT_FOUND)

class ClinicWithDoctorsListView(generics.ListAPIView):
    queryset = Clinic.objects.prefetch_related('doctors').all()
    serializer_class = ClinicWithDoctorsSerializer
    permission_classes = [IsAuthenticated]

class PatientCreateTokenView(APIView):
    permission_classes = [IsAuthenticated]
    def post(self, request, *args, **kwargs):
        user, doctor_id = request.user, request.data.get('doctor_id')
        if not hasattr(user, 'patient'): return Response({'error': 'Only patients can create tokens.'}, status=status.HTTP_403_FORBIDDEN)
        if not doctor_id: return Response({'error': 'Doctor ID is required.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            doctor = Doctor.objects.get(id=doctor_id)
            if Token.objects.filter(patient=user.patient, date=timezone.now().date()).exclude(status__in=['completed', 'cancelled']).exists():
                return Response({'error': 'You already have an active token for today.'}, status=status.HTTP_400_BAD_REQUEST)
            new_token = Token.objects.create(patient=user.patient, doctor=doctor)
            if user.patient.phone_number:
                message = f"Dear {user.patient.name}, your token {new_token.token_number} for Dr. {doctor.name} at {doctor.clinic.name} has been confirmed."
                send_sms_notification(user.patient.phone_number, message)
            return Response(TokenSerializer(new_token).data, status=status.HTTP_201_CREATED)
        except Doctor.DoesNotExist: return Response({'error': 'Doctor not found.'}, status=status.HTTP_404_NOT_FOUND)

class TokenListCreate(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = TokenSerializer
    def get_queryset(self):
        user = self.request.user
        clinic = None
        if hasattr(user, 'doctor'): clinic = user.doctor.clinic
        elif hasattr(user, 'receptionist'): clinic = user.receptionist.clinic
        if clinic:
            return Token.objects.filter(clinic=clinic, date=timezone.now().date(), status__in=['waiting', 'confirmed']).order_by('created_at')
        return Token.objects.none()

    def post(self, request, *args, **kwargs):
        patient_name, patient_age, phone_number, doctor_id = request.data.get('patient_name'), request.data.get('patient_age'), request.data.get('phone_number'), request.data.get('assigned_doctor')
        if not all([patient_name, patient_age, phone_number, doctor_id]): return Response({'error': 'Missing required fields'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            doctor = Doctor.objects.get(id=doctor_id)
            patient, _ = Patient.objects.get_or_create(phone_number=phone_number, defaults={'name': patient_name, 'age': patient_age})
            new_token = Token.objects.create(patient=patient, doctor=doctor)
            message = f"Dear {patient.name}, your token {new_token.token_number} for Dr. {doctor.name} at {doctor.clinic.name} has been confirmed."
            send_sms_notification(patient.phone_number, message)
            return Response(TokenSerializer(new_token).data, status=status.HTTP_201_CREATED)
        except Doctor.DoesNotExist: return Response({'error': 'Doctor not found'}, status=status.HTTP_404_NOT_FOUND)

class DoctorList(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = DoctorSerializer
    def get_queryset(self):
        user = self.request.user
        clinic = None
        if hasattr(user, 'doctor'): clinic = user.doctor.clinic
        elif hasattr(user, 'receptionist'): clinic = user.receptionist.clinic
        if clinic:
            return Doctor.objects.filter(clinic=clinic)
        return Doctor.objects.none()

class LoginView(APIView):
    permission_classes = [permissions.AllowAny] # <-- THE FIX IS HERE
    def post(self, request, format=None):
        username, password = request.data.get('username'), request.data.get('password')
        user = authenticate(request, username=username, password=password)
        if user:
            token, _ = AuthToken.objects.get_or_create(user=user)
            user_data = {'token': token.key}
            role, profile_data = None, None
            
            if hasattr(user, 'doctor'): 
                role, profile_data = 'doctor', DoctorSerializer(user.doctor).data
            elif hasattr(user, 'receptionist'):
                role = 'receptionist'
                clinic = user.receptionist.clinic
                profile_data = {'username': user.username, 'clinic': {'id': clinic.id, 'name': clinic.name} if clinic else None}
            elif hasattr(user, 'patient'): 
                role = 'patient'
                patient = user.patient
                profile_data = PatientSerializer(patient).data
            
            if role: 
                user_data['user'] = {**profile_data, 'role': role}
                return Response(user_data)
            
            return Response({'error': 'User profile not found'}, status=status.HTTP_404_NOT_FOUND)
        return Response({'error': 'Invalid Credentials'}, status=status.HTTP_400_BAD_REQUEST)

class MyHistoryView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ConsultationSerializer
    def get_queryset(self):
        user = self.request.user
        if hasattr(user, 'patient'):
            return Consultation.objects.filter(patient=user.patient).order_by('-date')
        return Consultation.objects.none()

class PatientHistoryView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ConsultationSerializer
    def get_queryset(self): 
        return Consultation.objects.filter(patient__id=self.kwargs['patient_id']).order_by('-date')

class PatientLiveQueueView(generics.ListAPIView):
    serializer_class = AnonymizedTokenSerializer
    permission_classes = [permissions.IsAuthenticated]
    def get_queryset(self):
        doctor_id = self.kwargs['doctor_id']
        today = timezone.now().date()
        active_statuses = ['waiting', 'confirmed', 'in_consultancy']
        return Token.objects.filter(
            doctor_id=doctor_id,
            date=today,
            status__in=active_statuses
        ).order_by('token_number')

class ConsultationCreateView(APIView):
    permission_classes = [IsAuthenticated]
    def post(self, request, *args, **kwargs):
        data = request.data
        patient_id = data.get('patient')
        notes = data.get('notes')
        prescription_items_data = data.get('prescription_items', [])

        if not patient_id or not notes:
            return Response({'error': 'Patient and notes are required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            patient = Patient.objects.get(id=patient_id)
            doctor = request.user.doctor

            new_prescription_items = []
            with transaction.atomic():
                consultation = Consultation.objects.create(patient=patient, doctor=doctor, notes=notes)
                
                for item_data in prescription_items_data:
                    item = PrescriptionItem.objects.create(consultation=consultation, **item_data)
                    new_prescription_items.append(item)
                
                try:
                    token = Token.objects.filter(patient=patient, date=timezone.now().date(), status__in=['waiting', 'confirmed', 'in_consultancy']).latest('created_at')
                    if token:
                        token.status = 'completed'
                        token.save()
                except Token.DoesNotExist:
                    pass

                if patient.phone_number and new_prescription_items:
                    MORNING_DOSE_TIME = time(8, 0)
                    AFTERNOON_DOSE_TIME = time(13, 0)
                    EVENING_DOSE_TIME = time(20, 0)
                    
                    today = timezone.now().date()

                    for item in new_prescription_items:
                        for day in range(1, int(item.duration_days) + 1):
                            reminder_date = today + timedelta(days=day)
                            
                            if item.timing_morning:
                                schedule_datetime = datetime.combine(reminder_date, MORNING_DOSE_TIME)
                                message = f"Hi {patient.name}, it's time for your morning dose of {item.medicine_name}."
                                async_task('api.tasks.send_prescription_reminder_sms', patient.phone_number, message, schedule=schedule_datetime)

                            if item.timing_afternoon:
                                schedule_datetime = datetime.combine(reminder_date, AFTERNOON_DOSE_TIME)
                                message = f"Hi {patient.name}, it's time for your afternoon dose of {item.medicine_name}."
                                async_task('api.tasks.send_prescription_reminder_sms', patient.phone_number, message, schedule=schedule_datetime)

                            if item.timing_evening:
                                schedule_datetime = datetime.combine(reminder_date, EVENING_DOSE_TIME)
                                message = f"Hi {patient.name}, it's time for your evening dose of {item.medicine_name}."
                                async_task('api.tasks.send_prescription_reminder_sms', patient.phone_number, message, schedule=schedule_datetime)
            
            serializer = ConsultationSerializer(consultation)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        except Patient.DoesNotExist:
            return Response({'error': 'Patient not found.'}, status=status.HTTP_404_NOT_FOUND)
        except Doctor.DoesNotExist:
            return Response({'error': 'Logged-in user is not a doctor.'}, status=status.HTTP_403_FORBIDDEN)
        except Exception as e:
            return Response({'error': f'An unexpected error occurred: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class TokenUpdateStatusView(generics.UpdateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = TokenSerializer
    lookup_field = 'id'
    
    def get_queryset(self):
        user = self.request.user
        clinic = None
        if hasattr(user, 'doctor'): clinic = user.doctor.clinic
        elif hasattr(user, 'receptionist'): clinic = user.receptionist.clinic
        if clinic:
            return Token.objects.filter(clinic=clinic, date=timezone.now().date())
        return Token.objects.none()

    def patch(self, request, *args, **kwargs):
        instance = self.get_object()
        new_status = request.data.get('status')
        if new_status not in ['confirmed', 'completed', 'skipped', 'cancelled', 'in_consultancy']:
            return Response({'error': 'Invalid or not allowed status update.'}, status=status.HTTP_400_BAD_REQUEST)
        instance.status = new_status
        instance.save()
        return Response(TokenSerializer(instance).data)

# ====================================================================
# --- IVR LOGIC ---
# ====================================================================
def create_and_speak_token(response, doctor, caller_phone_number, age=0):
    patient_name = f"IVR Patient {caller_phone_number[-4:]}"
    patient, _ = Patient.objects.get_or_create(phone_number=caller_phone_number, defaults={'name': patient_name, 'age': age})
    
    if Token.objects.filter(patient=patient, date=timezone.now().date()).exclude(status__in=['completed', 'cancelled']).exists():
        response.say(f"You already have an active token for today. Please check your SMS. Goodbye.")
        response.hangup()
        return response

    new_token = Token.objects.create(patient=patient, doctor=doctor)
    message = f"Your token for Dr. {doctor.name} at {doctor.clinic.name} is {new_token.token_number}."
    send_sms_notification(patient.phone_number, message)
    
    token_number_spoken = " ".join(list(str(new_token.token_number)))
    response.say(f"You have been assigned to Doctor {doctor.name}. Your token is {token_number_spoken}. An SMS has been sent. Goodbye.")
    response.hangup()
    return response

@csrf_exempt
def ivr_welcome(request):
    response = VoiceResponse()
    gather = response.gather(num_digits=1, action='/api/ivr/select_clinic/')
    say_message = "Welcome. Please select a clinic. "
    clinics = Clinic.objects.all()
    if not clinics: 
        response.say("Sorry, no clinics are configured. Goodbye.")
        response.hangup()
    else:
        for i, clinic in enumerate(clinics): say_message += f"For {clinic.name}, press {i + 1}. "
        gather.say(say_message)
        response.redirect('/api/ivr/welcome/')
    return HttpResponse(str(response), content_type='text/xml')

@csrf_exempt
def ivr_select_clinic(request):
    choice = request.POST.get('Digits')
    response = VoiceResponse()
    try:
        clinic = Clinic.objects.all()[int(choice) - 1]
        gather = response.gather(num_digits=1, action=f'/api/ivr/handle_booking_type/{clinic.id}/')
        gather.say(f"You selected {clinic.name}. For next available doctor, press 1. To choose a specific doctor, press 2.")
        response.redirect(f'/api/ivr/select_clinic/')
    except (ValueError, IndexError):
        response.say("Invalid choice.")
        response.redirect('/api/ivr/welcome/')
    return HttpResponse(str(response), content_type='text/xml')

@csrf_exempt
def ivr_handle_booking_type(request, clinic_id):
    choice = request.POST.get('Digits')
    response = VoiceResponse()
    try:
        clinic = Clinic.objects.get(id=clinic_id)
        if choice == '1':
            doctor = Doctor.objects.filter(clinic=clinic).annotate(num_tokens=Count('token', filter=Q(date=timezone.now().date()))).order_by('num_tokens').first()
            if not doctor: 
                response.say("Sorry, no doctors are available.")
                response.hangup()
            else: 
                create_and_speak_token(response, doctor, request.POST.get('From', 'Unknown'))
        elif choice == '2':
            gather = response.gather(num_digits=1, action=f'/api/ivr/handle_specific_doctor/{clinic.id}/')
            say_message = "Please select a doctor. "
            for i, doctor in enumerate(Doctor.objects.filter(clinic=clinic)): 
                say_message += f"For Doctor {doctor.name}, press {i + 1}. "
            gather.say(say_message)
        else: 
            response.say("Invalid choice.")
            response.redirect(f'/api/ivr/handle_booking_type/{clinic_id}/')
    except Clinic.DoesNotExist: 
        response.say("Clinic not found.")
        response.hangup()
    return HttpResponse(str(response), content_type='text/xml')

@csrf_exempt
def ivr_handle_specific_doctor(request, clinic_id):
    choice = request.POST.get('Digits')
    response = VoiceResponse()
    try:
        doctor = Doctor.objects.filter(clinic_id=clinic_id)[int(choice) - 1]
        create_and_speak_token(response, doctor, request.POST.get('From', 'Unknown'))
    except (ValueError, IndexError):
        response.say("Invalid choice.")
        response.redirect(f'/api/ivr/handle_booking_type/{clinic_id}/')
    return HttpResponse(str(response), content_type='text/xml')