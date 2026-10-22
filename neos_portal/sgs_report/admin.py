from django.contrib import admin

from .models import (
    Battery,
    BatteryUsage,
    Gauge,
    SGSImage,
    SGSReport,
    SGSSequenceEntry,
    SGSShift,
    SGSStation,
)

admin.site.register(SGSReport)
admin.site.register(SGSShift)
admin.site.register(SGSSequenceEntry)
admin.site.register(SGSImage)
admin.site.register(SGSStation)
admin.site.register(Gauge)
admin.site.register(Battery)
admin.site.register(BatteryUsage)
