from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .forms import WitherBatchForm
from .models import Garden, Trough, WitherBatch
from .rules import LOCK_MESSAGE, get_latest_batch, is_batch_locked
from .seed import ensure_seed_data


class LockRuleTests(TestCase):
    def setUp(self):
        self.garden = Garden.objects.create(name="测试园", altitudeBand="600m")
        # 装叶中起步，带一个合格最新批次，稍后切到可下槽。
        self.trough = Trough.objects.create(
            garden=self.garden,
            troughCode="A-01",
            cultivar="福鼎大白",
            loadKg=Decimal("100.00"),
            status=Trough.STATUS_LOADING,
        )
        now = timezone.now()
        self.history = WitherBatch.objects.create(
            trough=self.trough,
            startedAt=now - timezone.timedelta(hours=48),
            targetMoisture=Decimal("42.00"),
            actualMoisture=Decimal("41.00"),
            rollGrade="二级",
        )
        self.latest = WitherBatch.objects.create(
            trough=self.trough,
            startedAt=now - timezone.timedelta(hours=24),
            targetMoisture=Decimal("35.00"),
            actualMoisture=Decimal("34.80"),
            rollGrade="特级",
        )
        self.trough.status = Trough.STATUS_READY
        self.trough.save()

    def _form_data(self, **overrides):
        data = {
            "trough": str(self.trough.pk),
            "startedAt": timezone.localtime(self.latest.startedAt).strftime(
                "%Y-%m-%dT%H:%M"
            ),
            "targetMoisture": str(self.latest.targetMoisture),
            "actualMoisture": str(self.latest.actualMoisture),
            "rollGrade": self.latest.rollGrade,
        }
        data.update(overrides)
        return data

    # ---- 判定本身 ----

    def test_latest_batch_locked_when_ready_history_not(self):
        self.assertTrue(is_batch_locked(self.latest))
        self.assertFalse(is_batch_locked(self.history))
        self.assertEqual(get_latest_batch(self.trough), self.latest)

    def test_unlock_when_back_to_loading(self):
        self.trough.status = Trough.STATUS_LOADING
        self.trough.save()
        self.latest.refresh_from_db()
        self.assertFalse(is_batch_locked(self.latest))

    # ---- 表单：锁定字段拒绝、其他字段可改 ----

    def test_form_rejects_target_moisture_change_with_chinese_message(self):
        form = WitherBatchForm(
            data=self._form_data(targetMoisture="30.00"),
            instance=self.latest,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("targetMoisture", form.errors)
        self.assertIn(LOCK_MESSAGE, form.errors["targetMoisture"])

    def test_form_rejects_started_at_change(self):
        other = (self.latest.startedAt - timezone.timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M"
        )
        form = WitherBatchForm(
            data=self._form_data(startedAt=other),
            instance=self.latest,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("startedAt", form.errors)
        self.assertIn(LOCK_MESSAGE, form.errors["startedAt"])

    def test_form_allows_actual_moisture_and_roll_grade(self):
        form = WitherBatchForm(
            data=self._form_data(actualMoisture="33.00", rollGrade="一级"),
            instance=self.latest,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.latest.refresh_from_db()
        self.assertEqual(self.latest.actualMoisture, Decimal("33.00"))
        self.assertEqual(self.latest.rollGrade, "一级")

    def test_form_readonly_widgets_match_backend_lock(self):
        form = WitherBatchForm(instance=self.latest)
        self.assertTrue(form.locked)
        for name in ("startedAt", "targetMoisture"):
            self.assertTrue(form.fields[name].widget.attrs.get("readonly"))
        for name in ("actualMoisture", "rollGrade", "trough"):
            self.assertNotIn("readonly", form.fields[name].widget.attrs)

    def test_form_unlocked_after_back_to_loading_target_change_succeeds(self):
        self.trough.status = Trough.STATUS_LOADING
        self.trough.save()
        self.latest.refresh_from_db()
        form = WitherBatchForm(
            data=self._form_data(targetMoisture="31.00"),
            instance=self.latest,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.latest.refresh_from_db()
        self.assertEqual(self.latest.targetMoisture, Decimal("31.00"))

    def test_form_editable_when_withering(self):
        self.trough.status = Trough.STATUS_WITHERING
        self.trough.save()
        self.latest.refresh_from_db()
        form = WitherBatchForm(instance=self.latest)
        self.assertFalse(form.locked)
        self.assertNotIn("readonly", form.fields["targetMoisture"].widget.attrs)

    def test_history_batch_editable_even_when_ready(self):
        form = WitherBatchForm(
            data={
                "trough": str(self.trough.pk),
                "startedAt": timezone.localtime(self.history.startedAt).strftime(
                    "%Y-%m-%dT%H:%M"
                ),
                "targetMoisture": "55.00",
                "actualMoisture": "41.00",
                "rollGrade": "三级",
            },
            instance=self.history,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.history.refresh_from_db()
        self.assertEqual(self.history.targetMoisture, Decimal("55.00"))

    # ---- 模型层兜底：绕过表单直接改也拒绝 ----

    def test_model_blocks_bypass_of_locked_fields(self):
        self.latest.targetMoisture = Decimal("99.00")
        with self.assertRaises(ValidationError) as ctx:
            self.latest.save()
        self.assertIn("targetMoisture", ctx.exception.message_dict)
        self.assertIn(LOCK_MESSAGE, ctx.exception.message_dict["targetMoisture"])
        # 数据库未被改动
        self.assertEqual(
            WitherBatch.objects.get(pk=self.latest.pk).targetMoisture,
            Decimal("35.00"),
        )

    def test_model_allows_actual_moisture_bypass(self):
        self.latest.actualMoisture = Decimal("30.00")
        self.latest.save()
        self.assertEqual(
            WitherBatch.objects.get(pk=self.latest.pk).actualMoisture,
            Decimal("30.00"),
        )

    # ---- 改态入口仍走共用校验 ----

    def test_ready_transition_requires_valid_moisture(self):
        self.latest.actualMoisture = None
        self.latest.save()
        self.trough.status = Trough.STATUS_WITHERING
        self.trough.save()
        self.trough.status = Trough.STATUS_READY
        with self.assertRaises(ValidationError):
            self.trough.save()
        # 误改态未发生：锁定态与计数不受影响
        self.trough.refresh_from_db()
        self.assertEqual(self.trough.status, Trough.STATUS_WITHERING)


class ViewLockTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user("tester", password="pw123456")
        self.client.force_login(user)
        self.garden = Garden.objects.create(name="视图园", altitudeBand="600m")
        self.trough = Trough.objects.create(
            garden=self.garden,
            troughCode="V-01",
            cultivar="品种",
            loadKg=Decimal("80.00"),
            status=Trough.STATUS_LOADING,
        )
        now = timezone.now()
        self.history = WitherBatch.objects.create(
            trough=self.trough,
            startedAt=now - timezone.timedelta(hours=40),
            targetMoisture=Decimal("40.00"),
            actualMoisture=Decimal("40.50"),
            rollGrade="二级",
        )
        self.latest = WitherBatch.objects.create(
            trough=self.trough,
            startedAt=now - timezone.timedelta(hours=20),
            targetMoisture=Decimal("36.00"),
            actualMoisture=Decimal("35.50"),
            rollGrade="特级",
        )
        self.trough.status = Trough.STATUS_READY
        self.trough.save()

    def test_edit_page_readonly_matches_lock(self):
        resp = self.client.get(reverse("batch_edit", args=[self.latest.pk]))
        self.assertEqual(resp.status_code, 200)
        form = resp.context["form"]
        self.assertTrue(form.fields["targetMoisture"].widget.attrs.get("readonly"))
        self.assertTrue(form.fields["startedAt"].widget.attrs.get("readonly"))
        self.assertNotIn("readonly", form.fields["actualMoisture"].widget.attrs)
        self.assertContains(resp, "已锁定")

        resp_hist = self.client.get(reverse("batch_edit", args=[self.history.pk]))
        self.assertNotIn(
            "readonly",
            resp_hist.context["form"].fields["targetMoisture"].widget.attrs,
        )

    def test_post_tampered_locked_field_rejected_and_unchanged(self):
        resp = self.client.post(
            reverse("batch_edit", args=[self.latest.pk]),
            {
                "trough": str(self.trough.pk),
                "startedAt": timezone.localtime(self.latest.startedAt).strftime(
                    "%Y-%m-%dT%H:%M"
                ),
                "targetMoisture": "20.00",
                "actualMoisture": "35.50",
                "rollGrade": "特级",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, LOCK_MESSAGE)
        self.assertEqual(
            WitherBatch.objects.get(pk=self.latest.pk).targetMoisture,
            Decimal("36.00"),
        )

    def test_post_actual_moisture_update_succeeds(self):
        resp = self.client.post(
            reverse("batch_edit", args=[self.latest.pk]),
            {
                "trough": str(self.trough.pk),
                "startedAt": timezone.localtime(self.latest.startedAt).strftime(
                    "%Y-%m-%dT%H:%M"
                ),
                "targetMoisture": "36.00",
                "actualMoisture": "32.00",
                "rollGrade": "特级",
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            WitherBatch.objects.get(pk=self.latest.pk).actualMoisture,
            Decimal("32.00"),
        )

    def test_batch_list_marks_lock_from_shared_rule(self):
        resp = self.client.get(reverse("batch_list"))
        flags = {b.pk: b.is_locked for b in resp.context["batches"]}
        self.assertTrue(flags[self.latest.pk])
        self.assertFalse(flags[self.history.pk])
        self.assertContains(resp, "已锁定")

    def test_trough_list_marks_lock_and_ready_filter_reconciles_home(self):
        # 首页可下槽数
        home = self.client.get(reverse("home"))
        ready_count = home.context["ready_count"]

        resp = self.client.get(reverse("trough_list"))
        self.assertEqual(resp.context["status_counts"]["ready"], ready_count)
        self.assertEqual(ready_count, Trough.objects.ready().count())
        locked = {t.pk: t.latest_locked for t in resp.context["troughs"]}
        self.assertTrue(locked[self.trough.pk])

        ready_view = self.client.get(reverse("trough_list"), {"status": "ready"})
        self.assertEqual(ready_view.context["status_filter"], "ready")
        self.assertEqual(ready_view.context["object_list"].count(), ready_count)
        self.assertEqual(
            set(ready_view.context["object_list"].values_list("pk", flat=True)),
            set(Trough.objects.ready().values_list("pk", flat=True)),
        )

    def test_invalid_status_change_does_not_jump_ready_count(self):
        before = Trough.objects.ready().count()
        self.latest.actualMoisture = Decimal("55.00")
        self.latest.save()
        # 另一装叶中槽尝试非法切到可下槽，应被拒绝，计数不跳动
        other = Trough.objects.create(
            garden=self.garden,
            troughCode="V-02",
            cultivar="x",
            loadKg=Decimal("10.00"),
            status=Trough.STATUS_LOADING,
        )
        resp = self.client.post(
            reverse("trough_edit", args=[other.pk]),
            {
                "garden": str(self.garden.pk),
                "troughCode": "V-02",
                "cultivar": "x",
                "loadKg": "10.00",
                "status": "ready",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Trough.objects.ready().count(), before)


class SeedDataTests(TestCase):
    def test_seed_ready_trough_has_history_and_latest_batch(self):
        ensure_seed_data()
        ready = Trough.objects.ready()
        self.assertTrue(ready.exists())
        t4 = ready.get(troughCode="B-02")
        self.assertGreaterEqual(t4.batches.count(), 2)
        latest = t4.latest_batch()
        self.assertTrue(is_batch_locked(latest))
        history = t4.batches.exclude(pk=latest.pk).first()
        self.assertIsNotNone(history)
        self.assertFalse(is_batch_locked(history))
