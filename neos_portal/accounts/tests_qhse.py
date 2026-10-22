import datetime

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.dept_testing import link_department, make_action, test_department
from accounts.models import Profile, Role
from action_tracker.models import Action
from qhse.models import Incident, Inspection, Investigation, SiteVisit


def user(name, role):
    u = User.objects.create_user(name, password="x")
    Profile.objects.update_or_create(user=u, defaults={"role": role})
    link_department(u)                      # Level 1 / 2 see only their own department
    return u


def _with_dept(model):
    def make(**kw):
        kw.setdefault("department", test_department())
        return model.objects.create(**kw)
    return make


mk_incident, mk_inspection, mk_investigation = _with_dept(Incident), _with_dept(Inspection), _with_dept(Investigation)


class QhseModelTests(TestCase):
    def test_references_count_up_each_year_and_closing_stamps_the_date(self):
        a = mk_incident(title="A", date=datetime.date(2026, 3, 1))
        b = mk_incident(title="B", date=datetime.date(2026, 4, 1))
        c = mk_incident(title="C", date=datetime.date(2027, 1, 1))
        self.assertEqual((a.reference, b.reference, c.reference), ("INC-2026-001", "INC-2026-002", "INC-2027-001"))
        self.assertIsNone(a.closed_date)
        a.status = Incident.CLOSED
        a.save()
        self.assertEqual(a.closed_date, timezone.localdate())
        a.status = Incident.OPEN
        a.save()
        self.assertIsNone(a.closed_date)

    def test_inspection_status_and_repeat(self):
        today = timezone.localdate()
        late = mk_inspection(title="Yard", due_date=today - datetime.timedelta(days=3), repeat_months=1)
        self.assertEqual((late.status, late.status_text), ("overdue", "3 days overdue"))
        soon = mk_inspection(title="Soon", due_date=today + datetime.timedelta(days=2))
        self.assertEqual(soon.status, "soon")
        nxt = late.complete(datetime.date(2026, 1, 31), "Pass")
        self.assertEqual(nxt.due_date, datetime.date(2026, 2, 28))        # month-end is clamped
        self.assertEqual((late.status, late.result), ("done", "Pass"))
        self.assertIsNone(Inspection.objects.get(pk=soon.pk).complete(today, "Pass"))


