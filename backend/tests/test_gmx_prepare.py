# ==================================================
# 功能说明：位置约束与温控 index 生成单元测试
# 使用方法：cd backend && python -m pytest tests/test_gmx_prepare.py -q
# 依赖环境：pip install pytest
# 生成时间：2026-07-17
# ==================================================

from __future__ import annotations

from pathlib import Path

import pytest

from engine.gmx_prepare import (
    build_temperature_index,
    ensure_position_restraints,
    generate_posres_for_moltype,
)
from engine.simulation import generate_gromacs_inputs


def _mini_top() -> str:
    """构造迷你 ACPYPE 风格拓扑：system + CL- + NA+ + WAT。"""
    return """\
[ defaults ]
1 2 yes 0.5 0.8333

[ atomtypes ]
 N    N   0 0 A 0.3 0.5
 H    H   0 0 A 0.1 0.05
 CT   CT  0 0 A 0.3 0.4
 HC   HC  0 0 A 0.2 0.05
 c3   c3  0 0 A 0.3 0.4
 Cl-  Cl- 0 0 A 0.4 0.1
 Na+  Na+ 0 0 A 0.2 0.3
 OW   OW  0 0 A 0.3 0.6
 HW   HW  0 0 A 0.0 0.0

[ moleculetype ]
;name nrexcl
 system 3

[ atoms ]
; nr type resi res atom cgnr charge mass
  1   N    1   ALA   N   1  -0.4  14.01
  2   H    1   ALA   H   2   0.3   1.008
  3   CT   1   ALA   CA  3   0.1  12.01
  4   HC   1   ALA   HA  4   0.1   1.008
  5   c3   2   LIG1  C1  5   0.0  12.01
  6   HC   2   LIG1  H1  6   0.0   1.008

[ moleculetype ]
 CL- 1

[ atoms ]
  1  Cl-  1  CL-  CL-  1  -1  35.45

[ moleculetype ]
 NA+ 1

[ atoms ]
  1  Na+  1  NA+  NA+  1  1  22.99

[ moleculetype ]
 WAT 2

[ atoms ]
  1  OW  1  WAT  O   1  -0.834  16.00
  2  HW  1  WAT  H1  1   0.417   1.008
  3  HW  1  WAT  H2  1   0.417   1.008

[ system ]
 system

[ molecules ]
; Compound nmols
 system 1
 CL-    2
 NA+    3
 WAT    4
"""


def _mini_gro() -> str:
    """与迷你拓扑原子数一致的 gro：6+2+3+4*3=23。"""
    n = 6 + 2 + 3 + 12
    lines = ["mini", f"{n}"]
    # 简化：只写占位坐标
    for i in range(1, n + 1):
        lines.append(f"{1:5d}ALA  N   {i:5d}{0.1:8.3f}{0.2:8.3f}{0.3:8.3f}")
    lines.append(f"{2.0:10.5f}{2.0:10.5f}{2.0:10.5f}")
    return "\n".join(lines) + "\n"


def test_posres_skips_hydrogens(tmp_path: Path) -> None:
    """位置约束仅含非氢原子，使用局部编号。"""
    atoms = [
        {"nr": 1, "type": "N", "resname": "ALA", "atom": "N", "mass": 14.0},
        {"nr": 2, "type": "H", "resname": "ALA", "atom": "H", "mass": 1.008},
        {"nr": 3, "type": "CT", "resname": "ALA", "atom": "CA", "mass": 12.0},
    ]
    fp = tmp_path / "posre.itp"
    n = generate_posres_for_moltype(atoms, fp)
    assert n == 2
    text = fp.read_text(encoding="utf-8")
    assert "     1     1  1000  1000  1000" in text
    assert "     3     1  1000  1000  1000" in text
    assert "     2     1" not in text


def test_ensure_posres_and_index(tmp_path: Path) -> None:
    """溶质注入 POSRES；水/离子进入 Water_and_ions。"""
    (tmp_path / "system.top").write_text(_mini_top(), encoding="utf-8")
    (tmp_path / "system.gro").write_text(_mini_gro(), encoding="utf-8")
    stats = ensure_position_restraints(tmp_path, ["LIG1"])
    assert len(stats) == 1
    assert stats[0].moltype == "system"
    assert stats[0].n_restrained == 3  # N, CA, C1
    top = (tmp_path / "system.top").read_text(encoding="utf-8")
    assert "#ifdef POSRES" in top
    assert 'posre_system.itp' in top
    # 水/离子 moleculetype 不应出现 POSRES include 在其块内重复误绑
    idx = build_temperature_index(tmp_path, ["LIG1"])
    assert idx.n_protein_ligand == 6
    assert idx.n_water_ions == 2 + 3 + 12
    assert idx.n_system == 23
    ndx = (tmp_path / "index.ndx").read_text(encoding="utf-8")
    assert "[ Protein_Ligand ]" in ndx
    assert "[ Water_and_ions ]" in ndx


def test_mdp_nsteps_and_pcoupl(tmp_path: Path) -> None:
    """NVT/NPT 步数与压耦算法。"""
    generate_gromacs_inputs(str(tmp_path), {
        "timestep": 0.002,
        "nvt_time_ps": 500.0,
        "npt_time_ps": 1000.0,
        "simulation_time_ns": 10.0,
        "temperature": 310.0,
        "pressure": 1.0,
        "tau_t": 0.1,
        "tau_p": 5.0,
    })
    nvt = (tmp_path / "mdp" / "nvt.mdp").read_text(encoding="utf-8")
    npt = (tmp_path / "mdp" / "npt.mdp").read_text(encoding="utf-8")
    md = (tmp_path / "mdp" / "md.mdp").read_text(encoding="utf-8")
    assert "nsteps      = 250000" in nvt
    assert "nsteps      = 500000" in npt
    assert "define      = -DPOSRES" in nvt
    assert "define      = -DPOSRES" in npt
    assert "define      = -DPOSRES" not in md
    assert "pcoupl      = C-rescale" in npt
    assert "refcoord_scaling = com" in npt
    assert "pcoupl      = Parrinello-Rahman" in md
    assert "tc-grps     = Protein_Ligand Water_and_ions" in nvt
    assert "tc-grps     = Protein_Ligand Water_and_ions" in npt
    assert "tc-grps     = Protein_Ligand Water_and_ions" in md
    run = (tmp_path / "run_md.sh").read_text(encoding="utf-8")
    assert "-r em.gro" in run
    assert "-r nvt.gro" in run
    assert "-n index.ndx" in run
    assert "-maxwarn" not in run
