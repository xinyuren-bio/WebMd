# ==================================================
# 功能说明：蛋白 PDB 双构象（altLoc）只保留一套的单元测试
# 使用方法：在 backend 目录 python -m unittest tests.test_altloc
# 依赖环境：Python 标准库
# 生成时间：2026-07-17
# ==================================================

from __future__ import annotations

import unittest

from engine.pdb_sanitize import resolve_altloc_lines


def _atom(
    serial: int,
    name: str,
    alt: str,
    resname: str,
    chain: str,
    resi: int,
    x: float,
    y: float,
    z: float,
    occ: float = 0.5,
) -> str:
    """构造固定列宽的 ATOM 行。"""
    return (
        f"ATOM  {serial:5d} {name:>4s}{alt}{resname:3s} {chain}{resi:4d}    "
        f"{x:8.3f}{y:8.3f}{z:8.3f}{occ:6.2f}{21.0:6.2f}           {name.strip()[0]:1s}\n"
    )


class TestResolveAltloc(unittest.TestCase):
    """验证 altLoc 择优与清空。"""

    def test_keep_one_set_prefer_a_when_equal(self) -> None:
        """标准命名的 A/B 双构象：同分时保留 A，丢弃 B。"""
        lines = [
            _atom(1, "N", " ", "ASN", "A", 741, -41.0, -11.7, -32.9, 1.0),
            _atom(2, "CA", "A", "ASN", "A", 741, -40.367, -12.985, -33.131, 0.5),
            _atom(3, "CA", "B", "ASN", "A", 741, -40.386, -13.000, -33.098, 0.5),
            _atom(4, "C", " ", "ASN", "A", 741, -39.4, -13.0, -31.9, 1.0),
            _atom(5, "O", " ", "ASN", "A", 741, -39.3, -12.0, -31.1, 1.0),
            _atom(6, "CB", "A", "ASN", "A", 741, -39.659, -13.002, -34.506, 0.5),
            _atom(7, "CB", "B", "ASN", "A", 741, -39.687, -13.057, -34.459, 0.5),
        ]
        out = resolve_altloc_lines(lines)
        atoms = [ln for ln in out if ln.startswith("ATOM")]
        self.assertEqual(len(atoms), 5)
        names = [ln[12:16].strip() for ln in atoms]
        self.assertEqual(names, ["N", "CA", "C", "O", "CB"])
        for ln in atoms:
            self.assertEqual(ln[16], " ")
            self.assertAlmostEqual(float(ln[54:60]), 1.0, places=2)
        # 保留的是 A 的 CA 坐标
        ca = next(ln for ln in atoms if ln[12:16].strip() == "CA")
        self.assertAlmostEqual(float(ca[30:38]), -40.367, places=3)

    def test_prefer_standard_names_over_c01(self) -> None:
        """一套写成 C01、一套写成 CA 时，保留标准命名那套。"""
        lines = [
            _atom(1, "N", " ", "ASN", "A", 741, -41.0, -11.7, -32.9, 1.0),
            _atom(2, "C01", "A", "ASN", "A", 741, -40.367, -12.985, -33.131, 0.5),
            _atom(3, "CA", "B", "ASN", "A", 741, -40.386, -13.000, -33.098, 0.5),
            _atom(4, "C02", "A", "ASN", "A", 741, -39.659, -13.002, -34.506, 0.5),
            _atom(5, "CB", "B", "ASN", "A", 741, -39.687, -13.057, -34.459, 0.5),
            _atom(6, "C", " ", "ASN", "A", 741, -39.4, -13.0, -31.9, 1.0),
        ]
        out = resolve_altloc_lines(lines)
        atoms = [ln for ln in out if ln.startswith("ATOM")]
        names = {ln[12:16].strip() for ln in atoms}
        self.assertIn("CA", names)
        self.assertIn("CB", names)
        self.assertNotIn("C01", names)
        self.assertNotIn("C02", names)


if __name__ == "__main__":
    unittest.main()