class QhseViewTests(TestCase):
    def setUp(self):
        self.editor = user("ed", Role.EDITOR)
        self.viewer = user("vw", Role.VIEWER)
        self.approver = user("ap", Role.APPROVER)
        from departments.models import Department

        self.dept = test_department()
        self.client.force_login(self.editor)

    def test_every_page_renders(self):
        inc = mk_incident(title="Spill", severity="High")
        ins = mk_inspection(title="Yard", due_date=timezone.localdate())
        for url in (reverse("qhse:dashboard"), reverse("qhse:incident_list"), reverse("qhse:inspection_list"),
                    reverse("qhse:incident_detail", args=[inc.pk]), reverse("qhse:inspection_detail", args=[ins.pk]),
                    reverse("qhse:incident_create"), reverse("qhse:incident_edit", args=[inc.pk]),
                    reverse("qhse:inspection_create"), reverse("qhse:inspection_done", args=[ins.pk]),
                    reverse("qhse:incident_action", args=[inc.pk]), reverse("qhse:inspection_action", args=[ins.pk]),
                    reverse("qhse:incident_list") + "?tab=all&severity=High&q=spill",
                    reverse("qhse:inspection_list") + "?tab=all&type=audit"):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_menu_has_the_tab(self):
        self.assertContains(self.client.get(reverse("core:dashboard")), "QHSE")

    def test_report_an_incident(self):
        r = self.client.post(reverse("qhse:incident_create"), {
            "department": self.dept.pk, "date": timezone.localdate().isoformat(), "incident_type": "near_miss", "severity": "Medium",
            "title": "Dropped tool", "description": "x"})
        inc = Incident.objects.get()
        self.assertRedirects(r, reverse("qhse:incident_detail", args=[inc.pk]))
        self.assertEqual((inc.reported_by, inc.status), (self.editor, "Open"))
        future = self.client.post(reverse("qhse:incident_create"), {
            "date": (timezone.localdate() + datetime.timedelta(days=2)).isoformat(), "incident_type": "incident",
            "severity": "Low", "title": "x"})
        self.assertEqual(future.status_code, 200)

    def test_raise_a_corrective_action_links_it_to_the_action_tracker(self):
        inc = mk_incident(title="Spill", severity="Critical", client="Client A")
        self.client.post(reverse("qhse:incident_action", args=[inc.pk]), {
            "action_item": "Fix hose", "priority": "High", "assignee": ""})
        action = Action.objects.get()
        self.assertEqual((action.priority, action.client, action.action_type), ("High", "Client A", "procedure"))
        self.assertEqual(list(inc.actions.all()), [action])
        self.assertContains(self.client.get(reverse("qhse:incident_detail", args=[inc.pk])), "Fix hose")

    def test_mark_inspection_done_schedules_the_next(self):
        ins = mk_inspection(title="Yard", due_date=timezone.localdate(), repeat_months=3)
        self.client.post(reverse("qhse:inspection_done", args=[ins.pk]), {
            "done_date": timezone.localdate().isoformat(), "result": "Fail", "findings": "bad"})
        ins.refresh_from_db()
        self.assertEqual((ins.result, ins.findings), ("Fail", "bad"))
        self.assertEqual(Inspection.objects.count(), 2)

    def test_status_buttons(self):
        inc = mk_incident(title="Spill")
        self.client.post(reverse("qhse:incident_status", args=[inc.pk]), {"status": "Closed"})
        inc.refresh_from_db()
        self.assertEqual(inc.status, "Closed")
        self.client.post(reverse("qhse:incident_status", args=[inc.pk]), {"status": "nonsense"})
        inc.refresh_from_db()
        self.assertEqual(inc.status, "Closed")

    def test_permissions(self):
        inc = mk_incident(title="Spill")
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(reverse("qhse:dashboard")).status_code, 200)
        self.assertEqual(self.client.get(reverse("qhse:incident_create")).status_code, 403)
        self.assertEqual(self.client.post(reverse("qhse:incident_status", args=[inc.pk]), {"status": "Closed"}).status_code, 403)
        self.client.force_login(self.editor)
        self.assertEqual(self.client.post(reverse("qhse:incident_delete", args=[inc.pk])).status_code, 403)   # delete is Level 3
        self.client.force_login(self.approver)
        self.client.post(reverse("qhse:incident_delete", args=[inc.pk]))
        self.assertFalse(Incident.objects.exists())

    def test_seed_command_fills_the_pages(self):
        from django.core.management import call_command
        call_command("seed_qhse_sample_data", verbosity=0)
        self.assertEqual((Incident.objects.count(), Inspection.objects.count()), (10, 10))
        self.assertEqual(self.client.get(reverse("qhse:dashboard")).status_code, 200)
        call_command("seed_qhse_sample_data", verbosity=0)                      # does nothing the second time
        self.assertEqual(Incident.objects.count(), 10)


