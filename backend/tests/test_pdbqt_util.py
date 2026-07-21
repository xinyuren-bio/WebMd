# ==================================================
# 功能说明：PDBQT 多构象抽取为 PDB（线形肽对接模式）单元测试
# 使用方法：在 backend 目录 python -m unittest tests.test_pdbqt_util
# 依赖环境：Python 标准库
# 生成时间：2026-07-21
# ==================================================

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from engine.pdbqt_util import count_poses, extract_pose, pdbqt_to_pdb


def _atom(serial: int, name: str, resname: str, resi: int, x: float, y: float, z: float) -> str:
    """构造固定列宽 ATOM 行（含 PDBQT 尾部电荷/类型占位）。"""
    base = (
        f"ATOM  {serial:5d} {name:>4s} {resname:3s} A{resi:4d}    "
        f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00"
    )
    return base + "    0.000 AA\n"


class TestPdbqtToPdb(unittest.TestCase):
    """验证多 MODEL PDBQT 抽取为干净 PDB。"""

    def _write_multi_model(self, fp: Path) -> None:
        """写入含两个 MODEL 的简易 PDBQT。"""
        text = (
            "MODEL        1\n"
            + _atom(1, "N", "ALA", 1, 1.0, 2.0, 3.0)
            + _atom(2, "CA", "ALA", 1, 1.5, 2.5, 3.5)
            + "ROOT\n"
            + "ENDROOT\n"
            + "TORSDOF 0\n"
            + "ENDMDL\n"
            + "MODEL        2\n"
            + _atom(1, "N", "ALA", 1, 10.0, 20.0, 30.0)
            + _atom(2, "CA", "ALA", 1, 10.5, 20.5, 30.5)
            + "ENDMDL\n"
        )
        fp.write_text(text, encoding="utf-8")

    def test_count_and_extract_second_pose(self) -> None:
        """多 MODEL：构象数为 2，抽取第 2 个应含 ATOM 且坐标为第二套。"""
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            src = wd / "ligand.pdbqt"
            self._write_multi_model(src)
            self.assertEqual(count_poses(src), 2)

            out = wd / "pose1.pdb"
            extract_pose(src, 1, out)
            body = out.read_text(encoding="utf-8")
            self.assertIn("ATOM", body)
            self.assertNotIn("ROOT", body)
            self.assertNotIn("MODEL", body)
            self.assertIn("END", body)
            # 第二构象 X≈10
            ca = next(ln for ln in body.splitlines() if ln.startswith("ATOM") and " CA " in ln)
            self.assertAlmostEqual(float(ca[30:38]), 10.5, places=3)

    def test_pdbqt_to_pdb_writes_linear_peptide_file(self) -> None:
        """pdbqt_to_pdb 写出目标 PDB 并返回构象总数。"""
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            src = wd / "pep.pdbqt"
            self._write_multi_model(src)
            dst = wd / "linear_peptide_upload.pdb"
            path, n = pdbqt_to_pdb(src, dst, index=0, work_dir=wd)
            self.assertEqual(n, 2)
            self.assertEqual(Path(path), dst)
            text = dst.read_text(encoding="utf-8")
            atoms = [ln for ln in text.splitlines() if ln.startswith("ATOM")]
            self.assertEqual(len(atoms), 2)
            self.assertTrue(text.rstrip().endswith("END"))
            # 第一构象 X≈1
            self.assertAlmostEqual(float(atoms[0][30:38]), 1.0, places=3)


if __name__ == "__main__":
    unittest.main()
