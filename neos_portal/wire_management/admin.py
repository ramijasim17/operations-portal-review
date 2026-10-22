from django.contrib import admin

from .models import WireDrum, WireLogEntry, WireLogEvidence

admin.site.register(WireDrum)
admin.site.register(WireLogEntry)
admin.site.register(WireLogEvidence)
