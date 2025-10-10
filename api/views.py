from django.shortcuts import render
from rest_framework import generics, status, permissions
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.authtoken.models import Token as AuthToken
from rest_framework.views import APIView
from .models import Token, Doctor, Patient, Consultation, Receptionist, Clinic, PrescriptionItem, State, District
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
from django.contrib.auth import authenticate, get_user_model
from django.views.decorators.csrf import csrf_exempt
from twilio.twiml.voice_response import VoiceResponse
from django.http import HttpResponse
from django.db import transaction
import random

# --- Core App Imports ---
from .utils import send_sms_notification
# --- Imports for Django-Q Scheduling ---
from django_q.tasks import async_task
from datetime import datetime, timedelta, time

User = get_user_model()

# --- Helper Functions ---
def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371.0 # Radius of Earth in kilometers
    lat1_rad, lon1_rad, lat2_rad, lon2_rad = map(radians, [lat1, lon1, lat2, lon2])
    dlon, dlat = lon2_rad - lon1_rad, lat2_rad - lat1_rad
    a = sin(dlat / 2)**2 + cos(lat1_rad) * cos(lat2_rad) * sin(dlon / 2)**2
    return R * (2 * atan2(sqrt(a), sqrt(1 - a)))

# --- NEW HELPER FUNCTION TO GET SLOTS ---
# This logic is moved from AvailableSlotsView to be reusable
def _get_available_slots_for_doctor(doctor_id, date_str):
    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return None # Return None on bad date format

    start_time = time(9, 0)
    end_time = time(17, 0)
    slot_duration = timedelta(minutes=15)

    all_slots = []
    current_time = datetime.combine(target_date, start_time)
    end_datetime = datetime.combine(target_date, end_time)
    while current_time < end_datetime:
        all_slots.append(current_time.time())
        current_time += slot_duration

    booked_tokens = Token.objects.filter(
        doctor_id=doctor_id,
        date=target_date,
        appointment_time__isnull=False
    ).exclude(status='cancelled')
    
    booked_slots = {token.appointment_time for token in booked_tokens}

    available_slots = [slot for slot in all_slots if slot not in booked_slots]
    
    return [slot.strftime('%H:%M') for slot in available_slots]


