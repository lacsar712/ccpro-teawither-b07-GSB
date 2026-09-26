from django.core.exceptions import ValidationError
from django.db import models

from .rules import (
    LOCKED_FIELDS,
    LOCK_MESSAGE,
    READY_MOISTURE_LIMIT,
    READY_MOISTURE_MESSAGE,
    get_latest_batch,
    is_batch_locked,
)


class Garden(models.Model):
    name = models.CharField("茶园名称", max_length=120)
    altitudeBand = models.CharField("海拔带", max_length=60)
    notes = models.TextField("备注", blank=True, default="")

    class Meta:
        ordering = ["name"]
        verbose_name = "茶园"
        verbose_name_plural = "茶园"

    def __str__(self):
        return self.name


class TroughQuerySet(models.QuerySet):
    def ready(self):
        """当前为「可下槽」的槽。首页计数、列表筛选共用此查询，保证对账一致。"""
        return self.filter(status=Trough.STATUS_READY)


class Trough(models.Model):
    STATUS_LOADING = "loading"
    STATUS_WITHERING = "withering"
    STATUS_READY = "ready"
    STATUS_CHOICES = [
        (STATUS_LOADING, "装叶中"),
        (STATUS_WITHERING, "萎凋中"),
        (STATUS_READY, "可下槽"),
    ]

    garden = models.ForeignKey(
        Garden,
        on_delete=models.CASCADE,
        related_name="troughs",
        verbose_name="茶园",
    )
    troughCode = models.CharField("槽位编号", max_length=40)
    cultivar = models.CharField("茶树品种", max_length=80)
    loadKg = models.DecimalField("装叶量(kg)", max_digits=10, decimal_places=2)
    status = models.CharField(
        "状态",
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_LOADING,
    )

    objects = TroughQuerySet.as_manager()

    class Meta:
        ordering = ["garden__name", "troughCode"]
        verbose_name = "萎凋槽"
        verbose_name_plural = "萎凋槽"
        constraints = [
            models.UniqueConstraint(
                fields=["garden", "troughCode"],
                name="uniq_trough_code_per_garden",
            ),
        ]

    def __str__(self):
        return f"{self.garden.name}-{self.troughCode}"

    def latest_batch(self):
        # 与锁定判定同源，避免两处各写一套“最新批次”口径。
        return get_latest_batch(self)

    def clean(self):
        super().clean()
        if self.status != self.STATUS_READY:
            return
        latest = self.latest_batch()
        if (
            latest is None
            or latest.actualMoisture is None
            or latest.actualMoisture > READY_MOISTURE_LIMIT
        ):
            raise ValidationError({"status": READY_MOISTURE_MESSAGE})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class WitherBatch(models.Model):
    trough = models.ForeignKey(
        Trough,
        on_delete=models.CASCADE,
        related_name="batches",
        verbose_name="萎凋槽",
    )
    startedAt = models.DateTimeField("开始时间")
    targetMoisture = models.DecimalField(
        "目标含水率(%)", max_digits=5, decimal_places=2
    )
    actualMoisture = models.DecimalField(
        "实测含水率(%)",
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    rollGrade = models.CharField("揉捻等级", max_length=40)

    class Meta:
        ordering = ["-startedAt", "-id"]
        verbose_name = "萎凋批次"
        verbose_name_plural = "萎凋批次"

    def __str__(self):
        return f"{self.trough} @ {self.startedAt:%Y-%m-%d %H:%M}"

    def _locked_field_changes(self):
        """已存在批次在锁定态下被改动的锁定字段（仅模型层兜底，正常入口走表单）。"""
        if not self.pk or not is_batch_locked(self):
            return []
        old = type(self).objects.filter(pk=self.pk).values(*LOCKED_FIELDS).first()
        if old is None:
            return []
        changed = []
        for field in LOCKED_FIELDS:
            if getattr(self, field) != old[field]:
                changed.append(field)
        return changed

    def clean(self):
        super().clean()
        changed = self._locked_field_changes()
        if changed:
            raise ValidationError({field: LOCK_MESSAGE for field in changed})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
