
from django.db import models
from django.conf import settings


class Category(models.TextChoices):
    UNITS = "Units", "Units"
    PCE = "PCE", "PCE"
    DOWN_HOLE_TOOLS = "Down Hole Tools", "Down Hole Tools"
    FLANGE = "Flange", "Flange"
    CROSSOVER = "Crossover", "Crossover"
    SAFETY_DEVICE = "Safety Device", "Safety Device"
    LIFTING_GEARS = "Lifting Gears", "Lifting Gears"
    INSTRUMENT = "Instrument", "Instrument"


LOCATION_CHOICES = [
    ("ABC Base", "ABC Base"), ("Unit-01", "Unit-01"), ("Unit-02", "Unit-02"),
    ("Unit-03", "Unit-03"), ("Workshop", "Workshop"), ("Other", "Other"),
]

IN_OPERATIONS_CHOICES = [("Yes", "Yes"), ("No", "No")]

CATEGORY_FIELDS = {
    Category.UNITS: ["plate_number", "engine_model", "power_rating", "fuel_type"],
    Category.PCE: ["working_pressure", "test_pressure", "connection_top", "connection_bottom", "max_od", "id_inch"],
    Category.DOWN_HOLE_TOOLS: ["max_od", "length_val", "fish_neck", "connection_top", "connection_bottom"],
    Category.FLANGE: ["size", "working_pressure", "connection_top", "connection_bottom"],
    Category.CROSSOVER: ["connection_top", "connection_bottom", "max_od", "id_inch", "length_val"],
    Category.SAFETY_DEVICE: ["working_pressure", "test_pressure"],
    Category.LIFTING_GEARS: ["swl"],
    Category.INSTRUMENT: ["calibration_date", "sensor_type", "battery_type"],
}

COMMON_FIELDS = ["manufacturer", "location", "in_operations"]


class CertType(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class CategoryCertRequirement(models.Model):
    category = models.CharField(max_length=50, choices=Category.choices)
    cert_type = models.ForeignKey(CertType, on_delete=models.CASCADE)

    class Meta:
        unique_together = ("category", "cert_type")


class Equipment(models.Model):
    serial_number = models.CharField(max_length=100, unique=True)
    category = models.CharField(max_length=50, choices=Category.choices)
    description = models.CharField(max_length=255, blank=True, default="")
    manufacturer = models.CharField(max_length=255, blank=True, default="")
    location = models.CharField(max_length=50, choices=LOCATION_CHOICES, blank=True, default="ABC Base")
    in_operations = models.CharField(max_length=3, choices=IN_OPERATIONS_CHOICES, blank=True, default="Yes")

    plate_number = models.CharField(max_length=100, blank=True, default="")
    engine_model = models.CharField(max_length=100, blank=True, default="")
    power_rating = models.CharField(max_length=100, blank=True, default="")
    fuel_type = models.CharField(max_length=100, blank=True, default="")

    working_pressure = models.CharField(max_length=100, blank=True, default="")
    test_pressure = models.CharField(max_length=100, blank=True, default="")
    connection_top = models.CharField(max_length=100, blank=True, default="")
    connection_bottom = models.CharField(max_length=100, blank=True, default="")

    max_od = models.CharField(max_length=100, blank=True, default="")
    id_inch = models.CharField(max_length=100, blank=True, default="")
    length_val = models.CharField(max_length=100, blank=True, default="")
    fish_neck = models.CharField(max_length=100, blank=True, default="")

    size = models.CharField(max_length=100, blank=True, default="")
    swl = models.CharField(max_length=100, blank=True, default="")

    calibration_date = models.CharField(max_length=100, blank=True, default="")
    sensor_type = models.CharField(max_length=100, blank=True, default="")
    battery_type = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        ordering = ["category", "serial_number"]
        verbose_name_plural = "Equipment"

    def __str__(self):
        return f"{self.serial_number} ({self.category})"

    def category_fields(self):
        return COMMON_FIELDS + CATEGORY_FIELDS.get(self.category, [])

    def required_cert_types(self):
        return CertType.objects.filter(categorycertrequirement__category=self.category)


def cert_upload_path(instance, filename):
    return f"equipment_certs/{instance.equipment.serial_number}/{instance.cert_type.name}/{filename}"


class EquipmentCert(models.Model):
    equipment = models.ForeignKey(Equipment, on_delete=models.CASCADE, related_name="certs")
    cert_type = models.ForeignKey(CertType, on_delete=models.CASCADE)
    cert_number = models.CharField(max_length=100, blank=True, default="")
    expiry_date = models.DateField(null=True, blank=True)
    file = models.FileField(upload_to=cert_upload_path, null=True, blank=True)
    uploaded_at = models.DateTimeField(auto_now=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
    )

    class Meta:
        unique_together = ("equipment", "cert_type")

    def __str__(self):
        return f"{self.equipment.serial_number} · {self.cert_type.name}"

    @property
    def status(self) -> str:
        if not self.file:
            return "missing"
        if not self.expiry_date:
            return "na"
        from datetime import date, timedelta
        today = date.today()
        if self.expiry_date < today:
            return "expired"
        if self.expiry_date <= today + timedelta(days=30):
            return "soon"
        return "valid"