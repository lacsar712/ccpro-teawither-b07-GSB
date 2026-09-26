from django import forms

from .models import Garden, Trough, WitherBatch
from .rules import LOCKED_FIELDS, LOCK_MESSAGE, is_batch_locked


class GardenForm(forms.ModelForm):
    class Meta:
        model = Garden
        fields = ["name", "altitudeBand", "notes"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "input"}),
            "altitudeBand": forms.TextInput(attrs={"class": "input"}),
            "notes": forms.Textarea(attrs={"class": "input", "rows": 3}),
        }


class TroughForm(forms.ModelForm):
    class Meta:
        model = Trough
        fields = ["garden", "troughCode", "cultivar", "loadKg", "status"]
        widgets = {
            "garden": forms.Select(attrs={"class": "input"}),
            "troughCode": forms.TextInput(attrs={"class": "input"}),
            "cultivar": forms.TextInput(attrs={"class": "input"}),
            "loadKg": forms.NumberInput(attrs={"class": "input", "step": "0.01"}),
            "status": forms.Select(attrs={"class": "input"}),
        }


class WitherBatchForm(forms.ModelForm):
    # 供模板在锁定字段标签旁展示“已锁定”，与实际只读/强制字段同源。
    locked_field_names = LOCKED_FIELDS

    class Meta:
        model = WitherBatch
        fields = [
            "trough",
            "startedAt",
            "targetMoisture",
            "actualMoisture",
            "rollGrade",
        ]
        widgets = {
            "trough": forms.Select(attrs={"class": "input"}),
            "startedAt": forms.DateTimeInput(
                attrs={"class": "input", "type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "targetMoisture": forms.NumberInput(
                attrs={"class": "input", "step": "0.01"}
            ),
            "actualMoisture": forms.NumberInput(
                attrs={"class": "input", "step": "0.01"}
            ),
            "rollGrade": forms.TextInput(attrs={"class": "input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["startedAt"].input_formats = [
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
        ]
        if self.instance and self.instance.pk and self.instance.startedAt:
            from django.utils import timezone

            local = timezone.localtime(self.instance.startedAt)
            self.initial["startedAt"] = local.strftime("%Y-%m-%dT%H:%M")

        # 锁定与否以数据库当前状态为准（渲染只读 + 保存拒绝同源），
        # 不依赖可能被篡改的提交值。
        self._stored = self._load_stored()
        self.locked = bool(self._stored and is_batch_locked(self._stored))
        if self.locked:
            for name in LOCKED_FIELDS:
                widget = self.fields[name].widget
                widget.attrs["readonly"] = True
                widget.attrs["class"] = (
                    widget.attrs.get("class", "") + " input-readonly"
                ).strip()
                widget.attrs["title"] = LOCK_MESSAGE

    def _load_stored(self):
        if not self.instance.pk:
            return None
        return (
            WitherBatch.objects.select_related("trough")
            .filter(pk=self.instance.pk)
            .first()
        )

    def clean(self):
        cleaned_data = super().clean()
        if self.locked:
            for name in LOCKED_FIELDS:
                if name not in cleaned_data or cleaned_data.get(name) is None:
                    # 字段本身校验失败时已有错误，跳过锁定比较。
                    continue
                if self._locked_value_changed(name, cleaned_data[name]):
                    self.add_error(name, LOCK_MESSAGE)
                else:
                    # 还原为库中精确值再参与保存：startedAt 控件精度只到分钟，
                    # 否则回填值丢失秒/微秒，会被模型层兜底误判为“改动锁定字段”。
                    cleaned_data[name] = getattr(self._stored, name)
        return cleaned_data

    def _locked_value_changed(self, name, new_value):
        old_value = getattr(self._stored, name)
        if name == "startedAt":
            # 编辑控件精度到分钟（秒/微秒在渲染时被截断），按分钟归一化后比较，
            # 避免库里带秒的原值被误判为“已修改”。
            from django.utils import timezone

            def minute_of(value):
                if timezone.is_aware(value):
                    value = timezone.localtime(value)
                return value.replace(second=0, microsecond=0)

            return minute_of(new_value) != minute_of(old_value)
        return new_value != old_value
