from django import forms

from .models import Garden, Trough, WitherBatch, locked_batch_fields


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
        # 前端只读与后端强制同源：锁定字段（目标含水率/开始时间）在编辑页
        # 渲染为只读；若绕过前端提交新值，后端 WitherBatch.clean 同样拒绝。
        self.locked_fields = locked_batch_fields(self.instance)
        for name in self.locked_fields:
            field = self.fields[name]
            field.widget.attrs["readonly"] = "readonly"
            field.help_text = "槽位「可下槽」期间该字段已锁定，后端保存同样拒绝修改。"
