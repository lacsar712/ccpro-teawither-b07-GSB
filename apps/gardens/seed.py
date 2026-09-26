from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import Garden, Trough, WitherBatch


def ensure_seed_data():
    """Idempotent seed: users + sample gardens/troughs/batches."""
    User = get_user_model()

    if not User.objects.filter(username="admin").exists():
        User.objects.create_superuser("admin", "admin@teawither.local", "123456")

    if not User.objects.filter(username="witherer").exists():
        User.objects.create_user("witherer", "witherer@teawither.local", "123456")

    if Garden.objects.exists():
        return

    g1 = Garden.objects.create(
        name="云雾岭一号园",
        altitudeBand="800-1000m",
        notes="向阳坡，晨雾较重",
    )
    g2 = Garden.objects.create(
        name="竹影台二号园",
        altitudeBand="600-800m",
        notes="背风缓坡",
    )

    t1 = Trough.objects.create(
        garden=g1,
        troughCode="A-01",
        cultivar="福鼎大白",
        loadKg=Decimal("120.50"),
        status=Trough.STATUS_WITHERING,
    )
    t2 = Trough.objects.create(
        garden=g1,
        troughCode="A-02",
        cultivar="铁观音",
        loadKg=Decimal("95.00"),
        status=Trough.STATUS_LOADING,
    )
    t3 = Trough.objects.create(
        garden=g2,
        troughCode="B-01",
        cultivar="黄金芽",
        loadKg=Decimal("88.25"),
        status=Trough.STATUS_WITHERING,
    )

    # 与编辑表单同为分钟精度，避免锁定批次编辑时的误报
    now = timezone.now().replace(second=0, microsecond=0)
    WitherBatch.objects.create(
        trough=t1,
        startedAt=now - timezone.timedelta(hours=18),
        targetMoisture=Decimal("38.00"),
        actualMoisture=Decimal("37.50"),
        rollGrade="一级",
    )
    WitherBatch.objects.create(
        trough=t2,
        startedAt=now - timezone.timedelta(hours=2),
        targetMoisture=Decimal("40.00"),
        actualMoisture=None,
        rollGrade="待评",
    )
    WitherBatch.objects.create(
        trough=t3,
        startedAt=now - timezone.timedelta(hours=30),
        targetMoisture=Decimal("36.00"),
        actualMoisture=Decimal("42.00"),
        rollGrade="二级",
    )

    # Ready trough with valid moisture
    t4 = Trough.objects.create(
        garden=g2,
        troughCode="B-02",
        cultivar="龙井43",
        loadKg=Decimal("110.00"),
        status=Trough.STATUS_WITHERING,
    )
    # 历史（非最新）批次：不受「可下槽」锁定影响，可自由编辑
    WitherBatch.objects.create(
        trough=t4,
        startedAt=now - timezone.timedelta(hours=72),
        targetMoisture=Decimal("36.50"),
        actualMoisture=Decimal("36.20"),
        rollGrade="一级",
    )
    # 最新批次：槽位可下槽期间其目标含水率与开始时间锁定
    WitherBatch.objects.create(
        trough=t4,
        startedAt=now - timezone.timedelta(hours=24),
        targetMoisture=Decimal("35.00"),
        actualMoisture=Decimal("34.80"),
        rollGrade="特级",
    )
    t4.status = Trough.STATUS_READY
    t4.save()
