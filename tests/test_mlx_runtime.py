"""فحص تشغيل محرك Apple MLX وتسريع عتاد Metal على Apple Silicon.

التحقق من:
1. توفر مكتبة mlx ومحرك النماذج mlx_lm.
2. تفعيل معالج الرسوميات (GPU) وتسريع Metal.
3. دقة العمليات الحسابية المتجهة على الذاكرة الموحدة (Unified Memory).
4. التعامل الآمن في حال غياب المكتبة على المنصات الأخرى (Graceful Degradation).
"""
from __future__ import annotations

import sys
import pytest


def test_apple_mlx_availability_and_metal():
    try:
        import mlx.core as mx
        import mlx_lm
    except ImportError:
        pytest.skip("مكتبة Apple MLX غير متوفرة في بيئة الاختبار الحالية")

    # فحص الإصدار وتوفر العتاد
    assert hasattr(mx, "__version__")
    assert hasattr(mlx_lm, "__version__")

    # في حال بيئة macOS arm64، يجب أن يكون Metal متاحاً
    if sys.platform == "darwin":
        device = mx.default_device()
        assert device.type in (mx.gpu, mx.cpu)
        if mx.metal.is_available():
            assert device == mx.Device(mx.gpu, 0)


def test_apple_mlx_tensor_operations():
    try:
        import mlx.core as mx
    except ImportError:
        pytest.skip("مكتبة Apple MLX غير متوفرة في بيئة الاختبار الحالية")

    # عمليات المصفوفات والتقييم الحتمي
    t1 = mx.array([1.0, 2.0, 3.0, 4.0])
    t2 = mx.array([10.0, 20.0, 30.0, 40.0])
    sum_t = t1 + t2
    mx.eval(sum_t)

    assert sum_t.tolist() == [11.0, 22.0, 33.0, 44.0]
    assert mx.sum(sum_t).item() == 110.0
