from django.contrib import admin

from .models import SQBDocument, SQBJob, SQBTrackerEntry

admin.site.register(SQBJob)
admin.site.register(SQBDocument)
admin.site.register(SQBTrackerEntry)
