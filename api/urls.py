from django.urls import path
from . import views

urlpatterns = [
    # --- Public URLs ---
    path('public/clinics/', views.PublicClinicListView.as_view(), name='public-clinic-list'),

    # --- Analytics ---
    path('analytics/', views.ClinicAnalyticsView.as_view(), name='clinic-analytics'),

    # --- Patient Self-Service URLs ---
    path('register/patient/', views.PatientRegisterView.as_view(), name='patient-register'),
    # OTP URLs have been removed
    path('tokens/get_my_token/', views.GetPatientTokenView.as_view(), name='get-patient-token'),
    path('tokens/confirm_arrival/', views.ConfirmArrivalView.as_view(), name='confirm-arrival'),
    path('clinics_with_doctors/', views.ClinicWithDoctorsListView.as_view(), name='clinics-with-doctors'),
    path('tokens/patient_create/', views.PatientCreateTokenView.as_view(), name='patient-create-token'),
    path('tokens/patient_cancel/', views.PatientCancelTokenView.as_view(), name='patient-cancel-token'),
    path('patient/live-queue/<int:doctor_id>/', views.PatientLiveQueueView.as_view(), name='patient-live-queue'),
    path('doctors/<int:doctor_id>/available-slots/<str:date>/', views.AvailableSlotsView.as_view(), name='available-slots'),


    # --- Staff & General Login URLs ---
    path('login/', views.LoginView.as_view(), name='login'),
    path('login/staff/', views.StaffLoginView.as_view(), name='staff-login'),
    
    # --- Staff Dashboard URLs ---
    path('tokens/', views.TokenListCreate.as_view(), name='token-list-create'),
    path('doctors/', views.DoctorList.as_view(), name='doctor-list'),
    path('tokens/<int:id>/update_status/', views.TokenUpdateStatusView.as_view(), name='token-update-status'),


    # --- Patient History & Consultation URLs ---
    path('history/my_history/', views.MyHistoryView.as_view(), name='my-history'),
    path('history/<int:patient_id>/', views.PatientHistoryView.as_view(), name='patient-history'),
    path('consultations/create/', views.ConsultationCreateView.as_view(), name='consultation-create'),

    # --- Advanced IVR URLs ---
    path('ivr/welcome/', views.ivr_welcome, name='ivr-welcome'),
    path('ivr/handle-state/', views.ivr_handle_state, name='ivr-handle-state'),
    path('ivr/handle-district/<int:state_id>/', views.ivr_handle_district, name='ivr-handle-district'),
    path('ivr/handle-clinic/<int:district_id>/', views.ivr_handle_clinic, name='ivr-handle-clinic'),
    path('ivr/handle-booking-type/<int:clinic_id>/', views.ivr_handle_booking_type, name='ivr-handle-booking-type'),
    path('ivr/handle-specialization/<int:clinic_id>/', views.ivr_handle_specialization, name='ivr-handle-specialization'),
    path('ivr/handle-doctor/<int:clinic_id>/<str:spec>/', views.ivr_handle_doctor, name='ivr-handle-doctor'),
]