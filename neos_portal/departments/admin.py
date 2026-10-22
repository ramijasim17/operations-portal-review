from django.contrib import admin

from .models import (
    Asset,
    AssetCert,
    AssetFieldValue,
    CertType,
    Classification,
    ClassificationCertRequirement,
    Department,
    FieldDefinition,
)

admin.site.register(Department)
admin.site.register(Classification)
admin.site.register(FieldDefinition)
admin.site.register(Asset)
admin.site.register(AssetFieldValue)
admin.site.register(CertType)
admin.site.register(ClassificationCertRequirement)
admin.site.register(AssetCert)
