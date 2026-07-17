# ==================================================
# 功能说明：线形肽 N 端 H/H2/H3 → H1/H2/H3 修正单元测试
# 使用方法：在 backend 目录 python -m unittest tests.test_linear_peptide_nterm
# 依赖环境：Python 标准库
# 生成时间：2026-07-17
# ==================================================

from __future__ import annotations

import unittest

from engine.system_builder import _fix_terminal_atoms_for_tleap


def _atom(serial: int, name: str, resname: str, chain: str, resi: int) -> str:
    """构造 ATOM 行。"""
    return (
        f"ATOM  {serial:5d} {name:>4s} {resname:3s} {chain}{resi:4d}    "
        f"{0.0:8.3f}{0.0:8.3f}{0.0:8.3f}{1.0:6.2f}{0.0:6.2f}           H\n"
    )


class TestLinearPeptideNterm(unittest.TestCase):
    """验证 NASP 场景下 H → H1。"""

    def test_h_h2_h3_renames_to_h1(self) -> None:
        """N 端同时有 H/H2/H3 时，H 应改为 H1。"""
        lines = [
            _atom(1, "N", "ASP", "C", 9001),
            _atom(2, "H", "ASP", "C", 9001),
            _atom(3, "H2", "ASP", "C", 9001),
            _atom(4, "H3", "ASP", "C", 9001),
            _atom(5, "CA", "ASP", "C", 9001),
        ]
        out = _fix_terminal_atoms_for_tleap(lines)
        names = [ln[12:16].strip() for ln in out if ln.startswith("ATOM")]
        self.assertIn("H1", names)
        self.assertNotIn("H", names)
        self.assertIn("H2", names)
        self.assertIn("H3", names)


if __name__ == "__main__":
    unittest.main()
