# ==================================================
# 功能说明：非标准肽 PDB 检测与序列严格核实的单元测试
# 使用方法：在 backend 目录 python -m unittest tests.test_peptide_seq_rebuild
# 依赖环境：Python 标准库
# 生成时间：2026-07-17
# ==================================================

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from engine.peptide_seq_rebuild import (
    is_nonstandard_peptide_pdb,
    normalize_peptide_sequence,
    rebuild_peptide_pdb_from_sequence,
    verify_sequence_composition,
    parse_pdb_atoms,
    NeedPeptideSequence,
)
from engine.cyclic_peptide import prepare_linear_peptide

DATA = Path(__file__).resolve().parent / "data"
DMEK = DATA / "nonstandard_peptide_dmek.pdb"


def _write_ala_ala_element_only(fp: Path) -> None:
    """写出几何合理的 Ala–Ala，但残基名空、原子名仅元素（模拟对接导出）。"""
    from engine.peptide_seq_rebuild import _format_atom_line

    atoms = [
        ("N", -1.2, 0.0, 0.0),
        ("C", 0.0, 0.0, 0.0),
        ("C", 0.8, 1.2, 0.0),
        ("O", 0.3, 2.3, 0.0),
        ("C", 0.5, -1.3, 0.0),
        ("N", 2.1, 1.0, 0.0),
        ("C", 3.0, 2.0, 0.0),
        ("C", 4.4, 1.6, 0.0),
        ("O", 5.2, 2.5, 0.0),
        ("C", 2.6, 3.4, 0.0),
    ]
    lines = []
    for i, (e, x, y, z) in enumerate(atoms, 1):
        # resname 用空格占位，模拟残基名缺失
        ln = _format_atom_line(i, e, "   ", " ", 1, x, y, z, e)
        # 强制残基名三列为空白
        ln = ln[:17] + "   " + ln[20:]
        lines.append(ln)
    lines.append("END\n")
    fp.write_text("".join(lines), encoding="utf-8")


def _write_standard_ala_ala(fp: Path) -> None:
    """写出标准命名的 Ala–Ala PDB。"""
    rows = [
        (1, "N", "ALA", 1, -1.2, 0.0, 0.0, "N"),
        (2, "CA", "ALA", 1, 0.0, 0.0, 0.0, "C"),
        (3, "C", "ALA", 1, 0.8, 1.2, 0.0, "C"),
        (4, "O", "ALA", 1, 0.3, 2.3, 0.0, "O"),
        (5, "CB", "ALA", 1, 0.5, -1.3, 0.0, "C"),
        (6, "N", "ALA", 2, 2.1, 1.0, 0.0, "N"),
        (7, "CA", "ALA", 2, 3.0, 2.0, 0.0, "C"),
        (8, "C", "ALA", 2, 4.4, 1.6, 0.0, "C"),
        (9, "O", "ALA", 2, 5.2, 2.5, 0.0, "O"),
        (10, "CB", "ALA", 2, 2.6, 3.4, 0.0, "C"),
    ]
    lines = []
    for ser, name, rn, ri, x, y, z, el in rows:
        nm = f" {name:<3s}" if len(name) < 4 else name[:4]
        lines.append(
            f"ATOM  {ser:5d} {nm} {rn} A{ri:4d}    "
            f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00          {el:>2s}\n"
        )
    lines.append("END\n")
    fp.write_text("".join(lines), encoding="utf-8")


class TestPeptideSeqRebuild(unittest.TestCase):
    """肽序列严格重建相关测试。"""

    def test_detect_dmek_nonstandard(self) -> None:
        """对接导出样例应被识别为非标准。"""
        self.assertTrue(DMEK.is_file())
        self.assertTrue(is_nonstandard_peptide_pdb(DMEK))

    def test_wrong_sequence_composition_fails(self) -> None:
        """组成不符的序列必须失败。"""
        atoms = parse_pdb_atoms(DMEK)
        with self.assertRaises(ValueError):
            verify_sequence_composition("AA", atoms)

    def test_normalize_sequence(self) -> None:
        """序列规范化与非法字符。"""
        self.assertEqual(normalize_peptide_sequence(" ac de "), "ACDE")
        with self.assertRaises(ValueError):
            normalize_peptide_sequence("A")
        with self.assertRaises(ValueError):
            normalize_peptide_sequence("AXZ")

    def test_rebuild_ala_ala_from_element_only(self) -> None:
        """合成 Ala–Ala 元素名 PDB + 序列 AA 应重建出带 CA 的标准 PDB。"""
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "bad.pdb"
            out = Path(td) / "good.pdb"
            _write_ala_ala_element_only(src)
            self.assertTrue(is_nonstandard_peptide_pdb(src))
            meta = rebuild_peptide_pdb_from_sequence(src, "AA", out)
            self.assertEqual(meta["n_residues"], 2)
            text = out.read_text(encoding="utf-8")
            self.assertIn(" CA ", text)
            self.assertIn("ALA", text)

    def test_prepare_linear_needs_sequence(self) -> None:
        """非标准无序列时应抛 NeedPeptideSequence。"""
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(NeedPeptideSequence):
                prepare_linear_peptide(str(DMEK), td)

    def test_prepare_linear_standard_unaffected(self) -> None:
        """标准线形肽路径不受影响。"""
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "std.pdb"
            _write_standard_ala_ala(src)
            self.assertFalse(is_nonstandard_peptide_pdb(src))
            meta = prepare_linear_peptide(str(src), td)
            self.assertEqual(meta["n_residues"], 2)
            self.assertTrue(Path(meta["clean_pdb"]).is_file())


if __name__ == "__main__":
    unittest.main()
