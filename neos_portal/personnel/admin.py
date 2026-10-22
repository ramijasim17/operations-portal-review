from django.contrib import admin

from .models import Personnel, PersonnelCert, TrainingCourse

admin.site.register(Personnel)
admin.site.register(TrainingCourse)
admin.site.register(PersonnelCert)
