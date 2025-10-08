from django.db import migrations
from django.contrib.auth import get_user_model

def create_superuser(apps, schema_editor):
    User = get_user_model()
    if not User.objects.filter(username='admin').exists():
        User.objects.create_superuser(
            username='admin',
            email='admin@example.com',
            password='YOUR_SECURE_PASSWORD_HERE' # <-- CHOOSE A STRONG PASSWORD
        )

class Migration(migrations.Migration):

    dependencies = [
        ('api', '0001_initial'), # This should match your previous migration file
    ]

    operations = [
        migrations.RunPython(create_superuser),
    ]