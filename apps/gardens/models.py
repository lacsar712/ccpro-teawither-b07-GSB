from django.core.exceptions import ValidationError
from django.db import models


# ---- 「可下槽」锁定判定 -------------------------------------------------------
# 以下函数是锁定规则的唯一口径：改态入口（Trough.clean/save）、
# 批次编辑保存（WitherBatch.clean/save）与列表展示（模板属性）都走这里，
# 任何入口不得各自重写判定逻辑。

#: 槽位「可下槽」期间，其最新批次被锁定的字段
LOCKED_BATCH_FIELDS = ("targetMoisture", "startedAt")

#: 尝试修改锁定字段时的中文拒绝说明
LOCKED_BATCH_FIELD_MESSAGES = {
    "targetMoisture": "槽位已可下槽，最新批次的目标含水率已锁定，禁止修改。",
    "startedAt": "槽位已可下槽，最新批次的开始时间已锁定，禁止修改。",
}


def latest_batch_of(trough):
    """萎凋槽的最新批次（改态校验与锁定判定共用的唯一查询口径）。"""
    if trough is None or trough.pk is None:
        return None
    return trough.batches.order_by("-startedAt", "-id").first()


def is_trough_locked(trough):
    """槽位是否处于锁定态：状态为「可下槽」且已存在最新批次。"""
    if trough is None or trough.status != Trough.STATUS_READY:
        return False
    return latest_batch_of(trough) is not None


def is_batch_locked(batch):
    """批次是否锁定：所属槽位为「可下槽」且本批次是该槽最新批次。

    非最新批次不受此锁；槽位改回「装叶中」等非可下槽状态后锁定即解除。
    """
    if batch is None or batch.pk is None:
        return False
    trough = batch.trough
    if trough.status != Trough.STATUS_READY:
        return False
    latest = latest_batch_of(trough)
    return latest is not None and latest.pk == batch.pk


def locked_batch_fields(batch):
    """该批次当前被锁定的字段名元组；未锁定时为空元组。"""
    return LOCKED_BATCH_FIELDS if is_batch_locked(batch) else ()


def _minute_floor(value):
    """按分钟归一（编辑表单的时间精度为分钟）。"""
    if value is None:
        return None
    return value.replace(second=0, microsecond=0)


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
        return latest_batch_of(self)

    @property
    def is_locked(self):
        """锁定态（可下槽且有最新批次），与批次保存拒绝同一判定。"""
        return is_trough_locked(self)

    def clean(self):
        super().clean()
        if self.status != self.STATUS_READY:
            return
        latest = latest_batch_of(self)
        if latest is None or latest.actualMoisture is None or latest.actualMoisture > 40:
            raise ValidationError(
                {
                    "status": "无法设为可下槽：最新批次的实测含水率为空或高于 40%。"
                }
            )

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

    @property
    def is_locked(self):
        """是否处于「可下槽」锁定态，与保存拒绝同一判定。"""
        return is_batch_locked(self)

    def clean(self):
        super().clean()
        locked = locked_batch_fields(self)
        if not locked:
            return
        old = WitherBatch.objects.filter(pk=self.pk).first()
        if old is None:
            return
        errors = {}
        for name in locked:
            new_value = getattr(self, name)
            old_value = getattr(old, name)
            if name == "startedAt":
                # 编辑表单精度为分钟：按分钟归一后比较，
                # 避免秒/微秒差异误伤「仍可改」字段的正常保存。
                new_value = _minute_floor(new_value)
                old_value = _minute_floor(old_value)
            if new_value != old_value:
                errors[name] = LOCKED_BATCH_FIELD_MESSAGES[name]
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
