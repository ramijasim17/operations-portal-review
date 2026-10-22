from django.conf import settings
from django.db import models


def empty_board():
    return {"bg": "lined", "pages": [{"items": []}]}


class Board(models.Model):
    """A private handwritten / typed notes board. Everything drawn is kept as
    vector items (pen strokes, shapes, text) in `data`, so it can be reopened
    and edited later; exports are rendered from it by the browser."""
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="whiteboards")
    title = models.CharField(max_length=150, default="Untitled board")
    data = models.JSONField(default=empty_board)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.title

    @property
    def page_count(self):
        return len((self.data or {}).get("pages", [])) or 1
