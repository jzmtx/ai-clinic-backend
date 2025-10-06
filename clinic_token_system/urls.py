from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    # This line points any 'api/...' request to the api app's urls.
    path('api/', include('api.urls')),
]

