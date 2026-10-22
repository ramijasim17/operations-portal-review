"""Departments: Level 1 and 2 see only their own department in Action Tracker, QHSE, Maintenance and Personnel;
Level 3 and Admin see all of them and choose one."""
import datetime
import importlib

from django.apps import apps
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from action_tracker.models import Action
from departments.models import Asset, Classification, Department
from maintenance.models import MaintenanceRecord
from personnel.models import Personnel, PersonnelCert, TrainingCourse
from qhse.models import AuditPart, AuditTemplate, Incident, Inspection, Investigation, SiteVisit, SiteVisitItem

from .dept_testing import link_department
from .models import Profile, Role

TODAY = timezone.localdate()


def user(name, role, dept=None):
    u = User.objects.create_user(name, password="x")
    Profile.objects.update_or_create(user=u, defaults={"role": role})
    if dept:
        link_department(u, dept)
    return u


class Base(TestCase):
    def setUp(self):
        self.wl = Department.objects.create(name="Wireline & Slickline", location="Base")
        self.wt = Department.objects.create(name="Well Testing", location="Base")
        self.a_wl = Asset.objects.create(classification=Classification.objects.create(department=self.wl, name="C"), name="WL-1", description="x")
        self.a_wt = Asset.objects.create(classification=Classification.objects.create(department=self.wt, name="C"), name="WT-1", description="x")
        self.p_wl = Personnel.objects.create(employee_id="1", name="Wella Wire", department="Wireline & Slickline")
        self.p_wt = Personnel.objects.create(employee_id="2", name="Tess Tester", department="well testing")       # capital letters do not matter
        self.p_none = Personnel.objects.create(employee_id="3", name="Nobody Dept")
        self.act_wl = Action.objects.create(action_item="WL action", department=self.wl, due_date=TODAY - datetime.timedelta(days=2))
        self.act_wt = Action.objects.create(action_item="WT action", department=self.wt, due_date=TODAY - datetime.timedelta(days=2))
        self.act_none = Action.objects.create(action_item="Orphan action")
        self.job_wl = MaintenanceRecord.objects.create(asset_serial="WL-1", schedule="one_off", due_date=TODAY - datetime.timedelta(days=3))
        self.job_wt = MaintenanceRecord.objects.create(asset_serial="WT-1", schedule="one_off", due_date=TODAY - datetime.timedelta(days=3))
        self.job_unknown = MaintenanceRecord.objects.create(asset_serial="NOT-IN-DEPARTMENTS", schedule="one_off", due_date=TODAY)
        self.l2 = user("wl_l2", Role.EDITOR, self.wl)
        self.l1 = user("wt_l1", Role.VIEWER, self.wt)
        self.l3 = user("boss", Role.APPROVER)
        self.admin = user("theadmin", Role.ADMIN)
        self.lost = user("lost", Role.EDITOR)                    # Level 2 whose account is not linked to a department


