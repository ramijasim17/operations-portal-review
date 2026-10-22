from django import forms
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
import re

from approvals import services as approvals
from personnel.models import Personnel

from .models import Profile


def _name_tokens(text):
    return set(re.findall(r"[^\W_]+", (text or "").casefold()))


class SignupForm(forms.Form):
    employee_id = forms.CharField(
        max_length=50, label="Employee ID",
        help_text="Your ID exactly as it appears in the personnel records.",
    )
    username = forms.CharField(max_length=150)
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput, min_length=8)
    password_confirm = forms.CharField(widget=forms.PasswordInput)

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        existing = User.objects.filter(username__iexact=username).first()
        if existing and existing.is_active:
            raise forms.ValidationError("This username is already taken.")
        return username

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        existing = User.objects.filter(email__iexact=email).first()
        if existing and existing.is_active:
            raise forms.ValidationError("An account with this email already exists.")
        allowed = settings.SIGNUP_ALLOWED_DOMAINS  # optional extra limit; empty = any domain
        domain = email.rsplit("@", 1)[-1]
        if allowed and domain not in allowed:
            raise forms.ValidationError(
                "Sign-up is limited to company email addresses (%s)." % ", ".join("@" + d for d in allowed)
            )
        return email

    def clean_password(self):
        password = self.cleaned_data["password"]
        validate_password(password)  # the rules in AUTH_PASSWORD_VALIDATORS
        return password

    def clean_employee_id(self):
        return self.cleaned_data["employee_id"].strip()

    def clean(self):
        cleaned = super().clean()
        self.personnel = None
        emp_id = cleaned.get("employee_id")
        if emp_id:
            person = Personnel.objects.filter(employee_id__iexact=emp_id).first()
            if person is None:
                self.add_error("employee_id", "That Employee ID isn't in the personnel records. Check it, or ask your administrator.")
            elif person.status != "Active":
                self.add_error("employee_id", "This employee record isn't active. Please contact your administrator.")
            elif not approvals.is_approved(person, "personnel"):
                self.add_error("employee_id", "This employee record is still waiting for approval. Please try again once your administrator has approved it.")
            elif Profile.objects.filter(employee=person, user__is_active=True).exists():
                self.add_error("employee_id", "This Employee ID already has an account. Try signing in, or reset your password.")
            else:
                self.personnel = person
        if cleaned.get("password") and cleaned.get("password_confirm") and cleaned["password"] != cleaned["password_confirm"]:
            raise forms.ValidationError("Passwords don't match.")
        return cleaned


class VerifyCodeForm(forms.Form):
    code = forms.CharField(
        max_length=6, min_length=6,
        widget=forms.TextInput(attrs={"placeholder": "6-digit code", "autocomplete": "one-time-code", "inputmode": "numeric"}),
    )