class QhseMaintenanceActionLinkTests(TestCase):
    """The three tabs share data instead of repeating it: failures raise actions, equipment is linked, one overview page."""

    def setUp(self):
        from departments.models import Asset, Classification, Department
        from maintenance.models import MaintenanceRecord

        self.editor = user("ed2", Role.EDITOR)
        self.approver = user("ap2", Role.APPROVER)
        dept = test_department()
        self.asset = Asset.objects.create(classification=Classification.objects.create(department=dept, name="C"),
                                          name="PCE-1", description="Lubricator")
        self.test = MaintenanceRecord.objects.create(asset_serial="PCE-1", activity="pressure_test", schedule="on_failure")
        self.client.force_login(self.approver)       # a Level 3's report moves the schedule at once

    def test_a_failed_test_raises_one_high_action(self):
        url = reverse("maintenance:complete", args=[self.test.pk])
        self.client.post(url, {"done_date": "2026-05-10", "result": "Fail"})
        action = Action.objects.get()
        self.assertEqual((action.priority, action.action_type), ("High", "equipment"))
        self.assertIn("PCE-1", action.action_item)
        self.assertEqual(list(self.test.actions.all()), [action])
        self.client.post(url, {"done_date": "2026-05-11", "result": "Fail"})        # still open: no second one
        self.assertEqual(Action.objects.count(), 1)

    def test_a_pass_raises_nothing(self):
        self.client.post(reverse("maintenance:complete", args=[self.test.pk]), {"done_date": "2026-05-10", "result": "Pass"})
        self.assertFalse(Action.objects.exists())

    def test_a_reported_failure_raises_an_action(self):
        self.client.post(reverse("maintenance:report_failure", args=[self.test.pk]), {"date": "2026-05-10", "notes": "leak"})
        self.assertIn("Failure reported", Action.objects.get().action_item)

    def test_raise_an_action_by_hand_and_see_it_on_the_history_page(self):
        self.client.post(reverse("maintenance:raise_action", args=[self.test.pk]),
                         {"action_item": "PCE-1: replace seals", "priority": "Medium", "assignee": ""})
        page = self.client.get(reverse("maintenance:history", args=[self.test.pk]))
        self.assertContains(page, "replace seals")
        self.assertContains(page, "Asset overview")

    def test_equipment_is_checked_and_linked_both_ways(self):
        bad = self.client.post(reverse("qhse:inspection_create"), {
            "title": "Rig check", "kind": "inspection", "scope": "internal", "asset_serial": "NOPE", "due_date": "2026-12-01"})
        self.assertEqual(bad.status_code, 200)
        self.assertFalse(Inspection.objects.exists())
        self.client.post(reverse("qhse:inspection_create"), {
            "title": "Rig check", "kind": "inspection", "scope": "internal", "asset_serial": "PCE-1", "due_date": "2026-12-01"})
        self.client.post(reverse("qhse:incident_create"), {
            "date": timezone.localdate().isoformat(), "incident_type": "incident", "severity": "High",
            "title": "Hose burst", "asset_serial": "PCE-1"})
        incident = Incident.objects.get()
        page = self.client.get(reverse("qhse:incident_detail", args=[incident.pk]))
        self.assertContains(page, "PCE-1")
        self.assertContains(page, "Pressure test")                      # its maintenance job shows on the incident
        history = self.client.get(reverse("maintenance:history", args=[self.test.pk]))
        self.assertContains(history, "Hose burst")                      # and the incident shows on the maintenance page
        self.assertContains(history, "Rig check")

    def test_asset_overview_gathers_everything(self):
        mk_incident(title="Hose burst", asset_serial="PCE-1", severity="High")
        insp = mk_inspection(title="Rig check", asset_serial="PCE-1", due_date=timezone.localdate())
        action = make_action(action_item="Fix it")
        insp.actions.add(action)
        self.test.actions.add(make_action(action_item="Retest"))
        page = self.client.get(reverse("departments:asset_detail", args=[self.asset.pk]))
        self.assertEqual(page.status_code, 200)
        for text in ("Hose burst", "Rig check", "Fix it", "Retest", "Pressure test"):
            self.assertContains(page, text)
        self.assertEqual(page.context["open_actions"], 2)


