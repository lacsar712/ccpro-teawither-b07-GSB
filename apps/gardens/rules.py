"""可下槽锁定相关的唯一判定来源。

改态入口（Trough.clean 设为可下槽的校验）、批次保存强制（WitherBatch）、
编辑表单只读（WitherBatchForm）、列表锁定标记与首页可下槽计数都只依赖本模块，
避免“前端只读 / 后端保存 / 列表展示”各自判断导致分叉。
"""

READY_MOISTURE_LIMIT = 40
READY_MOISTURE_MESSAGE = "无法设为可下槽：最新萎凋批次的实测含水率为空或高于 40%。"

# 最新批次在所属槽为「可下槽」期间锁定的字段。
LOCKED_FIELDS = ("startedAt", "targetMoisture")
LOCK_MESSAGE = "槽位当前为「可下槽」，最新批次的开始时间与目标含水率已锁定，不可修改。"


def get_latest_batch(trough):
    """返回槽的最新萎凋批次（与模型排序一致：开始时间倒序、id 倒序）。"""
    if trough is None or not getattr(trough, "pk", None):
        return None
    # 列表页会 prefetch_related("trough__batches")，命中预取缓存时直接在内存里
    # 取最新，避免逐槽再查一次库；未预取时回落到正常查询。
    cache = getattr(trough, "_prefetched_objects_cache", {})
    if "batches" in cache:
        batches = cache["batches"]
        if not batches:
            return None
        return max(batches, key=lambda b: (b.startedAt, b.id))
    return trough.batches.order_by("-startedAt", "-id").first()


def is_batch_locked(batch):
    """批次是否处于锁定态：它是所属槽的最新批次，且该槽当前为「可下槽」。

    历史（非最新）批次不锁；槽改回「装叶中 / 萎凋中」后锁定解除。
    未保存、未分配槽的批次不锁。
    """
    from .models import Trough

    if batch is None or not getattr(batch, "pk", None):
        return False
    trough = batch.trough
    if trough is None or trough.status != Trough.STATUS_READY:
        return False
    latest = get_latest_batch(trough)
    return latest is not None and latest.pk == batch.pk
