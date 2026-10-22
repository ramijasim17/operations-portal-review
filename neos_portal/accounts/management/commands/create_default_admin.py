"""
Custom management command: creates one initial Admin account from
environment variables, safely re-runnable on every deploy.

Why this exists: Render's Free tier has no Shell/SSH access, so the normal
`python manage.py createsuperuser` (which asks interactive questions) can't
be run there at all. This command reads the same information from
environment variables instead, and does nothing if that account already
exists — so it's safe to run automatically on every single deploy (as part
of the build command) rather than as a manual one-time step.

Usage: python manage.py create_default_admin
Requires these environment variables to be set:
    ADMIN_USERNAME, ADMIN_EMAIL, ADMIN_PASSWORD
If they're not set, the command does nothing (skips silently) rather than
failing the whole deploy.
"""

import os
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create the initial admin account from ADMIN_USERNAME/ADMIN_EMAIL/ADMIN_PASSWORD env vars, if it doesn't already exist."

    def handle(self, *args, **options):
        username = os.environ.get("ADMIN_USERNAME")
        email = os.environ.get("ADMIN_EMAIL", "")
        password = os.environ.get("ADMIN_PASSWORD")

        if not username or not password:
            self.stdout.write("ADMIN_USERNAME/ADMIN_PASSWORD not set — skipping admin creation.")
            return

        if User.objects.filter(username=username).exists():
            self.stdout.write(f"Admin user '{username}' already exists — skipping.")
            return

        user = User.objects.create_superuser(username=username, email=email, password=password)
        user.profile.role = "admin"
        user.profile.save()
        self.stdout.write(self.style.SUCCESS(f"Created admin user '{username}'."))
