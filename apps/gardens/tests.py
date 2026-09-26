from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    Garden,
    Trough,
    WitherBatch,
    is_batch_locked,
    is_trough_locked,
    locked_batch_fields,
)
from .seed import ensure_seed_data


class LockTestCase(TestCase):
    """「可下槽」锁定：共用判定、后端强制、解锁流程。"""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            "tester", "tester@example.com", "pw123456"
        )
        cls.garden = Garden.objects.create(name="测试园", altitudeBand="800m")
        cls.trough = Trough.objects.create(
            garden=cls.garden,
            troughCode="T-01",
            cultivar="福鼎大白",
            loadKg=Decimal("100.00"),
            status=Trough.STATUS_WITHERING,
        )
        now = timezone.now().replace(second=0, microsecond=0)
        # 历史（非最新）批次
        cls.old_batch = WitherBatch.objects.create(
            trough=cls.trough,
            startedAt=now - timezone.timedelta(hours=72),
            targetMoisture=Decimal("36.50"),
            actualMoisture=Decimal("36.20"),
            rollGrade="一级",
        )
        # 最新批次
        cls.latest = WitherBatch.objects.create(
            trough=cls.trough,
            startedAt=now - timezone.timedelta(hours=24),
            targetMoisture=Decimal("35.00"),
            actualMoisture=Decimal("34.80"),
            rollGrade="特级",
        )
        cls.trough.status = Trough.STATUS_READY
        cls.trough.save()

    def setUp(self):
        self.client.force_login(self.user)

    # ---- 共用判定函数 ----

    def test_shared_predicates(self):
        self.assertTrue(is_trough_locked(self.trough))
        self.assertTrue(is_batch_locked(self.latest))
        self.assertEqual(
            locked_batch_fields(self.latest), ("targetMoisture", "startedAt")
        )
        # 非最新批次不受此锁
        self.assertFalse(is_batch_locked(self.old_batch))
        self.assertEqual(locked_batch_fields(self.old_batch), ())

    # ---- 后端强制（模型层，非仅前端只读） ----

    def test_locked_target_moisture_rejected_on_save(self):
        self.latest.targetMoisture = Decimal("36.00")
        with self.assertRaises(ValidationError) as cm:
            self.latest.save()
        self.assertIn("目标含水率已锁定", "".join(cm.exception.messages))
        self.latest.refresh_from_db()
        self.assertEqual(self.latest.targetMoisture, Decimal("35.00"))

    def test_locked_started_at_rejected_on_save(self):
        self.latest.startedAt = self.latest.startedAt + timezone.timedelta(hours=1)
        with self.assertRaises(ValidationError) as cm:
            self.latest.save()
        self.assertIn("开始时间已锁定", "".join(cm.exception.messages))

    def test_unlocked_fields_still_editable(self):
        self.latest.actualMoisture = Decimal("33.90")
        self.latest.rollGrade = "一级"
        self.latest.save()  # 不抛异常
        self.latest.refresh_from_db()
        self.assertEqual(self.latest.actualMoisture, Decimal("33.90"))
        self.assertEqual(self.latest.rollGrade, "一级")

    def test_non_latest_batch_freely_editable(self):
        self.old_batch.targetMoisture = Decimal("30.00")
        self.old_batch.startedAt = self.old_batch.startedAt - timezone.timedelta(
            hours=2
        )
        self.old_batch.save()  # 非最新批次不受锁
        self.old_batch.refresh_from_db()
        self.assertEqual(self.old_batch.targetMoisture, Decimal("30.00"))

    def test_lock_released_after_back_to_loading(self):
        self.trough.status = Trough.STATUS_LOADING
        self.trough.save()
        self.assertFalse(is_trough_locked(self.trough))
        self.latest.refresh_from_db()  # 清掉 FK 缓存，读到最新槽状态
        self.assertFalse(is_batch_locked(self.latest))
        # 解除后改目标含水须成功
        self.latest.targetMoisture = Decimal("32.00")
        self.latest.save()
        self.latest.refresh_from_db()
        self.assertEqual(self.latest.targetMoisture, Decimal("32.00"))

    # ---- 编辑保存入口（表单 POST，与列表展示同源） ----

    def _batch_post_data(self, batch, **overrides):
        data = {
            "trough": batch.trough_id,
            "startedAt": timezone.localtime(batch.startedAt).strftime(
                "%Y-%m-%dT%H:%M"
            ),
            "targetMoisture": str(batch.targetMoisture),
            "actualMoisture": (
                "" if batch.actualMoisture is None else str(batch.actualMoisture)
            ),
            "rollGrade": batch.rollGrade,
        }
        data.update(overrides)
        return data

    def test_form_post_locked_target_rejected_chinese(self):
        url = reverse("batch_edit", args=[self.latest.pk])
        data = self._batch_post_data(self.latest, targetMoisture="36.00")
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "目标含水率已锁定")
        self.latest.refresh_from_db()
        self.assertEqual(self.latest.targetMoisture, Decimal("35.00"))

    def test_form_post_locked_started_at_rejected_chinese(self):
        url = reverse("batch_edit", args=[self.latest.pk])
        shifted = timezone.localtime(
            self.latest.startedAt + timezone.timedelta(hours=3)
        ).strftime("%Y-%m-%dT%H:%M")
        data = self._batch_post_data(self.latest, startedAt=shifted)
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "开始时间已锁定")

    def test_form_post_unlocked_fields_ok(self):
        url = reverse("batch_edit", args=[self.latest.pk])
        data = self._batch_post_data(
            self.latest, actualMoisture="33.50", rollGrade="二级"
        )
        response = self.client.post(url, data)
        self.assertRedirects(
            response, reverse("batch_list"), fetch_redirect_response=False
        )
        self.latest.refresh_from_db()
        self.assertEqual(self.latest.actualMoisture, Decimal("33.50"))
        self.assertEqual(self.latest.rollGrade, "二级")

    def test_form_release_lock_then_edit_target_ok(self):
        # 槽改回装叶中
        response = self.client.post(
            reverse("trough_edit", args=[self.trough.pk]),
            {
                "garden": self.garden.pk,
                "troughCode": self.trough.troughCode,
                "cultivar": self.trough.cultivar,
                "loadKg": str(self.trough.loadKg),
                "status": Trough.STATUS_LOADING,
            },
        )
        self.assertRedirects(
            response, reverse("trough_list"), fetch_redirect_response=False
        )
        # 解除后改目标含水须成功
        response = self.client.post(
            reverse("batch_edit", args=[self.latest.pk]),
            self._batch_post_data(self.latest, targetMoisture="31.00"),
        )
        self.assertRedirects(
            response, reverse("batch_list"), fetch_redirect_response=False
        )
        self.latest.refresh_from_db()
        self.assertEqual(self.latest.targetMoisture, Decimal("31.00"))

    # ---- 编辑页只读与后端强制一致 ----

    def test_edit_page_readonly_matches_backend(self):
        response = self.client.get(reverse("batch_edit", args=[self.latest.pk]))
        form = response.context["form"]
        self.assertIn("readonly", form.fields["targetMoisture"].widget.attrs)
        self.assertIn("readonly", form.fields["startedAt"].widget.attrs)
        self.assertNotIn("readonly", form.fields["actualMoisture"].widget.attrs)
        self.assertNotIn("readonly", form.fields["rollGrade"].widget.attrs)
        self.assertContains(response, "readonly")

    def test_edit_page_no_readonly_when_unlocked(self):
        response = self.client.get(reverse("batch_edit", args=[self.old_batch.pk]))
        form = response.context["form"]
        for name in ("targetMoisture", "startedAt"):
            self.assertNotIn("readonly", form.fields[name].widget.attrs)

    # ---- 列表展示与保存拒绝同源 ----

    def test_lists_mark_lock_state(self):
        response = self.client.get(reverse("trough_list"))
        self.assertContains(response, "锁定")
        response = self.client.get(reverse("batch_list"))
        html = response.content.decode()
        self.assertEqual(html.count("已锁定"), 1)  # 仅最新批次一行

    # ---- 首页可下槽数与列表筛选对账 ----

    def test_home_ready_count_reconciles_with_filtered_list(self):
        home = self.client.get(reverse("home"))
        ready_count = home.context["ready_count"]
        filtered = self.client.get(reverse("trough_list") + "?status=ready")
        shown = len(filtered.context["troughs"])
        db_count = Trough.objects.filter(status=Trough.STATUS_READY).count()
        self.assertEqual(ready_count, shown)
        self.assertEqual(ready_count, db_count)

    def test_ready_count_not_disturbed_by_invalid_transition(self):
        # 最新批次实测含水 > 40 的槽不得误入可下槽
        t2 = Trough.objects.create(
            garden=self.garden,
            troughCode="T-02",
            cultivar="铁观音",
            loadKg=Decimal("90.00"),
            status=Trough.STATUS_WITHERING,
        )
        WitherBatch.objects.create(
            trough=t2,
            startedAt=timezone.now().replace(second=0, microsecond=0),
            targetMoisture=Decimal("38.00"),
            actualMoisture=Decimal("42.00"),
            rollGrade="二级",
        )
        before = Trough.objects.filter(status=Trough.STATUS_READY).count()
        response = self.client.post(
            reverse("trough_edit", args=[t2.pk]),
            {
                "garden": self.garden.pk,
                "troughCode": t2.troughCode,
                "cultivar": t2.cultivar,
                "loadKg": str(t2.loadKg),
                "status": Trough.STATUS_READY,
            },
        )
        self.assertEqual(response.status_code, 200)  # 表单拒绝，未跳转
        t2.refresh_from_db()
        self.assertEqual(t2.status, Trough.STATUS_WITHERING)
        after = Trough.objects.filter(status=Trough.STATUS_READY).count()
        self.assertEqual(before, after)


class SeedLockTestCase(TestCase):
    """种子数据：一个可下槽槽位，带最新批次与一条历史非最新批次。"""

    def test_seed_ready_trough_with_latest_and_history(self):
        ensure_seed_data()
        ready = Trough.objects.filter(status=Trough.STATUS_READY)
        self.assertEqual(ready.count(), 1)
        trough = ready.get()
        self.assertEqual(trough.troughCode, "B-02")
        batches = list(trough.batches.order_by("-startedAt", "-id"))
        self.assertGreaterEqual(len(batches), 2)
        latest, history = batches[0], batches[1]
        self.assertTrue(is_batch_locked(latest))
        self.assertFalse(is_batch_locked(history))
        # 种子时间为分钟精度，编辑页往返不触发误报
        self.assertEqual(latest.startedAt.second, 0)
        self.assertEqual(latest.startedAt.microsecond, 0)
