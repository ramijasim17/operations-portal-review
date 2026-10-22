from django.core.management.base import BaseCommand

from personnel.models import Personnel, TrainingCourse


class Command(BaseCommand):
    help = (
        "One-time helper: populates the new required_training_courses checkbox "
        "field from each person's existing comma-separated 'required_courses' "
        "text field, so the Certificates tab isn't empty on first use."
    )

    def handle(self, *args, **options):
        updated = 0
        for person in Personnel.objects.exclude(required_courses=""):
            names = [c.strip() for c in person.required_courses.split(",") if c.strip()]
            if not names:
                continue
            course_objs = []
            for name in names:
                course, _ = TrainingCourse.objects.get_or_create(course_name=name)
                course_objs.append(course)
            person.required_training_courses.set(course_objs)
            updated += 1

        self.stdout.write(self.style.SUCCESS(f"Set required courses for {updated} people."))