# --- MODIFIED VIEW ---
class AvailableSlotsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, doctor_id, date):
        # This view now calls the reusable helper function
        formatted_slots = _get_available_slots_for_doctor(doctor_id, date)
        if formatted_slots is None:
            return Response({'error': 'Invalid date format. Use YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)
        return Response(formatted_slots, status=status.HTTP_200_OK)


# --- Public & Private Views ---
class PublicClinicListView(generics.ListAPIView):
    queryset = Clinic.objects.prefetch_related('doctors').all()
    serializer_class = ClinicWithDoctorsSerializer
    permission_classes = [permissions.AllowAny]

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
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            patient = user.patient
            token, _ = AuthToken.objects.get_or_create(user=user)

            if patient.phone_number:
                message = f"Welcome to ClinicFlow AI, {patient.name}! Your registration was successful."
                send_sms_notification(patient.phone_number, message)
            
            patient_data = PatientSerializer(patient).data
            user_data = { 'token': token.key, 'user': {**patient_data, 'role': 'patient'} }

            return Response(user_data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ConfirmArrivalView(APIView):
    permission_classes = [IsAuthenticated]
    def post(self, request, *args, **kwargs):
        user = request.user
        user_lat, user_lon = request.data.get('latitude'), request.data.get('longitude')
        if not all([user_lat, user_lon]): return Response({'error': 'Location data is required.'}, status=status.HTTP_400_BAD_REQUEST)
        if not hasattr(user, 'patient'): return Response({'error': 'No patient profile found.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            token = Token.objects.get(patient=user.patient, date=timezone.now().date(), status='waiting')
            
            if token.appointment_time:
                now = timezone.now()
                appointment_datetime = timezone.make_aware(datetime.combine(token.date, token.appointment_time))
                
                start_window = appointment_datetime - timedelta(hours=20)
                end_window = appointment_datetime + timedelta(minutes=15)

                if not (start_window <= now <= end_window):
                    start_window_str = start_window.strftime('%I:%M %p')
                    return Response({
                        'error': f"You can only confirm arrival between {start_window_str} and the end of your appointment."
                    }, status=status.HTTP_400_BAD_REQUEST)

            clinic = token.clinic
            if not all([clinic.latitude, clinic.longitude]): return Response({'error': 'Clinic location has not been configured by the admin.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            distance = haversine_distance(float(user_lat), float(user_lon), clinic.latitude, clinic.longitude)
            token.distance_km = round(distance, 2)
            if distance > 1.0:
                token.save()
                return Response({'error': f'You are approximately {distance:.1f} km away. You must be within 1 km to confirm your arrival.'}, status=status.HTTP_400_BAD_REQUEST)
            
            token.status = 'confirmed'
            token.save()
            return Response({"message": "Arrival confirmed successfully!", "token": TokenSerializer(token).data}, status=status.HTTP_200_OK)
        except Token.DoesNotExist: return Response({'error': 'No active appointment found to confirm.'}, status=status.HTTP_404_NOT_FOUND)
        except Token.MultipleObjectsReturned: return Response({'error': 'Multiple active appointments found. Please contact reception.'}, status=status.HTTP_400_BAD_REQUEST)

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
    permission_classes = [permissions.AllowAny]

class PatientCreateTokenView(APIView):
    permission_classes = [IsAuthenticated]
    def post(self, request, *args, **kwargs):
        user = request.user
        doctor_id, appointment_date_str, appointment_time_str = request.data.get('doctor_id'), request.data.get('date'), request.data.get('time')
        if not all([doctor_id, appointment_date_str, appointment_time_str]): return Response({'error': 'Doctor, date, and time slot are required.'}, status=status.HTTP_400_BAD_REQUEST)
        if not hasattr(user, 'patient'): return Response({'error': 'Only patients can create appointments.'}, status=status.HTTP_403_FORBIDDEN)
        try:
            appointment_date = datetime.strptime(appointment_date_str, '%Y-%m-%d').date()
            appointment_time = datetime.strptime(appointment_time_str, '%H:%M').time()
            doctor = Doctor.objects.get(id=doctor_id)
        except (ValueError, Doctor.DoesNotExist): return Response({'error': 'Invalid data provided.'}, status=status.HTTP_400_BAD_REQUEST)
        if Token.objects.filter(patient=user.patient, date=appointment_date).exclude(status__in=['completed', 'cancelled']).exists(): return Response({'error': 'You already have an active appointment for this day.'}, status=status.HTTP_400_BAD_REQUEST)
        new_appointment = Token.objects.create(patient=user.patient, doctor=doctor, clinic=doctor.clinic, date=appointment_date, appointment_time=appointment_time, status='waiting')
        if user.patient.phone_number:
            message = (f"Hi {user.patient.name}, your appointment with Dr. {doctor.name} is confirmed for " f"{appointment_date.strftime('%d-%m-%Y')} at {appointment_time.strftime('%I:%M %p')}.")
            send_sms_notification(user.patient.phone_number, message)
        return Response(TokenSerializer(new_appointment).data, status=status.HTTP_201_CREATED)

class TokenListCreate(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = TokenSerializer
    def get_queryset(self):
        user = self.request.user
        today = timezone.now().date()
        if hasattr(user, 'doctor'): return Token.objects.filter(doctor=user.doctor, date=today).exclude(status__in=['completed', 'cancelled']).order_by('created_at')
        elif hasattr(user, 'receptionist'): return Token.objects.filter(clinic=user.receptionist.clinic, date=today).exclude(status__in=['completed', 'cancelled']).order_by('created_at')
        return Token.objects.none()
    def post(self, request, *args, **kwargs):
        patient_name = request.data.get('patient_name')
        patient_age = request.data.get('patient_age')
        phone_number = request.data.get('phone_number')
        doctor_id = request.data.get('assigned_doctor')
        appointment_time_str = request.data.get('appointment_time')
        if not all([patient_name, patient_age, phone_number, doctor_id]):
            return Response({'error': 'Missing required fields'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            receptionist = request.user.receptionist
            doctor = Doctor.objects.get(id=doctor_id, clinic=receptionist.clinic)
            patient, _ = Patient.objects.get_or_create(phone_number=phone_number, defaults={'name': patient_name, 'age': patient_age})
            appointment_time = None
            if appointment_time_str:
                try:
                    appointment_time = datetime.strptime(appointment_time_str, '%H:%M').time()
                except ValueError:
                    return Response({'error': 'Invalid time format. Use HH:MM.'}, status=status.HTTP_400_BAD_REQUEST)
            token_status = 'waiting' if appointment_time else 'confirmed'
            new_token = Token.objects.create(
                patient=patient, doctor=doctor, clinic=doctor.clinic,
                appointment_time=appointment_time, status=token_status
            )
            new_token.refresh_from_db()
            message = f"Dear {patient.name}, your token for Dr. {doctor.name} has been confirmed."
            if new_token.appointment_time:
                  message += f" Your appointment is at {new_token.appointment_time.strftime('%I:%M %p')}."
            if new_token.token_number:
                message += f" Your token number is {new_token.token_number}."
            send_sms_notification(patient.phone_number, message)
            return Response(TokenSerializer(new_token).data, status=status.HTTP_201_CREATED)
        except Doctor.DoesNotExist:
            return Response({'error': 'Doctor not found in your clinic'}, status=status.HTTP_404_NOT_FOUND)
        except Receptionist.DoesNotExist:
            return Response({'error': 'Only receptionists can create tokens.'}, status=status.HTTP_403_FORBIDDEN)

class DoctorList(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = DoctorSerializer
    def get_queryset(self):
        user = self.request.user
        clinic = None
        if hasattr(user, 'doctor'): clinic = user.doctor.clinic
        elif hasattr(user, 'receptionist'): clinic = user.receptionist.clinic
        if clinic: return Doctor.objects.filter(clinic=clinic)
        return Doctor.objects.none()

class LoginView(APIView):
    permission_classes = [permissions.AllowAny]
    def post(self, request, format=None):
        username, password = request.data.get('username'), request.data.get('password')
        user = authenticate(request, username=username, password=password)
        if user is not None and user.is_active and hasattr(user, 'patient'):
            token, _ = AuthToken.objects.get_or_create(user=user)
            patient_data = PatientSerializer(user.patient).data
            user_data = { 'token': token.key, 'user': {**patient_data, 'role': 'patient'} }
            return Response(user_data, status=status.HTTP_200_OK)
        if user is not None and not user.is_active and hasattr(user, 'patient'):
              return Response({'error': 'Account not verified. Please check your SMS for a verification code.'}, status=status.HTTP_401_UNAUTHORIZED)
        return Response({'error': 'Invalid Credentials or not a patient.'}, status=status.HTTP_400_BAD_REQUEST)

class StaffLoginView(APIView):
    permission_classes = [permissions.AllowAny]
    def post(self, request, *args, **kwargs):
        username = request.data.get('username')
        password = request.data.get('password')
        user = authenticate(username=username, password=password)
        if user is not None and user.is_staff:
            token, created = AuthToken.objects.get_or_create(user=user)
            role, profile_data, clinic_data = 'unknown', {'username': user.username}, None
            if hasattr(user, 'doctor'):
                role = 'doctor'
                profile_data['name'] = user.doctor.name
                if user.doctor.clinic: clinic_data = {'id': user.doctor.clinic.id, 'name': user.doctor.clinic.name}
            elif hasattr(user, 'receptionist'):
                role = 'receptionist'
                profile_data['name'] = user.get_full_name() or user.username
                if user.receptionist.clinic: clinic_data = {'id': user.receptionist.clinic.id, 'name': user.receptionist.clinic.name}
            response_data = {'token': token.key, 'user': {**profile_data, 'role': role, 'clinic': clinic_data}}
            return Response(response_data, status=status.HTTP_200_OK)
        return Response({'error': 'Invalid Credentials or not a staff member.'}, status=status.HTTP_400_BAD_REQUEST)

class MyHistoryView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ConsultationSerializer
    def get_queryset(self):
        user = self.request.user
        if hasattr(user, 'patient'): return Consultation.objects.filter(patient=user.patient).order_by('-date')
        return Consultation.objects.none()

class PatientHistoryView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ConsultationSerializer
    def get_queryset(self): 
        return Consultation.objects.filter(patient__id=self.kwargs['patient_id']).order_by('-date')

class PatientLiveQueueView(generics.ListAPIView):
    serializer_class = AnonymizedTokenSerializer
    permission_classes = [permissions.AllowAny]
    def get_queryset(self):
        doctor_id = self.kwargs['doctor_id']
        today = timezone.now().date()
        active_statuses = ['waiting', 'confirmed', 'in_consultancy']
        return Token.objects.filter(doctor_id=doctor_id, date=today, status__in=active_statuses).order_by('token_number')

class ConsultationCreateView(APIView):
    permission_classes = [IsAuthenticated]
    def post(self, request, *args, **kwargs):
        data = request.data
        patient_id, notes = data.get('patient'), data.get('notes')
        prescription_items_data = data.get('prescription_items', [])
        if not patient_id or not notes: return Response({'error': 'Patient and notes are required.'}, status=status.HTTP_400_BAD_REQUEST)
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
                        token.completed_at = timezone.now()
                        token.save()
                except Token.DoesNotExist: pass
                if patient.phone_number and new_prescription_items:
                    MORNING_DOSE_TIME, AFTERNOON_DOSE_TIME, EVENING_DOSE_TIME = time(8, 0), time(13, 0), time(20, 0)
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
        except Patient.DoesNotExist: return Response({'error': 'Patient not found.'}, status=status.HTTP_404_NOT_FOUND)
        except Doctor.DoesNotExist: return Response({'error': 'Logged-in user is not a doctor.'}, status=status.HTTP_403_FORBIDDEN)
        except Exception as e: return Response({'error': f'An unexpected error occurred: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class TokenUpdateStatusView(generics.UpdateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = TokenSerializer
    lookup_field = 'id'
    def get_queryset(self):
        user = self.request.user
        clinic = None
        if hasattr(user, 'doctor'): clinic = user.doctor.clinic
        elif hasattr(user, 'receptionist'): clinic = user.receptionist.clinic
        if clinic: return Token.objects.filter(clinic=clinic, date=timezone.now().date())
        return Token.objects.none()
    def patch(self, request, *args, **kwargs):
        instance = self.get_object()
        new_status = request.data.get('status')
        if new_status not in ['confirmed', 'completed', 'skipped', 'cancelled', 'in_consultancy']: return Response({'error': 'Invalid or not allowed status update.'}, status=status.HTTP_400_BAD_REQUEST)
        if new_status == 'completed': instance.completed_at = timezone.now()
        instance.status = new_status
        instance.save()
        return Response(TokenSerializer(instance).data)

# ====================================================================
# --- ADVANCED IVR LOGIC (WITH NEW TOKEN NUMBER LOGIC) ---
# ====================================================================

# --- MODIFIED FUNCTION ---
def create_and_speak_token(response, doctor, caller_phone_number):
    patient_name = f"IVR Patient {caller_phone_number[-4:]}"
    patient, _ = Patient.objects.get_or_create(phone_number=caller_phone_number, defaults={'name': patient_name, 'age': 0})
    
    today = timezone.now().date()

    if Token.objects.filter(patient=patient, date=today).exclude(status__in=['completed', 'cancelled']).exists():
        response.say("You already have an active appointment for today. Please check your SMS. Goodbye.")
        response.hangup()
        return response

    today_str = today.strftime('%Y-%m-%d')
    # This now calls the helper function directly, bypassing permission checks
    available_slots = _get_available_slots_for_doctor(doctor.id, today_str)
    
    if not available_slots:
        response.say(f"Sorry, Dr. {doctor.name} has no available slots for today.")
        response.hangup()
        return response

    first_slot_str = available_slots[0]
    appointment_time = datetime.strptime(first_slot_str, '%H:%M').time()
    
    start_time = time(9, 0)
    slot_duration_minutes = 15
    appointment_datetime = datetime.combine(today, appointment_time)
    start_datetime = datetime.combine(today, start_time)
    delta_minutes = (appointment_datetime - start_datetime).total_seconds() / 60
    slot_number = int(delta_minutes // slot_duration_minutes) + 1
    
    doctor_initial = doctor.name[0].upper() if doctor.name else "X"
    formatted_token_number = f"{doctor_initial}-{slot_number}"

    new_appointment = Token.objects.create(
        patient=patient, 
        doctor=doctor, 
        clinic=doctor.clinic, 
        date=today, 
        appointment_time=appointment_time, 
        token_number=formatted_token_number,
        status='waiting'
    )
    
    message = (f"Your appointment with Dr. {doctor.name} at {doctor.clinic.name} "
               f"is confirmed for {appointment_time.strftime('%I:%M %p')} today. "
               f"Your token number is {formatted_token_number}.")
    send_sms_notification(patient.phone_number, message)
    
    response.say(f"You have been booked with Doctor {doctor.name} for {appointment_time.strftime('%I:%M %p')} today. "
               f"Your token number is {formatted_token_number}. An SMS has been sent. Goodbye.")
    response.hangup()
    return response

# ... (The rest of the IVR functions remain unchanged) ...

@csrf_exempt
def ivr_welcome(request):
    response = VoiceResponse()
    states = State.objects.all()
    if not states:
        response.say("Sorry, no clinics are configured. Goodbye.")
        response.hangup()
        return HttpResponse(str(response), content_type='text/xml')

    gather = response.gather(num_digits=1, action='/api/ivr/handle-state/')
    say_message = "Welcome to ClinicFlow AI. Please select a state. "
    for i, state in enumerate(states):
        say_message += f"For {state.name}, press {i + 1}. "
    gather.say(say_message)
    response.redirect('/api/ivr/welcome/')
    return HttpResponse(str(response), content_type='text/xml')

@csrf_exempt
def ivr_handle_state(request):
    choice = request.POST.get('Digits')
    response = VoiceResponse()
    try:
        state = State.objects.all()[int(choice) - 1]
        districts = District.objects.filter(state=state)
        if not districts:
            response.say(f"Sorry, no districts found for {state.name}. Please try again.")
            response.redirect('/api/ivr/welcome/')
            return HttpResponse(str(response), content_type='text/xml')

        gather = response.gather(num_digits=len(str(districts.count())), action=f'/api/ivr/handle-district/{state.id}/')
        say_message = f"You selected {state.name}. Please select a district. "
        for i, district in enumerate(districts):
            say_message += f"For {district.name}, press {i + 1}. "
        gather.say(say_message)
        response.redirect('/api/ivr/handle-state/')
    except (ValueError, IndexError):
        response.say("Invalid choice.")
        response.redirect('/api/ivr/welcome/')
    return HttpResponse(str(response), content_type='text/xml')

@csrf_exempt
def ivr_handle_district(request, state_id):
    choice = request.POST.get('Digits')
    response = VoiceResponse()
    try:
        state = State.objects.get(id=state_id)
        district = District.objects.filter(state=state)[int(choice) - 1]
        clinics = Clinic.objects.filter(district=district)
        if not clinics:
            response.say(f"Sorry, no clinics found for {district.name}. Please try again.")
            response.redirect(f'/api/ivr/handle-state/')
            return HttpResponse(str(response), content_type='text/xml')

        gather = response.gather(num_digits=len(str(clinics.count())), action=f'/api/ivr/handle-clinic/{district.id}/')
        say_message = f"You selected {district.name}. Please select a clinic. "
        for i, clinic in enumerate(clinics):
            say_message += f"For {clinic.name}, press {i + 1}. "
        gather.say(say_message)
        response.redirect(f'/api/ivr/handle-district/{state.id}/')
    except (ValueError, IndexError, State.DoesNotExist):
        response.say("Invalid choice or error.")
        response.redirect('/api/ivr/welcome/')
    return HttpResponse(str(response), content_type='text/xml')

@csrf_exempt
def ivr_handle_clinic(request, district_id):
    choice = request.POST.get('Digits')
    response = VoiceResponse()
    try:
        district = District.objects.get(id=district_id)
        clinic = Clinic.objects.filter(district=district)[int(choice) - 1]
        gather = response.gather(num_digits=1, action=f'/api/ivr/handle-booking-type/{clinic.id}/')
        gather.say(f"You selected {clinic.name}. For the next available doctor, press 1. To find a doctor by specialization, press 2.")
        response.redirect(f'/api/ivr/handle-clinic/{district.id}/')
    except (ValueError, IndexError, District.DoesNotExist):
        response.say("Invalid choice or error.")
        response.redirect('/api/ivr/welcome/')
    return HttpResponse(str(response), content_type='text/xml')

@csrf_exempt
def ivr_handle_booking_type(request, clinic_id):
    choice = request.POST.get('Digits')
    response = VoiceResponse()
    caller_phone_number = request.POST.get('From', 'Unknown')
    try:
        clinic = Clinic.objects.get(id=clinic_id)
        if choice == '1':
            doctors = Doctor.objects.filter(clinic=clinic)
            best_doctor = None
            earliest_slot = None

            for doctor in doctors:
                today_str = timezone.now().date().strftime('%Y-%m-%d')
                available_slots = _get_available_slots_for_doctor(doctor.id, today_str)
                if available_slots:
                    first_slot_time = datetime.strptime(available_slots[0], '%H:%M').time()
                    if earliest_slot is None or first_slot_time < earliest_slot:
                        earliest_slot = first_slot_time
                        best_doctor = doctor
            
            if best_doctor:
                final_response = create_and_speak_token(response, best_doctor, caller_phone_number)
                return HttpResponse(str(final_response), content_type='text/xml')
            else:
                response.say("Sorry, no doctors have available slots today. Please call back later.")
                response.hangup()
                return HttpResponse(str(response), content_type='text/xml')

        elif choice == '2':
            specializations = Doctor.objects.filter(clinic=clinic).values_list('specialization', flat=True).distinct()
            if not specializations:
                response.say("Sorry, no specializations found for this clinic.")
                response.redirect(f'/api/ivr/handle-clinic/{clinic.district.id}/')
                return HttpResponse(str(response), content_type='text/xml')

            gather = response.gather(num_digits=len(str(len(specializations))), action=f'/api/ivr/handle-specialization/{clinic.id}/')
            say_message = "Please select a specialization. "
            for i, spec in enumerate(specializations):
                say_message += f"For {spec}, press {i + 1}. "
            gather.say(say_message)
            response.redirect(f'/api/ivr/handle-booking-type/{clinic.id}/')
        else:
            response.say("Invalid choice.")
            response.redirect(f'/api/ivr/handle-booking-type/{clinic.id}/')
    except (Clinic.DoesNotExist, AttributeError):
        response.say("An error occurred. Please start over.")
        response.redirect('/api/ivr/welcome/')
    return HttpResponse(str(response), content_type='text/xml')

@csrf_exempt
def ivr_handle_specialization(request, clinic_id):
    choice = request.POST.get('Digits')
    response = VoiceResponse()
    try:
        clinic = Clinic.objects.get(id=clinic_id)
        specializations = list(Doctor.objects.filter(clinic=clinic).values_list('specialization', flat=True).distinct())
        spec = specializations[int(choice) - 1]

        doctors = Doctor.objects.filter(clinic=clinic, specialization=spec)
        gather = response.gather(num_digits=len(str(doctors.count())), action=f'/api/ivr/handle-doctor/{clinic.id}/{spec}/')
        say_message = f"You selected {spec}. Please select a doctor. "
        for i, doctor in enumerate(doctors):
            say_message += f"For Doctor {doctor.name}, press {i + 1}. "
        gather.say(say_message)
        response.redirect(f'/api/ivr/handle-specialization/{clinic.id}/')
    except (ValueError, IndexError, Clinic.DoesNotExist):
        response.say("Invalid choice or error.")
        response.redirect(f'/api/ivr/handle-booking-type/{clinic.id}/')
    return HttpResponse(str(response), content_type='text/xml')

@csrf_exempt
def ivr_handle_doctor(request, clinic_id, spec):
    choice = request.POST.get('Digits')
    response = VoiceResponse()
    caller_phone_number = request.POST.get('From', 'Unknown')
    try:
        doctor = Doctor.objects.filter(clinic_id=clinic_id, specialization=spec)[int(choice) - 1]
        final_response = create_and_speak_token(response, doctor, caller_phone_number)
        return HttpResponse(str(final_response), content_type='text/xml')
    except (ValueError, IndexError):
        response.say("Invalid choice.")
        response.redirect(f'/api/ivr/handle-specialization/{clinic.id}/')
    return HttpResponse(str(response), content_type='text/xml')

