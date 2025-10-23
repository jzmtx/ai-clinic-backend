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
from django.db.models import Count, Avg, F, Q, Case, When, Value
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

def _get_available_slots_for_doctor(doctor_id, date_str):
    """Returns list of available HH:MM strings for a single date."""
    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return None
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
        doctor_id=doctor_id, date=target_date, appointment_time__isnull=False
    ).exclude(status__in=['cancelled', 'skipped'])
    booked_slots = {token.appointment_time for token in booked_tokens}
    available_slots = [slot for slot in all_slots if slot not in booked_slots]
    return [slot.strftime('%H:%M') for slot in available_slots]

# --- NEW: Function to find the next earliest available slot across dates ---
def _find_next_available_slot_for_doctor(doctor_id):
    """Checks today and future dates for the first available slot."""
    doctor = Doctor.objects.get(id=doctor_id)
    
    # Check today first, starting from the current time
    today = timezone.now().date()
    current_date = today
    
    # Check up to 7 days in the future (arbitrary limit for IVR convenience)
    for i in range(7):
        date_str = current_date.strftime('%Y-%m-%d')
        available_slots = _get_available_slots_for_doctor(doctor_id, date_str)
        
        if available_slots:
            # If today, filter out past slots
            if current_date == today:
                now_time = timezone.now().time()
                # Filter slots that are >= current time
                current_time_slots = [
                    slot for slot in available_slots 
                    if datetime.strptime(slot, '%H:%M').time() >= now_time
                ]
                if current_time_slots:
                    return current_date, current_time_slots[0]
            else:
                # If future date, return the very first slot
                return current_date, available_slots[0]

        current_date += timedelta(days=1)
        
    return None, None # No slots found in the next 7 days

