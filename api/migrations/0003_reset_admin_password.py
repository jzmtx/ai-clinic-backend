from django.db import migrations
from django.contrib.auth import get_user_model

def reset_password(apps, schema_editor):
    User = get_user_model()
    new_password = 'clinicadmin123'
    
    try:
        admin_user = User.objects.get(username='admin')
        admin_user.set_password(new_password)
        admin_user.save()
    except User.DoesNotExist:
        User.objects.create_superuser(
            username='admin',
            email='admin@example.com',
            password=new_password
        )

class Migration(migrations.Migration):

    dependencies = [
        ('api', '0002_create_superuser'),
    ]

    operations = [
        migrations.RunPython(reset_password),
    ]