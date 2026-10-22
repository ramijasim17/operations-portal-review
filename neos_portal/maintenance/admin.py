from django.contrib import admin

from .models import MaintenanceLevel, MaintenanceRecord

admin.site.register(MaintenanceLevel)
admin.site.register(MaintenanceRecord)
