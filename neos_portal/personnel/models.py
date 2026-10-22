import datetime

from django.contrib.contenttypes.fields import GenericRelation
from django.db import models
from django.utils import timezone

STATUS_CHOICES = [("Active", "Active"), ("Inactive", "Inactive")]


class Personnel(models.Model):
    employee_id = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=255)
    position = models.CharField(max_length=150, blank=True, default="")
    department = models.CharField(max_length=150, blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="Active")
    required_courses = models.CharField(
        max_length=500, blank=True, default="",
        help_text="Legacy comma-separated list, kept for imported data. Use required_training_courses for new management.",
    )
    required_training_courses = models.ManyToManyField(
        "TrainingCourse", blank=True, related_name="required_by",
        help_text="Which courses this person is required to hold \u2014 managed from the Required Courses tab.",
    )

    approvals = GenericRelation("approvals.Approval")      # waiting / approved / rejected, see approvals/

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Personnel"

    def __str__(self):
        return self.name

    def required_courses_list(self):
        return [c.strip() for c in self.required_courses.split(",") if c.strip()]


class TrainingCourse(models.Model):
    course_name = models.CharField(max_length=150, unique=True)

    class Meta:
        ordering = ["course_name"]

    def __str__(self):
        return self.course_name


class PersonnelCert(models.Model):
    personnel = models.ForeignKey(Personnel, on_delete=models.CASCADE, related_name="certs")
    course = models.ForeignKey(TrainingCourse, on_delete=models.CASCADE)
    expiry_date = models.DateField(null=True, blank=True)
    file = models.FileField(upload_to="personnel_certs/", blank=True, null=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        unique_together = ("personnel", "course")

    def __str__(self):
        return f"{self.personnel.name} \u00b7 {self.course.course_name}"

    @property
    def status(self):
        if not self.expiry_date:
            return "na"
        today = timezone.localdate()
        if self.expiry_date < today:
            return "expired"
        if self.expiry_date <= today + datetime.timedelta(days=30):
            return "soon"
        return "valid"
