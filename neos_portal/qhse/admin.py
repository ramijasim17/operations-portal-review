from django.contrib import admin

from .models import Incident, Inspection

admin.site.register(Incident)
admin.site.register(Inspection)
