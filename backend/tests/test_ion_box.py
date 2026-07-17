# ==================================================
# 功能说明：离子盒体积与盐对数计算单元测试
# 使用方法：cd backend && python -m pytest tests/test_ion_box.py -q
# 依赖环境：pip install pytest
# 生成时间：2026-07-17
# ==================================================

from __future__ import annotations

import math
import textwrap
from pathlib import Path

import pytest

from engine.ion_box import (
    N_A,
    actual_pair_concentration_M,
    assert_box_volume_consistent,
    box_volume_A3,
    ion_pairs_from_conc,
    plan_salt_pairs,
    read_amber_inpcrd_box,
)


def test_orthogonal_volume() -> None:
    """正交盒体积为三边乘积。"""
    assert box_volume_A3(10.0, 20.0, 30.0) == pytest.approx(6000.0)


def test_nonorthogonal_volume_not_simple_product() -> None:
    """非正交盒不得直接相乘。"""
    v = box_volume_A3(10.0, 10.0, 10.0, 90.0, 90.0, 60.0)
    assert v != pytest.approx(1000.0)
    assert v == pytest.approx(1000.0 * math.sqrt(0.75), rel=1e-6)


def test_ion_pairs_zero_conc() -> None:
    """0 M 额外盐浓度应得到 0 对。"""
    assert ion_pairs_from_conc(0.0, 1e6) == 0


def test_ion_pairs_tiny_box() -> None:
    """极小盒子在 0.15 M 下取整后可为 0。"""
    n = ion_pairs_from_conc(0.15, 1.0)  # 1 Å³
    assert n == 0


def test_ion_pairs_formula_round() -> None:
    """核对 N_pair = round(C·V·1e-27·N_A)。"""
    vol = 528882.0
    c = 0.15
    expect = int(round(c * vol * 1e-27 * N_A))
    assert ion_pairs_from_conc(c, vol) == expect


def test_plan_nacl_kcl() -> None:
    """NaCl/KCl 规划仅改变阳离子标记。"""
    a = plan_salt_pairs("nacl", "Na+", 0.15, 1e5)
    b = plan_salt_pairs("kcl", "K+", 0.15, 1e5)
    assert a.n_pair == b.n_pair
    assert a.cation == "Na+"
    assert b.cation == "K+"


def test_actual_concentration_inverse() -> None:
    """由 N_pair 与体积反算浓度应接近目标。"""
    vol = 400000.0
    n = ion_pairs_from_conc(0.15, vol)
    c = actual_pair_concentration_M(n, vol)
    assert c == pytest.approx(0.15, rel=0.02)


def test_box_consistency_ok_and_fail() -> None:
    """盒体积一致性校验。"""
    assert_box_volume_consistent(1000.0, 1000.0)
    with pytest.raises(RuntimeError):
        assert_box_volume_consistent(1000.0, 1100.0)


def test_read_inpcrd_box(tmp_path: Path) -> None:
    """从简易 inpcrd 读取正交盒。"""
    # 2 原子，盒边 10 20 30，角 90
    body = textwrap.dedent(
        """\
        TITLE
             2
          1.0000000  2.0000000  3.0000000  4.0000000  5.0000000  6.0000000
         10.0000000 20.0000000 30.0000000 90.0000000 90.0000000 90.0000000
        """
    )
    fp = tmp_path / "x.inpcrd"
    fp.write_text(body, encoding="utf-8")
    box = read_amber_inpcrd_box(fp)
    assert box.lx == pytest.approx(10.0)
    assert box.ly == pytest.approx(20.0)
    assert box.lz == pytest.approx(30.0)
    assert box.volume_A3 == pytest.approx(6000.0)
    assert box.is_orthogonal