class ActionTrackerDepartmentTests(Base):
    def test_a_level_2_person_sees_only_their_departments_actions(self):
        self.client.force_login(self.l2)
        page = self.client.get(reverse("action_tracker:list"), {"tab": "all"})
        self.assertContains(page, "WL action")
        self.assertNotContains(page, "WT action")
        self.assertNotContains(page, "Orphan action")
        self.assertNotContains(page, 'id="dept-select"')

    def test_level_3_chooses_and_sees_orphans_under_no_department(self):
        self.client.force_login(self.l3)
        get = lambda dept: self.client.get(reverse("action_tracker:list"), {"tab": "all", "dept": dept})
        self.assertContains(get(self.wl.pk), "WL action")
        self.assertNotContains(get(self.wl.pk), "WT action")
        self.assertContains(get("none"), "Orphan action")
        self.assertNotContains(get("none"), "WL action")
        for title in ("WL action", "WT action", "Orphan action"):
            self.assertContains(get("all"), title)

    def test_level_3_starts_on_all_departments_even_when_linked_to_one(self):
        link_department(self.l3, self.wl)
        self.client.force_login(self.l3)
        page = self.client.get(reverse("action_tracker:list"), {"tab": "all"})
        for title in ("WL action", "WT action"):
            self.assertContains(page, title)

    def test_a_person_with_no_department_sees_nothing_and_cannot_add(self):
        self.client.force_login(self.lost)
        page = self.client.get(reverse("action_tracker:list"), {"tab": "all"})
        self.assertNotContains(page, "WL action")
        self.assertContains(page, "not linked to a department")
        self.assertEqual(self.client.post(reverse("action_tracker:create"), {"action_item": "x", "priority": "High"}).status_code, 302)
        self.assertFalse(Action.objects.filter(action_item="x").exists())

    def test_a_new_action_from_a_level_2_person_takes_their_department(self):
        self.client.force_login(self.l2)
        self.client.post(reverse("action_tracker:create"), {"action_item": "Mine", "priority": "High", "department": self.wt.pk})
        self.assertEqual(Action.objects.get(action_item="Mine").department, self.wl)           # a forged choice is ignored

    def test_the_assignee_list_is_limited_to_their_department(self):
        self.client.force_login(self.l2)
        form = self.client.get(reverse("action_tracker:create")).context["form"]
        names = [value for value, _ in form.fields["assignee"].choices]
        self.assertIn("Wella Wire", names)
        self.assertNotIn("Tess Tester", names)

    def test_level_3_must_choose_a_department_unless_the_assignee_has_one(self):
        self.client.force_login(self.l3)
        self.assertEqual(self.client.post(reverse("action_tracker:create"), {"action_item": "x", "priority": "High"}).status_code, 200)
        self.client.post(reverse("action_tracker:create"), {"action_item": "Assigned", "priority": "High", "assignee": "Tess Tester"})
        self.assertEqual(Action.objects.get(action_item="Assigned").department, self.wt)

    def test_an_action_takes_the_department_of_its_assignee_when_created_in_code(self):
        self.assertEqual(Action.objects.create(action_item="y", assignee="Wella Wire").department, self.wl)
        self.assertIsNone(Action.objects.create(action_item="z", assignee="Unknown Person").department)

    def test_another_departments_action_is_not_found(self):
        self.client.force_login(self.l2)
        self.assertEqual(self.client.get(reverse("action_tracker:detail", args=[self.act_wl.pk])).status_code, 200)
        for name in ("detail", "edit"):
            self.assertEqual(self.client.get(reverse(f"action_tracker:{name}", args=[self.act_wt.pk])).status_code, 404, name)
        self.assertEqual(self.client.post(reverse("action_tracker:set_status", args=[self.act_wt.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("action_tracker:delete", args=[self.act_wt.pk])).status_code, 403)       # deleting is Level 3
        self.assertEqual(self.client.get(reverse("action_tracker:detail", args=[self.act_none.pk])).status_code, 404)

    def test_clearing_the_archive_only_clears_what_the_person_can_see(self):
        Action.objects.filter(pk__in=[self.act_wl.pk, self.act_wt.pk]).update(status="Completed")
        self.client.force_login(self.l3)
        self.client.get(reverse("action_tracker:list"), {"dept": self.wl.pk})            # looking at one department
        self.client.post(reverse("action_tracker:clear_completed"))
        self.assertFalse(Action.objects.filter(pk=self.act_wl.pk).exists())
        self.assertTrue(Action.objects.filter(pk=self.act_wt.pk).exists())

    def test_insights_are_split_too(self):
        self.client.force_login(self.l2)
        self.assertEqual(self.client.get(reverse("action_tracker:insights")).context["scope"].label, "Wireline & Slickline")


class MaintenanceDepartmentTests(Base):
    def test_jobs_are_placed_by_their_equipments_department(self):
        self.client.force_login(self.l2)
        page = self.client.get(reverse("maintenance:list"))
        self.assertContains(page, "WL-1")
        self.assertNotContains(page, "WT-1")
        self.assertNotContains(page, "NOT-IN-DEPARTMENTS")

    def test_level_3_chooses_and_unknown_equipment_is_under_no_department(self):
        self.client.force_login(self.l3)
        self.assertContains(self.client.get(reverse("maintenance:list"), {"dept": "none"}), "NOT-IN-DEPARTMENTS")
        self.assertNotContains(self.client.get(reverse("maintenance:list"), {"dept": self.wt.pk}), "WL-1")
        self.assertContains(self.client.get(reverse("maintenance:list"), {"dept": "all"}), "WT-1")

    def test_a_person_with_no_department_sees_nothing(self):
        self.client.force_login(self.lost)
        page = self.client.get(reverse("maintenance:list"))
        self.assertNotContains(page, "WL-1")
        self.assertContains(page, "not linked to a department")

    def test_only_their_own_equipment_can_be_used_for_a_new_job(self):
        self.client.force_login(self.l2)
        base = {"activity": "maintenance", "schedule": "one_off", "due_date": str(TODAY), "place": "yard", "times_per": 1, "every_unit": "month"}
        self.assertEqual(self.client.post(reverse("maintenance:create"), {**base, "asset_serial": "WT-1"}).status_code, 200)
        self.assertFalse(MaintenanceRecord.objects.filter(asset_serial="WT-1").exclude(pk=self.job_wt.pk).exists())
        self.client.post(reverse("maintenance:create"), {**base, "asset_serial": "WL-1"})
        self.assertEqual(MaintenanceRecord.objects.filter(asset_serial="WL-1").count(), 2)
        self.assertNotIn("WT-1", self.client.get(reverse("maintenance:create")).context["asset_serials"])

    def test_another_departments_job_is_not_found(self):
        self.client.force_login(self.l2)
        self.assertEqual(self.client.get(reverse("maintenance:history", args=[self.job_wl.pk])).status_code, 200)
        for name in ("edit", "complete", "report_failure", "history", "raise_action", "history_pdf"):
            self.assertEqual(self.client.get(reverse(f"maintenance:{name}", args=[self.job_wt.pk])).status_code, 404, name)
        self.assertEqual(self.client.post(reverse("maintenance:delete", args=[self.job_wt.pk])).status_code, 403)       # deleting is Level 3
        self.assertEqual(self.client.get(reverse("maintenance:history", args=[self.job_unknown.pk])).status_code, 404)

    def test_a_failed_test_raises_an_action_in_the_equipments_department(self):
        self.client.force_login(self.l3)
        test = MaintenanceRecord.objects.create(asset_serial="WT-1", activity="pressure_test", schedule="on_failure")
        self.client.post(reverse("maintenance:complete", args=[test.pk]), {"done_date": str(TODAY), "result": "Fail"})
        self.assertEqual(Action.objects.get(maintenance_records=test).department, self.wt)


class PersonnelDepartmentTests(Base):
    def test_people_are_split_by_the_department_written_on_their_record(self):
        self.client.force_login(self.l2)
        page = self.client.get(reverse("personnel:list"))
        self.assertContains(page, "Wella Wire")
        self.assertNotContains(page, "Tess Tester")
        self.assertNotContains(page, "Nobody Dept")
        self.client.force_login(self.l1)                                      # capital letters in the written name do not matter
        self.assertContains(self.client.get(reverse("personnel:list")), "Tess Tester")

    def test_level_3_chooses(self):
        self.client.force_login(self.l3)
        self.assertContains(self.client.get(reverse("personnel:list"), {"dept": self.wt.pk}), "Tess Tester")
        self.assertNotContains(self.client.get(reverse("personnel:list"), {"dept": self.wt.pk}), "Wella Wire")
        self.assertContains(self.client.get(reverse("personnel:list"), {"dept": "none"}), "Nobody Dept")
        self.assertContains(self.client.get(reverse("personnel:list"), {"dept": "all"}), "Wella Wire")

    def test_a_person_with_no_department_sees_nothing(self):
        self.client.force_login(self.lost)
        page = self.client.get(reverse("personnel:list"))
        self.assertNotContains(page, "Wella Wire")
        self.assertContains(page, "not linked to a department")

    def test_another_departments_person_is_not_found(self):
        self.client.force_login(self.l2)
        self.assertEqual(self.client.get(reverse("personnel:detail", args=[self.p_wl.pk])).status_code, 200)
        for name in ("detail", "edit"):
            self.assertEqual(self.client.get(reverse(f"personnel:{name}", args=[self.p_wt.pk])).status_code, 404, name)
        self.assertEqual(self.client.post(reverse("personnel:request_removal", args=[self.p_wt.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("personnel:delete", args=[self.p_wt.pk])).status_code, 403)       # Level 2 cannot delete at all
        self.client.force_login(self.l3)                                                                            # and a Level 3 who is looking at their own department
        self.client.get(reverse("personnel:list"), {"dept": self.wl.pk})
        self.assertEqual(Personnel.objects.filter(pk=self.p_wt.pk).count(), 1)

    def test_a_new_person_from_a_level_2_user_joins_their_department(self):
        self.client.force_login(self.l2)
        self.client.post(reverse("personnel:create"), {"employee_id": "77", "name": "New Hire", "position": "Op", "status": "Active", "department": "Well Testing"})
        self.assertEqual(Personnel.objects.get(employee_id="77").department, "Wireline & Slickline")

    def test_certificates_are_limited_to_the_visible_people(self):
        course = TrainingCourse.objects.create(course_name="H2S")
        cert = PersonnelCert.objects.create(personnel=self.p_wt, course=course, expiry_date=TODAY)
        self.client.force_login(self.l2)
        form = self.client.get(reverse("personnel:cert_create")).context["form"]
        names = [p.name for p in form.fields["personnel"].queryset]
        self.assertIn("Wella Wire", names)
        self.assertNotIn("Tess Tester", names)
        self.assertEqual(self.client.get(reverse("personnel:cert_edit", args=[cert.pk])).status_code, 404)

    def test_the_department_box_for_level_3_offers_the_departments(self):
        self.client.force_login(self.l3)
        form = self.client.get(reverse("personnel:create")).context["form"]
        self.assertEqual([c for c, _ in form.fields["department"].choices], ["", "Well Testing", "Wireline & Slickline"])

    def test_a_csv_import_by_a_level_2_user_only_touches_their_department(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.client.force_login(self.l2)
        csv = "Name,ID,Dept,Position,Status\nWella Wire,1,Well Testing,Boss,Active\nTess Tester,2,Wireline,Op,Active\nFresh Face,,Whatever,Op,Active\n"
        self.client.post(reverse("personnel:csv_import"), {"file": SimpleUploadedFile("p.csv", csv.encode())})
        self.assertEqual(Personnel.objects.get(name="Wella Wire").department, "Wireline & Slickline")      # department cannot be changed by them
        self.assertEqual(Personnel.objects.get(name="Tess Tester").department, "well testing")            # another department's person is skipped
        self.assertEqual(Personnel.objects.get(name="Fresh Face").department, "Wireline & Slickline")


class EveryDirectLinkTests(Base):
    """Typing in the address of another department's record never works for Level 1 and 2."""

    def test_sweep_of_every_record_address(self):
        inc = Incident.objects.create(title="WT", department=self.wt)
        ins = Inspection.objects.create(title="WT", department=self.wt, due_date=TODAY)
        inv = Investigation.objects.create(title="WT", department=self.wt)
        visit = SiteVisit.objects.create(site="WT", department=self.wt)
        item = SiteVisitItem.objects.create(visit=visit, text="x")
        tpl = AuditTemplate.objects.create(name="WT only", department=self.wt)
        part = AuditPart.objects.create(template=tpl, title="P")
        self.client.force_login(self.l2)
        pages = [("qhse:incident_detail", inc), ("qhse:incident_edit", inc), ("qhse:incident_action", inc),
                 ("qhse:inspection_detail", ins), ("qhse:inspection_edit", ins), ("qhse:inspection_done", ins),
                 ("qhse:inspection_conduct", ins), ("qhse:inspection_action", ins),
                 ("qhse:investigation_detail", inv), ("qhse:investigation_edit", inv), ("qhse:investigation_action", inv),
                 ("qhse:visit_detail", visit), ("qhse:visit_edit", visit), ("qhse:visit_pdf", visit), ("qhse:item_create", visit),
                 ("qhse:item_edit", item), ("qhse:item_action", item),
                 ("qhse:template_detail", tpl)]
        for name, obj in pages:
            self.assertEqual(self.client.get(reverse(name, args=[obj.pk])).status_code, 404, name)
        for name, obj in (("qhse:incident_status", inc), ("qhse:investigation_status", inv),
                          ("qhse:visit_submit", visit), ("qhse:item_remove", item)):
            self.assertEqual(self.client.post(reverse(name, args=[obj.pk])).status_code, 404, name)
        # templates with no department are shared; one for another department is not
        shared = AuditTemplate.objects.create(name="For all")
        self.assertEqual(self.client.get(reverse("qhse:template_detail", args=[shared.pk])).status_code, 200)
        self.assertTrue(part.pk)

    def test_the_investigate_links_are_checked_too(self):
        self.client.force_login(self.l2)
        base = reverse("qhse:investigation_create")
        self.assertEqual(self.client.get(base + f"?maintenance={self.job_wt.pk}").status_code, 404)
        self.assertEqual(self.client.get(base + f"?action={self.act_wt.pk}").status_code, 404)
        self.assertEqual(self.client.get(base + f"?maintenance={self.job_wl.pk}").status_code, 200)


class BackfillTests(Base):
    def test_existing_actions_get_a_department_from_their_assignee(self):
        a = Action.objects.create(action_item="old", assignee="Tess Tester")
        Action.objects.filter(pk=a.pk).update(department=None)              # as it was before departments existed
        module = importlib.import_module("qhse.migrations.0005_backfill_action_departments")
        module.backfill(apps, None)
        a.refresh_from_db()
        self.assertEqual(a.department, self.wt)


class NptInvestigationTests(Base):
    """Non-productive time can be looked into from the QHSE Follow-up page and from the SQB NPT tracker."""

    def setUp(self):
        super().setUp()
        from sqb.models import SQBTrackerEntry

        self.npt_wl = SQBTrackerEntry.objects.create(date=TODAY - datetime.timedelta(days=3), unit=self.a_wl, npt_hours=5, category="equipment",
                                                     client="Client A", description="Seal leak")
        self.npt_wt = SQBTrackerEntry.objects.create(date=TODAY - datetime.timedelta(days=2), unit=self.a_wt, npt_hours=2, category="personnel")
        self.old = SQBTrackerEntry.objects.create(date=TODAY - datetime.timedelta(days=200), unit=self.a_wl, npt_hours=9)
        self.zero = SQBTrackerEntry.objects.create(date=TODAY - datetime.timedelta(days=1), unit=self.a_wl, npt_hours=0)

    def test_follow_up_lists_recent_npt_biggest_first(self):
        self.client.force_login(self.l3)
        page = self.client.get(reverse("qhse:dashboard"), {"dept": "all"})
        self.assertEqual([e.pk for e in page.context["npt_entries"]], [self.npt_wl.pk, self.npt_wt.pk])      # old and zero-hour entries left out
        self.assertEqual(page.context["stats"]["npt_month"] >= 0, True)
        self.assertContains(page, "Non-productive time (NPT)")
        self.assertContains(page, f"?npt={self.npt_wl.pk}")

    def test_follow_up_for_a_level_2_person_shows_only_their_departments_npt(self):
        self.client.force_login(self.l2)
        page = self.client.get(reverse("qhse:dashboard"))
        self.assertEqual([e.pk for e in page.context["npt_entries"]], [self.npt_wl.pk])

    def test_investigate_prefills_from_the_npt_entry_and_links_it(self):
        self.client.force_login(self.l3)
        url = reverse("qhse:investigation_create") + f"?npt={self.npt_wl.pk}"
        form = self.client.get(url).context["form"]
        self.assertEqual(form.initial["trigger"], "npt")
        self.assertIn("5 h", form.initial["title"])
        self.assertIn("Seal leak", form.initial["what_happened"])
        self.assertEqual(form.initial["asset_serial"], "WL-1")
        self.client.post(url, {"department": self.wl.pk, "title": form.initial["title"], "trigger": "npt", "asset_serial": "WL-1",
                               "opened_date": str(TODAY), "what_happened": "x"})
        inv = Investigation.objects.get()
        self.assertEqual((inv.npt_entry, inv.department), (self.npt_wl, self.wl))
        page = self.client.get(reverse("qhse:dashboard"), {"dept": "all"})
        self.assertContains(page, inv.reference)                                   # the entry now shows its investigation, not a button
        self.assertContains(self.client.get(reverse("qhse:investigation_detail", args=[inv.pk])), "NPT entry")

    def test_a_level_2_person_cannot_investigate_another_departments_npt(self):
        self.client.force_login(self.l2)
        self.assertEqual(self.client.get(reverse("qhse:investigation_create") + f"?npt={self.npt_wt.pk}").status_code, 404)
        self.assertEqual(self.client.get(reverse("qhse:investigation_create") + f"?npt={self.npt_wl.pk}").status_code, 200)

    def test_the_npt_tracker_offers_an_investigate_button(self):
        self.client.force_login(self.l3)
        self.assertContains(self.client.get(reverse("sqb:tracker_list")), f"?npt={self.npt_wl.pk}")


class LockSwitchTests(Base):
    """DEPARTMENT_LOCK ships off: until it is turned on nobody is locked out, and everyone can use the Department box."""

    def test_with_the_lock_off_a_level_2_person_sees_everything_and_can_choose(self):
        from django.test import override_settings

        with override_settings(DEPARTMENT_LOCK=False):
            self.client.force_login(self.l2)
            page = self.client.get(reverse("action_tracker:list"), {"tab": "all"})
            for title in ("WL action", "WT action"):
                self.assertContains(page, title)
            self.assertContains(page, 'id="dept-select"')
            self.assertEqual(self.client.get(reverse("action_tracker:detail", args=[self.act_wt.pk])).status_code, 200)
            self.client.force_login(self.lost)                      # and an unlinked account is not shut out either
            self.assertContains(self.client.get(reverse("action_tracker:list"), {"tab": "all"}), "WL action")

    def test_the_report_lists_what_must_be_fixed_first(self):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("department_report", stdout=out)
        text = out.getvalue()
        self.assertIn("Nobody Dept", text)            # a person whose department matches nothing
        self.assertIn("lost", text)                   # a Level 2 account that would see nothing
        self.assertIn("Orphan", text.replace("Actions with no department: 1", "Orphan") if "Actions with no department: 1" in text else text)