class QhseFollowUpAndAuditTests(TestCase):
    def setUp(self):
        from departments.models import Asset, Classification, Department
        from maintenance.models import MaintenanceRecord

        self.l1 = user("l1", Role.VIEWER)
        self.l2 = user("l2", Role.EDITOR)
        self.l3 = user("l3", Role.APPROVER)
        dept = test_department()
        self.dept = dept
        Asset.objects.create(classification=Classification.objects.create(department=dept, name="C"), name="PCE-9", description="Lubricator")
        today = timezone.localdate()
        self.late = MaintenanceRecord.objects.create(asset_serial="PCE-9", schedule="one_off", due_date=today - datetime.timedelta(days=5))
        self.failed = MaintenanceRecord.objects.create(asset_serial="PCE-9", activity="pressure_test", schedule="repeating",
                                                       every_count=6, every_unit="month", due_date=today + datetime.timedelta(days=30),
                                                       last_done=today - datetime.timedelta(days=20), last_result="Fail")
        self.overdue_action = make_action(action_item="Late thing", due_date=today - datetime.timedelta(days=3))
        self.client.force_login(self.l2)

    # ---- follow-up and investigations
    def test_follow_up_lists_what_failed_and_what_is_late(self):
        page = self.client.get(reverse("qhse:dashboard"))
        self.assertContains(page, "Late thing")
        self.assertEqual((page.context["stats"]["failed"], page.context["stats"]["overdue_jobs"], page.context["stats"]["overdue_actions"]), (1, 1, 1))
        self.assertContains(page, "Investigate")

    def test_investigate_from_a_failed_job_prefills_and_links(self):
        url = reverse("qhse:investigation_create") + f"?maintenance={self.failed.pk}&trigger=failed_test"
        self.assertContains(self.client.get(url), "Failed test: PCE-9")
        r = self.client.post(url, {"title": "Failed test: PCE-9", "trigger": "failed_test", "asset_serial": "PCE-9",
                                   "opened_date": timezone.localdate().isoformat(), "what_happened": "x", "owner": ""})
        inv = Investigation.objects.get()
        self.assertRedirects(r, reverse("qhse:investigation_detail", args=[inv.pk]))
        self.assertEqual(inv.maintenance_record, self.failed)
        self.assertEqual(inv.reference[:8], f"INV-{timezone.localdate().year}")
        # once opened, the follow-up page shows it instead of an Investigate button for that job
        self.assertContains(self.client.get(reverse("qhse:dashboard")), inv.reference)

    def test_investigation_flow_close_and_corrective_action(self):
        inv = mk_investigation(title="Why late", trigger="overdue_action", action=self.overdue_action)
        self.client.post(reverse("qhse:investigation_action", args=[inv.pk]), {"action_item": "Fix cover", "priority": "High", "assignee": ""})
        self.assertEqual(inv.actions.count(), 1)
        self.client.post(reverse("qhse:investigation_status", args=[inv.pk]), {"status": "Closed"})
        inv.refresh_from_db()
        self.assertEqual(inv.closed_date, timezone.localdate())
        self.assertNotContains(self.client.get(reverse("qhse:dashboard")), "Why late")

    # ---- audit templates
    def make_template(self):
        from qhse.models import AuditPart, AuditPoint, AuditTemplate

        t = AuditTemplate.objects.create(name="Rig-up", pass_mark=80)
        part = AuditPart.objects.create(template=t, title="Lifting", order=1)
        self.p_crit = AuditPoint.objects.create(part=part, text="Plan signed", critical=True, order=1)
        self.p_plain = AuditPoint.objects.create(part=part, text="Tags", order=2)
        self.p_num = AuditPoint.objects.create(part=part, text="Test pressure", answer_type="number", unit="psi",
                                               min_value=3000, max_value=3300, order=3)
        return t

    def test_only_level_3_builds_templates(self):
        for who, expected in ((self.l1, 403), (self.l2, 403), (self.l3, 200)):
            self.client.force_login(who)
            self.assertEqual(self.client.get(reverse("qhse:template_create")).status_code, expected, who)
        t = self.make_template()
        self.client.force_login(self.l2)
        self.assertEqual(self.client.get(reverse("qhse:template_detail", args=[t.pk])).status_code, 200)      # anyone can look
        self.assertEqual(self.client.post(reverse("qhse:point_delete", args=[self.p_plain.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("qhse:part_edit", args=[self.p_plain.part_id]), {"title": "x"}).status_code, 403)
        self.client.force_login(self.l3)
        self.client.post(reverse("qhse:point_create", args=[self.p_plain.part_id]), {"text": "New point", "answer_type": "ok", "note_required": "on"})
        self.assertEqual(t.point_count, 4)
        self.client.post(reverse("qhse:move", args=["point", self.p_num.pk]), {"direction": "up"})
        self.assertEqual([p.text for p in self.p_plain.part.points.all()][:3], ["Plan signed", "Test pressure", "Tags"])

    def test_a_measured_point_needs_a_limit(self):
        t = self.make_template()
        self.client.force_login(self.l3)
        r = self.client.post(reverse("qhse:point_create", args=[self.p_plain.part_id]), {"text": "Gauge", "answer_type": "number", "unit": "psi"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(t.point_count, 3)

    def test_copy_template(self):
        t = self.make_template()
        self.client.force_login(self.l3)
        self.client.post(reverse("qhse:template_copy", args=[t.pk]))
        from qhse.models import AuditTemplate
        copy = AuditTemplate.objects.exclude(pk=t.pk).get()
        self.assertEqual((copy.name, copy.point_count), ("Rig-up (copy)", 3))

    def test_carrying_out_an_audit_scores_it_and_raises_actions(self):
        t = self.make_template()
        ins = mk_inspection(title="Rig-up audit", template=t, due_date=timezone.localdate(), scope="external", auditor_company="BV")
        url = reverse("qhse:inspection_conduct", args=[ins.pk])
        self.assertContains(self.client.get(url), "Plan signed")
        answers = {a.point_text: a.pk for a in ins.answers.all()}
        # not finished: a failed point with no reason, and an unanswered point
        r = self.client.post(url, {"finish": "1", f"outcome_{answers['Plan signed']}": "non_compliant", f"outcome_{answers['Tags']}": "compliant"})
        self.assertEqual(r.status_code, 200)
        ins.refresh_from_db()
        self.assertFalse(ins.is_done)
        r = self.client.post(url, {
            "finish": "1", "raise_actions": "1", "done_date": timezone.localdate().isoformat(),
            f"outcome_{answers['Plan signed']}": "non_compliant", f"notes_{answers['Plan signed']}": "missing",
            f"outcome_{answers['Tags']}": "compliant", f"value_{answers['Test pressure']}": "3400",
            f"notes_{answers['Test pressure']}": "too high"})
        self.assertRedirects(r, reverse("qhse:inspection_detail", args=[ins.pk]))
        ins.refresh_from_db()
        self.assertTrue(ins.is_done)
        self.assertEqual((ins.score, ins.result), (33, "Fail"))                 # 1 of 3 compliant, and a critical point failed
        self.assertEqual(Action.objects.filter(qhse_inspections=ins).count(), 2)  # one per non-compliant point
        self.assertTrue(Action.objects.filter(action_item__contains="Plan signed", priority="High").exists())
        page = self.client.get(reverse("qhse:inspection_detail", args=[ins.pk]))
        self.assertContains(page, "Results by point")
        self.assertContains(page, "3400")

    def test_passing_audit_and_template_changes_do_not_rewrite_it(self):
        t = self.make_template()
        ins = mk_inspection(title="A", template=t, due_date=timezone.localdate())
        url = reverse("qhse:inspection_conduct", args=[ins.pk])
        ins.answers.exists() or self.client.get(url)
        answers = {a.point_text: a.pk for a in ins.answers.all()}
        self.client.post(url, {"finish": "1", f"outcome_{answers['Plan signed']}": "compliant", f"outcome_{answers['Tags']}": "na",
                               f"value_{answers['Test pressure']}": "3100"})
        ins.refresh_from_db()
        self.assertEqual((ins.score, ins.result), (100, "Pass"))
        self.p_crit.text = "Renamed later"
        self.p_crit.save()
        self.assertEqual(ins.answers.get(point=self.p_crit).point_text, "Plan signed")

    # ---- site visits
    def test_site_visit_draft_submit_approve_lock_and_pdf(self):
        from qhse.models import SiteVisit, SiteVisitItem

        r = self.client.post(reverse("qhse:visit_create"), {"department": self.dept.pk, "date": timezone.localdate().isoformat(), "site": "Well G-2", "purpose": "Pre-job"})
        visit = SiteVisit.objects.get()
        self.assertEqual((visit.state, visit.reference[:3]), ("Draft", "SV-"))
        bad = self.client.post(reverse("qhse:item_create", args=[visit.pk]), {"area": "Camp", "kind": "finding", "text": "No extinguisher"})
        self.assertEqual(bad.status_code, 200)                                    # a finding needs a severity
        self.client.post(reverse("qhse:item_create", args=[visit.pk]), {"area": "Camp", "kind": "finding", "severity": "High", "text": "No extinguisher"})
        item = SiteVisitItem.objects.get()
        self.client.post(reverse("qhse:item_action", args=[item.pk]), {"action_item": "Fit extinguisher", "priority": "High", "assignee": ""})
        item.refresh_from_db()
        self.assertIsNotNone(item.action)
        self.client.post(reverse("qhse:visit_submit", args=[visit.pk]))
        self.assertEqual(visit.state, "Submitted")                                # a Level 2's report waits for a Level 3
        self.client.post(reverse("qhse:item_create", args=[visit.pk]), {"area": "x", "kind": "observation", "text": "late edit"})
        self.assertEqual(SiteVisitItem.objects.count(), 1)                        # locked
        self.client.force_login(self.l1)
        self.assertEqual(self.client.get(reverse("qhse:visit_pdf", args=[visit.pk])).status_code, 403)   # Level 1 cannot download
        self.client.force_login(self.l2)
        self.client.force_login(self.l3)
        from approvals import services
        services.decide(services.get(visit, "site_visit"), self.l3, approve=True)
        self.assertEqual(SiteVisit.objects.get().state, "Approved")
        pdf = self.client.get(reverse("qhse:visit_pdf", args=[visit.pk]))
        self.assertEqual((pdf.status_code, pdf["Content-Type"]), (200, "application/pdf"))
        self.assertTrue(pdf.content.startswith(b"%PDF"))

    def test_a_level_3_submission_is_approved_at_once(self):
        from qhse.models import SiteVisit

        self.client.force_login(self.l3)
        visit = SiteVisit.objects.create(department=test_department(), site="Yard", summary="ok")
        self.client.post(reverse("qhse:visit_submit", args=[visit.pk]))
        self.assertEqual(SiteVisit.objects.get().state, "Approved")

    def test_every_new_page_renders(self):
        from qhse.models import SiteVisit

        t = self.make_template()
        ins = mk_inspection(title="A", template=t, due_date=timezone.localdate())
        inv = mk_investigation(title="I")
        visit = SiteVisit.objects.create(department=test_department(), site="Yard")
        for url in (reverse("qhse:investigation_list"), reverse("qhse:investigation_detail", args=[inv.pk]),
                    reverse("qhse:investigation_create") + f"?action={self.overdue_action.pk}",
                    reverse("qhse:investigation_create") + f"?maintenance={self.late.pk}",
                    reverse("qhse:template_list"), reverse("qhse:template_detail", args=[t.pk]),
                    reverse("qhse:inspection_conduct", args=[ins.pk]), reverse("qhse:inspection_detail", args=[ins.pk]),
                    reverse("qhse:inspection_create") + f"?template={t.pk}", reverse("qhse:visit_list"), reverse("qhse:visit_list") + "?tab=Draft",
                    reverse("qhse:visit_detail", args=[visit.pk]), reverse("qhse:visit_create"), reverse("qhse:item_create", args=[visit.pk])):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.client.force_login(self.l3)
        for url in (reverse("qhse:template_create"), reverse("qhse:part_create", args=[t.pk]), reverse("qhse:point_create", args=[t.parts.first().pk]),
                    reverse("qhse:point_edit", args=[self.p_num.pk]), reverse("qhse:template_edit", args=[t.pk])):
            self.assertEqual(self.client.get(url).status_code, 200, url)


class QhseDepartmentTests(TestCase):
    """QHSE by department: Level 3 / Admin pick one; Level 1 and 2 are locked to their own."""

    def setUp(self):
        from departments.models import Asset, Classification, Department
        from maintenance.models import MaintenanceRecord

        self.wl = Department.objects.create(name="Wireline & Slickline", location="Base")
        self.wt = Department.objects.create(name="Well Testing", location="Base")
        Asset.objects.create(classification=Classification.objects.create(department=self.wl, name="C"), name="WL-1", description="x")
        Asset.objects.create(classification=Classification.objects.create(department=self.wt, name="C"), name="WT-1", description="x")
        self.boss = user("dept_l3", Role.APPROVER)
        self.wl_user = user("wl_user", Role.EDITOR)
        link_department(self.wl_user, self.wl)
        self.wt_user = user("wt_user", Role.VIEWER)
        link_department(self.wt_user, self.wt)
        self.nodept = User.objects.create_user("nodept", password="x")
        Profile.objects.update_or_create(user=self.nodept, defaults={"role": Role.EDITOR})
        today = timezone.localdate()
        self.late_wl = MaintenanceRecord.objects.create(asset_serial="WL-1", schedule="one_off", due_date=today - datetime.timedelta(days=4))
        self.late_wt = MaintenanceRecord.objects.create(asset_serial="WT-1", schedule="one_off", due_date=today - datetime.timedelta(days=2))
        self.i_wl = Incident.objects.create(title="WL incident", department=self.wl)
        self.i_wt = Incident.objects.create(title="WT incident", asset_serial="WT-1")          # department comes from the equipment
        self.i_none = Incident.objects.create(title="Unassigned incident")
        self.client.force_login(self.boss)

    def test_the_department_comes_from_the_equipment(self):
        self.assertEqual(self.i_wt.department, self.wt)
        self.assertIsNone(self.i_none.department)
        self.assertEqual(Inspection.objects.create(title="Check", asset_serial="WL-1", due_date=timezone.localdate()).department, self.wl)
        self.assertEqual(Investigation.objects.create(title="Why", asset_serial="WT-1").department, self.wt)

    # ---- Level 3 / Admin: choose a department
    def test_level_3_lists_follow_the_chosen_department(self):
        url = reverse("qhse:incident_list") + "?tab=all"
        page = self.client.get(url + f"&dept={self.wl.pk}")
        self.assertContains(page, "WL incident")
        self.assertNotContains(page, "WT incident")
        self.assertNotContains(page, "Unassigned incident")
        self.assertContains(self.client.get(url + "&dept=none"), "Unassigned incident")
        for title in ("WL incident", "WT incident", "Unassigned incident"):
            self.assertContains(self.client.get(url + "&dept=all"), title)

    def test_the_choice_is_remembered_across_the_session_and_the_four_tabs(self):
        self.client.get(reverse("qhse:incident_list") + f"?dept={self.wt.pk}")
        page = self.client.get(reverse("qhse:incident_list") + "?tab=all")
        self.assertContains(page, "WT incident")
        self.assertNotContains(page, "WL incident")
        self.assertEqual(self.client.get(reverse("maintenance:list")).context["scope"].label, "Well Testing")     # same choice elsewhere

    def test_follow_up_page_splits_maintenance_and_actions_by_department(self):
        action = make_action(action_item="WL late action", department=self.wl, due_date=timezone.localdate() - datetime.timedelta(days=1))
        wl = self.client.get(reverse("qhse:dashboard") + f"?dept={self.wl.pk}")
        self.assertEqual((wl.context["stats"]["overdue_jobs"], wl.context["stats"]["overdue_actions"]), (1, 1))
        self.assertContains(wl, "WL-1")
        self.assertNotContains(wl, "WT-1")
        wt = self.client.get(reverse("qhse:dashboard") + f"?dept={self.wt.pk}")
        self.assertEqual((wt.context["stats"]["overdue_jobs"], wt.context["stats"]["overdue_actions"]), (1, 0))
        self.assertEqual(self.client.get(reverse("qhse:dashboard") + "?dept=all").context["stats"]["overdue_jobs"], 2)
        self.assertTrue(action.pk)

    def test_templates_for_all_departments_show_everywhere(self):
        from qhse.models import AuditTemplate

        AuditTemplate.objects.create(name="For everyone")
        AuditTemplate.objects.create(name="Only WL", department=self.wl)
        wt = self.client.get(reverse("qhse:template_list") + f"?dept={self.wt.pk}")
        self.assertContains(wt, "For everyone")
        self.assertNotContains(wt, "Only WL")
        self.assertContains(self.client.get(reverse("qhse:template_list") + f"?dept={self.wl.pk}"), "Only WL")

    def test_site_visit_and_investigation_lists_are_split_too(self):
        from qhse.models import SiteVisit

        SiteVisit.objects.create(site="WL site", department=self.wl)
        SiteVisit.objects.create(site="WT site", department=self.wt)
        Investigation.objects.create(title="WL why", department=self.wl)
        page = self.client.get(reverse("qhse:visit_list") + f"?dept={self.wt.pk}")
        self.assertContains(page, "WT site")
        self.assertNotContains(page, "WL site")
        self.assertNotContains(self.client.get(reverse("qhse:investigation_list") + f"?dept={self.wt.pk}"), "WL why")

    def test_a_new_record_starts_on_the_department_being_looked_at(self):
        self.client.get(reverse("qhse:incident_list") + f"?dept={self.wl.pk}")
        self.assertEqual(self.client.get(reverse("qhse:incident_create")).context["form"]["department"].value(), self.wl.pk)

    def test_level_3_must_choose_a_department_unless_the_equipment_gives_one(self):
        base = {"date": timezone.localdate().isoformat(), "incident_type": "incident", "severity": "Low", "title": "x"}
        self.assertEqual(self.client.post(reverse("qhse:incident_create"), base).status_code, 200)       # asked for a department
        self.assertEqual(Incident.objects.filter(title="x").count(), 0)
        self.client.post(reverse("qhse:incident_create"), {**base, "asset_serial": "WL-1"})
        self.assertEqual(Incident.objects.get(title="x").department, self.wl)

    # ---- Level 1 / 2: only their own department
    def test_a_level_2_person_sees_only_their_own_department(self):
        self.client.force_login(self.wl_user)
        page = self.client.get(reverse("qhse:incident_list") + "?tab=all")
        self.assertContains(page, "WL incident")
        self.assertNotContains(page, "WT incident")
        self.assertNotContains(page, "Unassigned incident")
        self.assertNotContains(page, 'id="dept-select"')                           # nothing to switch
        self.assertContains(page, "Wireline &amp; Slickline")
        # asking for another department in the address changes nothing
        other = self.client.get(reverse("qhse:incident_list") + f"?tab=all&dept={self.wt.pk}")
        self.assertNotContains(other, "WT incident")
        self.assertNotContains(self.client.get(reverse("qhse:incident_list") + "?tab=all&dept=all"), "WT incident")

    def test_another_departments_record_is_not_found_for_level_1_and_2(self):
        self.client.force_login(self.wl_user)
        self.assertEqual(self.client.get(reverse("qhse:incident_detail", args=[self.i_wl.pk])).status_code, 200)
        for name in ("incident_detail", "incident_edit", "incident_action"):
            self.assertEqual(self.client.get(reverse(f"qhse:{name}", args=[self.i_wt.pk])).status_code, 404, name)
        self.assertEqual(self.client.post(reverse("qhse:incident_status", args=[self.i_wt.pk]), {"status": "Closed"}).status_code, 404)
        self.client.force_login(self.boss)                                          # Level 3 can open anything
        self.assertEqual(self.client.get(reverse("qhse:incident_detail", args=[self.i_wt.pk])).status_code, 200)

    def test_follow_up_for_a_level_2_person_is_only_their_department(self):
        self.client.force_login(self.wl_user)
        page = self.client.get(reverse("qhse:dashboard"))
        self.assertContains(page, "WL-1")
        self.assertNotContains(page, "WT-1")

    def test_a_record_a_level_2_person_creates_goes_to_their_department(self):
        self.client.force_login(self.wl_user)
        self.client.post(reverse("qhse:incident_create"), {"date": timezone.localdate().isoformat(), "incident_type": "incident",
                                                           "severity": "Low", "title": "Mine", "department": self.wt.pk})      # a forged choice is ignored
        self.assertEqual(Incident.objects.get(title="Mine").department, self.wl)

    def test_a_person_with_no_department_sees_nothing(self):
        self.client.force_login(self.nodept)
        page = self.client.get(reverse("qhse:incident_list") + "?tab=all")
        self.assertNotContains(page, "WL incident")
        self.assertNotContains(page, "WT incident")
        self.assertContains(page, "not linked to a department")
        self.assertEqual(self.client.get(reverse("qhse:incident_detail", args=[self.i_wl.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("qhse:dashboard")).context["stats"]["overdue_jobs"], 0)
