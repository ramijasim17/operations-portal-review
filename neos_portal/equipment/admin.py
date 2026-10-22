from django.contrib import admin
from .models import Equipment, CertType, CategoryCertRequirement, EquipmentCert


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = ("serial_number", "category", "description", "location")
    list_filter = ("category",)
    search_fields = ("serial_number", "description")


@admin.register(CertType)
class CertTypeAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(CategoryCertRequirement)
class CategoryCertRequirementAdmin(admin.ModelAdmin):
    list_display = ("category", "cert_type")
    list_filter = ("category",)


@admin.register(EquipmentCert)
class EquipmentCertAdmin(admin.ModelAdmin):
    list_display = ("equipment", "cert_type", "expiry_date", "status", "uploaded_by", "uploaded_at")
    list_filter = ("cert_type",)
    search_fields = ("equipment__serial_number",)
