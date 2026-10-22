from django.contrib import admin

from .models import WAFileType, WARecord

admin.site.register(WAFileType)
admin.site.register(WARecord)
