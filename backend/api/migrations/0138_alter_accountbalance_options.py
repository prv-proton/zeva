# Generated by Django 3.2.18 on 2023-04-11 21:24

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0137_userprofile_keycloak_user_id'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='accountbalance',
            options={'managed': False},
        ),
    ]