# --- MODIFIED: Refactored create_and_speak_token to use the new helper ---
def create_and_speak_token(response, doctor, caller_phone_number):
    patient_name = f"IVR Patient {caller_phone_number[-4:]}"
    
    # Safely retrieve patient (handles database corruption by taking first match)
    patient_query = Patient.objects.filter(phone_number=caller_phone_number)
    patient = patient_query.first() 
    if not patient:
        patient, _ = Patient.objects.get_or_create(phone_number=caller_phone_number, defaults={'name': patient_name, 'age': 0})
    
    today = timezone.now().date()
    
    # --- STEP 1: Check for existing active token on ANY day ---
    if Token.objects.filter(patient=patient).exclude(status__in=['completed', 'cancelled', 'skipped']).exists():
        response.say("You already have an active appointment. Cannot book another one now. Goodbye.")
        response.hangup()
        return HttpResponse(str(response), content_type='text/xml') 

    # --- STEP 2: Find the next available slot (today or future) ---
    appointment_date, first_slot_str = _find_next_available_slot_for_doctor(doctor.id)
    
    if not appointment_date:
        response.say(f"Sorry, Dr. {doctor.name} has no available slots in the next few days. Goodbye.")
        response.hangup()
        return HttpResponse(str(response), content_type='text/xml')
        
    appointment_time = datetime.strptime(first_slot_str, '%H:%M').time()
    
    # Calculate token number
    start_time = time(9, 0)
    slot_duration_minutes = 15
    appointment_datetime = datetime.combine(appointment_date, appointment_time)
    start_datetime = datetime.combine(appointment_date, start_time)
    delta_minutes = (appointment_datetime - start_datetime).total_seconds() / 60
    slot_number = int(delta_minutes // slot_duration_minutes) + 1
    doctor_initial = doctor.name[0].upper() if doctor.name else "X"
    formatted_token_number = f"{doctor_initial}-{slot_number}"
    
    # --- STEP 3: Create the token ---
    new_appointment = Token.objects.create(
        patient=patient, doctor=doctor, clinic=doctor.clinic, date=appointment_date, 
        appointment_time=appointment_time, token_number=formatted_token_number, status='waiting'
    )
    
    # --- STEP 4: Speak and send confirmation ---
    date_spoken = "today" if appointment_date == today else appointment_date.strftime("%B %d")
    
    message = (f"Your appointment with Dr. {doctor.name} at {doctor.clinic.name} is confirmed for "
               f"{appointment_time.strftime('%I:%M %p')} on {date_spoken}. Your token number is {formatted_token_number}.")
    try: 
        send_sms_notification(patient.phone_number, message)
    except Exception as e:
        print(f"IVR: Failed to send confirmation SMS to {patient.phone_number}: {e}") 
    
    response.say(f"Your appointment with Doctor {doctor.name} is confirmed for {appointment_time.strftime('%I:%M %p')} on {date_spoken}. "
                 f"Your token number is {formatted_token_number}. An SMS confirmation has been sent. Goodbye.")
    
    return HttpResponse(str(response), content_type='text/xml') # FIX

# --- Standard API Views (rest are kept for completeness) ---

class AvailableSlotsView(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request, doctor_id, date):
        formatted_slots = _get_available_slots_for_doctor(doctor_id, date)
        if formatted_slots is None:
            return Response({'error': 'Invalid date format. Use YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)
        return Response(formatted_slots, status=status.HTTP_200_OK)

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
                message = f"Welcome to MedQ, {patient.name}! Your registration was successful."
                try:
                     send_sms_notification(patient.phone_number, message)
                except Exception as e:
                     print(f"Failed to send welcome SMS: {e}")
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
                start_window = appointment_datetime - timedelta(minutes=20) 
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
            if distance > 1.0: # 1km radius check
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
            today = timezone.now().date()
            token = Token.objects.filter(
                patient=user.patient, 
                date=today
            ).exclude(status__in=['completed', 'cancelled']).order_by('appointment_time', 'created_at').first()
            if not token:
                token = Token.objects.filter(
                    patient=user.patient, 
                    date__gt=today, 
                    status__in=['waiting', 'confirmed']
                ).order_by('date', 'appointment_time', 'created_at').first()
            
            if token:
                return Response(TokenSerializer(token).data)
            else:
                return Response({'error': 'No active or upcoming appointments found.'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            print(f"Error in GetPatientTokenView: {e}")
            return Response({'error': 'An error occurred while fetching your token.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

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
            
            if appointment_date < timezone.now().date():
                return Response({'error': 'Cannot book appointments for past dates.'}, status=status.HTTP_400_BAD_REQUEST)

            doctor = Doctor.objects.get(id=doctor_id)
        except (ValueError, Doctor.DoesNotExist): return Response({'error': 'Invalid data provided.'}, status=status.HTTP_400_BAD_REQUEST)
        
        if Token.objects.filter(patient=user.patient, date=appointment_date).exclude(status__in=['completed', 'cancelled', 'skipped']).exists(): 
            return Response({'error': 'You already have an active appointment for this day.'}, status=status.HTTP_400_BAD_REQUEST)
        
        formatted_slots = _get_available_slots_for_doctor(doctor_id, appointment_date_str)
        if formatted_slots is None or appointment_time_str not in formatted_slots:
             return Response({'error': 'This slot is no longer available. Please select another time.'}, status=status.HTTP_409_CONFLICT) 

        new_appointment = Token.objects.create(patient=user.patient, doctor=doctor, clinic=doctor.clinic, date=appointment_date, appointment_time=appointment_time, status='waiting')
        if user.patient.phone_number:
            message = (f"Hi {user.patient.name}, your appointment with Dr. {doctor.name} is confirmed for " f"{appointment_date.strftime('%d-%m-%Y')} at {appointment_time.strftime('%I:%M %p')}.")
            try:
                send_sms_notification(user.patient.phone_number, message)
            except Exception as e:
                print(f"Failed to send confirmation SMS for new token {new_appointment.id}: {e}")
        return Response(TokenSerializer(new_appointment).data, status=status.HTTP_201_CREATED)

class TokenListCreate(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = TokenSerializer
    
    def get_queryset(self):
        user = self.request.user
        today = timezone.now().date()
        
        status_priority = Case(
            When(status='in_consultancy', then=Value(1)), 
            When(status='confirmed', then=Value(2)),      
            When(status='waiting', then=Value(3)),        
            default=Value(4)
        )
        
        base_queryset = Token.objects.filter(
            date=today
        ).exclude(status__in=['completed', 'cancelled'])
        
        if hasattr(user, 'doctor'): 
            queryset = base_queryset.filter(doctor=user.doctor)
        elif hasattr(user, 'receptionist'): 
            queryset = base_queryset.filter(clinic=user.receptionist.clinic)
        else:
            return Token.objects.none()

        return queryset.order_by(
            status_priority,
            F('appointment_time').asc(nulls_last=True), 
            'created_at'
        )

    def post(self, request, *args, **kwargs):
        patient_name = request.data.get('patient_name')
        patient_age = request.data.get('patient_age')
        phone_number = request.data.get('phone_number')
        doctor_id = request.data.get('assigned_doctor')
        appointment_time_str = request.data.get('appointment_time') # e.g., "09:15"
        
        if not all([patient_name, patient_age, phone_number, doctor_id]):
            return Response({'error': 'Missing required fields'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            if not hasattr(request.user, 'receptionist'):
                 return Response({'error': 'Only receptionists can create tokens.'}, status=status.HTTP_403_FORBIDDEN)
                 
            receptionist = request.user.receptionist
            doctor = Doctor.objects.get(id=doctor_id, clinic=receptionist.clinic)
            
            patient, created = Patient.objects.update_or_create(
                phone_number=phone_number, 
                defaults={'name': patient_name, 'age': patient_age}
            )
            
            appointment_time = None
            today_str = timezone.now().date().strftime('%Y-%m-%d')
            
            # --- MODIFIED: Add slot check for receptionists ---
            if appointment_time_str:
                try:
                    appointment_time = datetime.strptime(appointment_time_str, '%H:%M').time()
                    
                    # Check if slot is available
                    available_slots = _get_available_slots_for_doctor(doctor.id, today_str)
                    # Note: available_slots is a list of strings (e.g., "09:15")
                    if available_slots is None or appointment_time_str not in available_slots:
                         return Response({'error': 'This slot is already booked. Please select another.'}, status=status.HTTP_409_CONFLICT)
                
                except ValueError:
                    return Response({'error': 'Invalid time format. Use HH:MM.'}, status=status.HTTP_400_BAD_REQUEST)

            # Check if this patient already has an active token for today
            if Token.objects.filter(patient=patient, date=timezone.now().date()).exclude(status__in=['completed', 'cancelled', 'skipped']).exists():
                 return Response({'error': f'Patient {patient.name} already has an active token for today.'}, status=status.HTTP_400_BAD_REQUEST)

            # If walk-in (no time), status is 'confirmed' (ready to be seen)
            # If booked slot, status is 'waiting' (for patient arrival)
            token_status = 'waiting' if appointment_time else 'confirmed'
            # --- END MODIFICATION ---
            
            new_token = Token.objects.create(
                patient=patient, doctor=doctor, clinic=doctor.clinic,
                date=timezone.now().date(),
                appointment_time=appointment_time, 
                status=token_status
            )
            
            message = f"Dear {patient.name}, your token for Dr. {doctor.name} at {doctor.clinic.name} has been confirmed for today."
            if new_token.appointment_time:
                  message += f" Your appointment is at {new_token.appointment_time.strftime('%I:%M %p')}."
            
            new_token.refresh_from_db() 
            if new_token.token_number:
                message += f" Your token number is {new_token.token_number}."
            
            try:
                send_sms_notification(patient.phone_number, message)
            except Exception as e:
                print(f"Receptionist: Failed to send confirmation SMS to {patient.phone_number}: {e}")
                
            return Response(TokenSerializer(new_token).data, status=status.HTTP_201_CREATED)
        
        except Doctor.DoesNotExist:
            return Response({'error': 'Doctor not found in your clinic'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e: 
             print(f"Error creating token (receptionist): {e}")
             return Response({'error': 'An error occurred while creating the token.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class DoctorList(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
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
        
        if user is not None:
             if not user.is_active:
                 if hasattr(user, 'patient'):
                       return Response({'error': 'Account not verified. Please contact support.'}, status=status.HTTP_401_UNAUTHORIZED)
                 else:
                       return Response({'error': 'Account is inactive.'}, status=status.HTTP_403_FORBIDDEN)
             
             if hasattr(user, 'patient'):
                 token, _ = AuthToken.objects.get_or_create(user=user)
                 patient_data = PatientSerializer(user.patient).data
                 user_data = { 'token': token.key, 'user': {**patient_data, 'role': 'patient'} }
                 return Response(user_data, status=status.HTTP_200_OK)

        return Response({'error': 'Invalid Credentials or not a patient.'}, status=status.HTTP_400_BAD_REQUEST)

class StaffLoginView(APIView):
    permission_classes = [permissions.AllowAny]
    def post(self, request, *args, **kwargs):
        username = request.data.get('username')
        password = request.data.get('password')
        user = authenticate(username=username, password=password)
        if user is not None and user.is_staff: # Staff must be active
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
        patient_id = self.kwargs.get('patient_id')
        if patient_id:
            return Consultation.objects.filter(patient__id=patient_id).order_by('-date')
        return Consultation.objects.none()

class PatientLiveQueueView(generics.ListAPIView):
    serializer_class = AnonymizedTokenSerializer
    permission_classes = [permissions.AllowAny] 
    def get_queryset(self):
        doctor_id = self.kwargs.get('doctor_id') 
        date_str = self.kwargs.get('date') 

        if not doctor_id or not date_str:
            return Token.objects.none() 
            
        try: 
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return Token.objects.none() 

        active_statuses = ['waiting', 'confirmed', 'in_consultancy']
        
        status_priority = Case(
            When(status='in_consultancy', then=Value(1)),
            When(status='confirmed', then=Value(2)),     
            When(status='waiting', then=Value(3)),       
            default=Value(4) 
        )
        
        return Token.objects.filter(
            doctor_id=doctor_id, 
            date=target_date, 
            status__in=active_statuses
        ).order_by(
            status_priority,
            F('appointment_time').asc(nulls_last=True), 
            'created_at'
        )

class ConsultationCreateView(APIView):
    permission_classes = [IsAuthenticated]
    def post(self, request, *args, **kwargs):
        data = request.data
        patient_id, notes = data.get('patient'), data.get('notes')
        prescription_items_data = data.get('prescription_items', [])
        if not patient_id or not notes: return Response({'error': 'Patient and notes are required.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            patient = Patient.objects.get(id=patient_id)
            if not hasattr(request.user, 'doctor'):
                 return Response({'error': 'Only doctors can create consultations.'}, status=status.HTTP_403_FORBIDDEN)
            doctor = request.user.doctor
            
            new_prescription_items = []
            with transaction.atomic():
                consultation = Consultation.objects.create(patient=patient, doctor=doctor, notes=notes)
                for item_data in prescription_items_data:
                    item = PrescriptionItem.objects.create(consultation=consultation, **item_data)
                    new_prescription_items.append(item)
                
                try:
                    token = Token.objects.filter(
                        patient=patient, 
                        doctor=doctor, 
                        date=timezone.now().date(), 
                        status__in=['waiting', 'confirmed', 'in_consultancy']
                    ).latest('created_at') 
                    
                    token.status = 'completed'
                    token.completed_at = timezone.now()
                    token.save(update_fields=['status', 'completed_at'])
                except Token.DoesNotExist: 
                     print(f"No active token found to complete for patient {patient.id} with doctor {doctor.id} today.")

                if patient.phone_number and new_prescription_items:
                    MORNING_DOSE_TIME, AFTERNOON_DOSE_TIME, EVENING_DOSE_TIME = time(8, 0), time(13, 0), time(20, 0)
                    today = timezone.now().date()
                    for item in new_prescription_items:
                        try:
                             duration = int(item.duration_days)
                             for day in range(1, duration + 1):
                                 reminder_date = today + timedelta(days=day)
                                 if item.timing_morning:
                                     schedule_datetime = datetime.combine(reminder_date, MORNING_DOSE_TIME)
                                     async_task('api.tasks.send_prescription_reminder_sms', patient.phone_number, message, schedule=schedule_datetime)
                                 if item.timing_afternoon:
                                     schedule_datetime = datetime.combine(reminder_date, AFTERNOON_DOSE_TIME)
                                     async_task('api.tasks.send_prescription_reminder_sms', patient.phone_number, message, schedule=schedule_datetime)
                                 if item.timing_evening:
                                     schedule_datetime = datetime.combine(reminder_date, EVENING_DOSE_TIME)
                                     async_task('api.tasks.send_prescription_reminder_sms', patient.phone_number, message, schedule=schedule_datetime)
                        except (ValueError, TypeError) as e:
                             print(f"Error scheduling reminders for item {item.id}: Invalid duration '{item.duration_days}'. Error: {e}")
            
            serializer = ConsultationSerializer(consultation)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        except Patient.DoesNotExist: return Response({'error': 'Patient not found.'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e: 
             print(f"Error creating consultation: {e}")
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
        
        allowed_statuses = ['waiting', 'confirmed', 'completed', 'skipped', 'cancelled', 'in_consultancy']
        if new_status not in allowed_statuses: 
            return Response({'error': 'Invalid status update.'}, status=status.HTTP_400_BAD_REQUEST)

        if instance.status == 'completed' or instance.status == 'cancelled':
             return Response({'error': f'Cannot change status from {instance.status}.'}, status=status.HTTP_400_BAD_REQUEST)
        
        if new_status == 'completed': 
             instance.completed_at = timezone.now()
        else:
             instance.completed_at = None 

        instance.status = new_status
        instance.save(update_fields=['status', 'completed_at'])
        return Response(TokenSerializer(instance).data)

# ====================================================================
# --- IVR LOGIC ---
# ====================================================================
def create_and_speak_token(response, doctor, caller_phone_number):
    patient_name = f"IVR Patient {caller_phone_number[-4:]}"
    # --- MODIFIED: Use filter().first() to avoid crash on duplicate records ---
    patient_query = Patient.objects.filter(phone_number=caller_phone_number)
    patient = patient_query.first() 
    
    # If patient not found, create a new one (safe to use get_or_create on the first attempt only)
    if not patient:
        patient, _ = Patient.objects.get_or_create(phone_number=caller_phone_number, defaults={'name': patient_name, 'age': 0})
    # --- END MODIFIED ---
    
    today = timezone.now().date()
    # Check for active token on ANY day (not just today)
    if Token.objects.filter(patient=patient).exclude(status__in=['completed', 'cancelled', 'skipped']).exists():
        response.say("You already have an active appointment. Cannot book another one now. Goodbye.")
        response.hangup()
        return HttpResponse(str(response), content_type='text/xml') 
        
    # --- MODIFIED: Find the next available slot (today or future) ---
    appointment_date, first_slot_str = _find_next_available_slot_for_doctor(doctor.id)
    
    if not appointment_date:
        response.say(f"Sorry, Dr. {doctor.name} has no available slots in the next few days. Goodbye.")
        response.hangup()
        return HttpResponse(str(response), content_type='text/xml')

    appointment_time = datetime.strptime(first_slot_str, '%H:%M').time()
    
    # Calculate token number
    start_time = time(9, 0)
    slot_duration_minutes = 15
    appointment_datetime = datetime.combine(appointment_date, appointment_time)
    start_datetime = datetime.combine(appointment_date, start_time)
    delta_minutes = (appointment_datetime - start_datetime).total_seconds() / 60
    slot_number = int(delta_minutes // slot_duration_minutes) + 1
    doctor_initial = doctor.name[0].upper() if doctor.name else "X"
    formatted_token_number = f"{doctor_initial}-{slot_number}"
    
    # --- Create the token with the correct date ---
    new_appointment = Token.objects.create(
        patient=patient, doctor=doctor, clinic=doctor.clinic, date=appointment_date, 
        appointment_time=appointment_time, token_number=formatted_token_number, status='waiting'
    )
    
    # --- STEP 4: Speak and send confirmation ---
    date_spoken = "today" if appointment_date == today else appointment_date.strftime("%B %d")
    
    message = (f"Your appointment with Dr. {doctor.name} at {doctor.clinic.name} is confirmed for "
               f"{appointment_time.strftime('%I:%M %p')} on {date_spoken}. Your token number is {formatted_token_number}.")
    try: 
        send_sms_notification(patient.phone_number, message)
    except Exception as e:
        print(f"IVR: Failed to send confirmation SMS to {patient.phone_number}: {e}") 
    
    response.say(f"Your appointment with Doctor {doctor.name} is confirmed for {appointment_time.strftime('%I:%M %p')} on {date_spoken}. "
                 f"Your token number is {formatted_token_number}. An SMS confirmation has been sent. Goodbye.")
    
    return HttpResponse(str(response), content_type='text/xml') # FIX

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
    # --- FIX: Return raw TwiML to prevent AttributeError in middleware ---
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
        num_digits = len(str(districts.count())) if districts.count() > 0 else 1 
        gather = response.gather(num_digits=num_digits, action=f'/api/ivr/handle-district/{state.id}/')
        say_message = f"You selected {state.name}. Please select a district. "
        for i, district in enumerate(districts):
            say_message += f"For {district.name}, press {i + 1}. "
        gather.say(say_message)
        response.redirect('/api/ivr/handle-state/') 
    except (ValueError, IndexError, TypeError): 
        response.say("Invalid choice.")
        response.redirect('/api/ivr/welcome/')
    # --- FIX: Return raw TwiML to prevent AttributeError in middleware ---
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
        num_digits = len(str(clinics.count())) if clinics.count() > 0 else 1
        gather = response.gather(num_digits=num_digits, action=f'/api/ivr/handle-clinic/{district.id}/')
        say_message = f"You selected {district.name}. Please select a clinic. "
        for i, clinic in enumerate(clinics):
            say_message += f"For {clinic.name}, press {i + 1}. "
        gather.say(say_message)
        response.redirect(f'/api/ivr/handle-district/{state.id}/')
    except (ValueError, IndexError, TypeError, State.DoesNotExist):
        response.say("Invalid choice or error.")
        response.redirect('/api/ivr/welcome/')
    # --- FIX: Return raw TwiML to prevent AttributeError in middleware ---
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
    except (ValueError, IndexError, TypeError, District.DoesNotExist):
        response.say("Invalid choice or error.")
        response.redirect('/api/ivr/welcome/')
    # --- FIX: Return raw TwiML to prevent AttributeError in middleware ---
    return HttpResponse(str(response), content_type='text/xml')

@csrf_exempt
def ivr_handle_booking_type(request, clinic_id):
    choice = request.POST.get('Digits')
    response = VoiceResponse()
    caller_phone_number = request.POST.get('From', None) 
    if not caller_phone_number:
         response.say("We could not identify your phone number. Cannot proceed with booking. Goodbye.")
         response.hangup()
         return HttpResponse(str(response), content_type='text/xml')
    try:
        clinic = Clinic.objects.get(id=clinic_id)
        if choice == '1': # Book with next available
            doctors = Doctor.objects.filter(clinic=clinic)
            if not doctors.exists():
                 response.say(f"Sorry, no doctors found for {clinic.name}. Please try again later.")
                 response.hangup()
                 return HttpResponse(str(response), content_type='text/xml')
            best_doctor = None
            earliest_slot_time = None 
            today_str = timezone.now().date().strftime('%Y-%m-%d')
            for doctor in doctors:
                available_slots = _get_available_slots_for_doctor(doctor.id, today_str)
                if available_slots:
                    first_slot_time = datetime.strptime(available_slots[0], '%H:%M').time()
                    if earliest_slot_time is None or first_slot_time < earliest_slot_time:
                        earliest_slot_time = first_slot_time
                        best_doctor = doctor
            if best_doctor:
                return create_and_speak_token(response, best_doctor, caller_phone_number)
            else:
                response.say(f"Sorry, no doctors have available slots today at {clinic.name}. Please call back later.")
                response.hangup()
                return HttpResponse(str(response), content_type='text/xml')
        elif choice == '2': # Find by specialization
            specializations = list(Doctor.objects.filter(clinic=clinic).values_list('specialization', flat=True).distinct())
            if not specializations:
                response.say(f"Sorry, no specializations found for {clinic.name}.")
                response.redirect(f'/api/ivr/handle-clinic/{clinic.district_id}/') # Use clinic's district ID
                return HttpResponse(str(response), content_type='text/xml')
            num_digits = len(str(len(specializations))) if specializations else 1
            gather = response.gather(num_digits=num_digits, action=f'/api/ivr/handle-specialization/{clinic.id}/')
            say_message = "Please select a specialization. "
            for i, spec in enumerate(specializations):
                say_message += f"For {spec}, press {i + 1}. "
            gather.say(say_message)
            response.redirect(f'/api/ivr/handle-booking-type/{clinic.id}/')
        else:
            response.say("Invalid choice.")
            response.redirect(f'/api/ivr/handle-booking-type/{clinic.id}/')
    except Clinic.DoesNotExist:
        response.say("Clinic not found. Please start over.")
        response.redirect('/api/ivr/welcome/')
    except Exception as e: 
         print(f"Error in ivr_handle_booking_type: {e}")
         response.say("An application error occurred. Please try again later. Goodbye.")
         response.hangup()
    # --- FIX: Return raw TwiML to prevent AttributeError in middleware ---
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
        if not doctors.exists():
             response.say(f"Sorry, no doctors found for specialization {spec} at this clinic.")
             response.redirect(f'/api/ivr/handle-booking-type/{clinic_id}/') # Go back
             return HttpResponse(str(response), content_type='text/xml')
        num_digits = len(str(doctors.count())) if doctors.count() > 0 else 1
        gather = response.gather(num_digits=num_digits, action=f'/api/ivr/handle-doctor/{clinic.id}/{spec}/')
        say_message = f"You selected {spec}. Please select a doctor. "
        for i, doctor in enumerate(doctors):
            say_message += f"For Doctor {doctor.name}, press {i + 1}. "
        gather.say(say_message)
        response.redirect(f'/api/ivr/handle-specialization/{clinic.id}/')
    except (ValueError, IndexError, TypeError, Clinic.DoesNotExist):
        response.say("Invalid choice or error.")
        response.redirect(f'/api/ivr/handle-booking-type/{clinic_id}/')
    except Exception as e:
         print(f"Error in ivr_handle_specialization: {e}")
         response.say("An application error occurred. Goodbye.")
         response.hangup()
    # --- FIX: Return raw TwiML to prevent AttributeError in middleware ---
    return HttpResponse(str(response), content_type='text/xml')

@csrf_exempt
def ivr_handle_doctor(request, clinic_id, spec):
    choice = request.POST.get('Digits')
    response = VoiceResponse()
    caller_phone_number = request.POST.get('From', None)
    if not caller_phone_number:
         response.say("We could not identify your phone number. Cannot proceed with booking. Goodbye.")
         response.hangup()
         return HttpResponse(str(response), content_type='text/xml')
    try:
        clinic = Clinic.objects.get(id=clinic_id) 
        doctor = Doctor.objects.filter(clinic_id=clinic_id, specialization=spec)[int(choice) - 1]
        final_response = create_and_speak_token(response, doctor, caller_phone_number)
        # --- FIX: Return the TwiML response object directly from the helper function's return ---
        return final_response
    except (ValueError, IndexError, TypeError):
        response.say("Invalid choice.")
        response.redirect(f'/api/ivr/handle-specialization/{clinic_id}/') 
    except Clinic.DoesNotExist: 
         response.say("Clinic not found. Please start over.")
         response.redirect('/api/ivr/welcome/')
    except Exception as e:
         print(f"Error in ivr_handle_doctor: {e}")
         response.say("An application error occurred during doctor selection. Please try again.")
         response.redirect(f'/api/ivr/handle-specialization/{clinic_id}/')
    # --- FIX: Return raw TwiML to prevent AttributeError in middleware ---
    return HttpResponse(str(response), content_type='text/xml')


# --- SMS CANCELLATION LOGIC ---
@csrf_exempt
def handle_incoming_sms(request):
    from_number = request.POST.get('From', None)
    body = request.POST.get('Body', '').strip().upper()
    response = VoiceResponse() # Using VoiceResponse to generate TwiML SMS reply
    if from_number and body == 'CANCEL':
        today = timezone.now().date()
        try:
            patient = Patient.objects.get(phone_number=from_number)
            active_token = Token.objects.filter(
                patient=patient, date=today, status__in=['waiting', 'confirmed']
            ).latest('created_at')
            active_token.status = 'cancelled'
            active_token.save(update_fields=['status']) # Only update status
            message = "Your appointment for today has been successfully cancelled. Thank you."
            response.message(message)
            print(f"Cancelled appointment for {from_number} via SMS.")
        except Patient.DoesNotExist:
            message = "We could not find an account associated with your phone number."
            response.message(message)
            print(f"Received 'CANCEL' from unknown number: {from_number}")
        except Token.DoesNotExist:
            message = "You do not have an active appointment scheduled for today to cancel."
            response.message(message)
            print(f"Received 'CANCEL' from {from_number}, but no active token was found.")
        except Exception as e:
            print(f"Error processing SMS cancellation for {from_number}: {e}")
            message = "We're sorry, an error occurred while trying to cancel your appointment. Please contact the clinic directly."
            response.message(message)
    else:
        print(f"Received non-cancellation SMS from {from_number}: '{body}' - No action taken.")
        pass 
    # --- FIX: Return raw TwiML to prevent AttributeError in middleware ---
    return HttpResponse(str(response), content_type='text/xml')
