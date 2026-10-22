from django.db import models


class DocumentType(models.Model):
    """Replaces the old fixed-choices field so types can be added and
    edited from the app itself instead of needing a code change."""
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class PublishingOrganization(models.Model):
    """Manageable list of organizations that can publish a controlled
    document -- add as many as needed (Client A, Client C, CLIENT B, Client D, ...)
    rather than a fixed set."""
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ControlledDocument(models.Model):
    name = models.CharField(max_length=255)
    document_type = models.ForeignKey(
        DocumentType, on_delete=models.PROTECT, related_name="documents",
        null=True, blank=True,
    )
    document_number = models.CharField(max_length=100, blank=True, default="")
    version = models.CharField(max_length=50, blank=True, default="")
    published_by = models.ForeignKey(
        PublishingOrganization, on_delete=models.PROTECT, related_name="documents",
        null=True, blank=True,
    )
    date_of_issue = models.DateField(null=True, blank=True)
    file = models.FileField(upload_to="controlled_documents/")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_of_issue", "name"]

    def __str__(self):
        return f"{self.document_number} \u2014 {self.name}" if self.document_number else self.name
