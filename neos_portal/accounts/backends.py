"""Let people sign in with whichever they remember: their Employee ID, their email, or the username they chose."""
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class FlexibleLoginBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        typed = (username or kwargs.get(get_user_model().USERNAME_FIELD) or "").strip()
        if not typed or password is None:
            return None
        user = self._find(typed)
        if user is not None and user.check_password(password) and self.user_can_authenticate(user):
            return user
        get_user_model()().set_password(password)          # same timing whether or not the account exists
        return None

    @staticmethod
    def _find(typed):
        User = get_user_model()
        found = User.objects.filter(username__iexact=typed).first()      # the username itself always wins
        if found is not None:
            return found
        for lookup in ({"profile__employee__employee_id__iexact": typed}, {"email__iexact": typed}):
            matches = list(User.objects.filter(**lookup)[:2])
            if len(matches) == 1:                                        # ambiguous (two accounts): don't guess
                return matches[0]
        return None
