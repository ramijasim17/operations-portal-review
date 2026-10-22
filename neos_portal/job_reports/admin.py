from django.contrib import admin

from .models import JobReport, JobReportDocument

admin.site.register(JobReport)
admin.site.register(JobReportDocument)
