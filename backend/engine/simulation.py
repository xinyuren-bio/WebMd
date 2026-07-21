# ==================================================
# 功能说明：生成 GROMACS mdp 参数文件与一键运行脚本 run_md.sh
# 使用方法：由 pipeline 调用 generate_gromacs_inputs(work_dir, params)
# 依赖环境：GROMACS (gmx 命令；NPT 需支持 C-rescale，通常 ≥2020)
# 生成时间：2026-07-17
# ==================================================

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# GROMACS 约束类型映射
_CONSTRAINT_MAP = {
    "HBonds": "h-bonds",
    "AllBonds": "all-bonds",
    "HAngles": "h-angles",
}

EM_MDP = """\
; 能量最小化（溶质重原子位置约束，与 NVT/NPT 同用 -DPOSRES）
define      = -DPOSRES
integrator  = steep
emtol       = 1000.0
emstep      = 0.01
nsteps      = 50000
nstlist     = 1
cutoff-scheme = Verlet
rlist       = {cutoff}
coulombtype = PME
rcoulomb    = {cutoff}
vdwtype     = Cut-off
rvdw        = {cutoff}
; 长程范德华色散校正：补偿 cutoff 截断丢失的长程色散吸引，
; 对能量与压强同时校正（EnerPres），避免 NPT 密度系统性偏低。
; 四个阶段统一开启，保证 EM/NVT/NPT/生产处于同一势能面。
DispCorr    = EnerPres
pbc         = xyz
"""

NVT_MDP = """\
; NVT 平衡（默认 500 ps；溶质重原子位置约束）
define      = -DPOSRES
integrator  = md
nsteps      = {nvt_steps}
dt          = {dt}
nstxout     = 0
nstvout     = 0
nstenergy   = 5000
nstlog      = 5000
cutoff-scheme = Verlet
rlist       = {cutoff}
coulombtype = PME
rcoulomb    = {cutoff}
vdwtype     = Cut-off
rvdw        = {cutoff}
; 长程范德华色散校正：补偿 cutoff 截断丢失的长程色散吸引，
; 对能量与压强同时校正（EnerPres），避免 NPT 密度系统性偏低。
; 四个阶段统一开启，保证 EM/NVT/NPT/生产处于同一势能面。
DispCorr    = EnerPres
pbc         = xyz
constraints = {constraints}
constraint-algorithm = lincs
lincs-order = 4
tcoupl      = V-rescale
tc-grps     = Protein_Ligand Water_and_ions
tau-t       = {tau_t} {tau_t}
ref-t       = {temperature} {temperature}
pcoupl      = no
gen-vel     = yes
gen-temp    = {temperature}
gen-seed    = -1
"""

NPT_MDP = """\
; NPT 平衡（默认 1000 ps；C-rescale；溶质重原子位置约束）
define      = -DPOSRES
integrator  = md
nsteps      = {npt_steps}
dt          = {dt}
nstxout     = 0
nstvout     = 0
nstenergy   = 5000
nstlog      = 5000
cutoff-scheme = Verlet
rlist       = {cutoff}
coulombtype = PME
rcoulomb    = {cutoff}
vdwtype     = Cut-off
rvdw        = {cutoff}
; 长程范德华色散校正：补偿 cutoff 截断丢失的长程色散吸引，
; 对能量与压强同时校正（EnerPres），避免 NPT 密度系统性偏低。
; 四个阶段统一开启，保证 EM/NVT/NPT/生产处于同一势能面。
DispCorr    = EnerPres
pbc         = xyz
constraints = {constraints}
constraint-algorithm = lincs
lincs-order = 4
tcoupl      = V-rescale
tc-grps     = Protein_Ligand Water_and_ions
tau-t       = {tau_t} {tau_t}
ref-t       = {temperature} {temperature}
pcoupl      = C-rescale
pcoupltype  = isotropic
tau-p       = {tau_p}
ref-p       = {pressure}
compressibility = 4.5e-5
refcoord_scaling = com
continuation = yes
gen-vel     = no
"""

MD_MDP = """\
; 生产 MD（无 POSRES；Parrinello-Rahman）
integrator  = md
nsteps      = {prod_steps}
dt          = {dt}
nstxout-compressed = {nstxout}
nstenergy   = {nstenergy}
nstlog      = {nstlog}
cutoff-scheme = Verlet
rlist       = {cutoff}
coulombtype = PME
rcoulomb    = {cutoff}
vdwtype     = Cut-off
rvdw        = {cutoff}
; 长程范德华色散校正：补偿 cutoff 截断丢失的长程色散吸引，
; 对能量与压强同时校正（EnerPres），避免 NPT 密度系统性偏低。
; 四个阶段统一开启，保证 EM/NVT/NPT/生产处于同一势能面。
DispCorr    = EnerPres
pbc         = xyz
constraints = {constraints}
constraint-algorithm = lincs
lincs-order = 4
tcoupl      = V-rescale
tc-grps     = Protein_Ligand Water_and_ions
tau-t       = {tau_t} {tau_t}
ref-t       = {temperature} {temperature}
pcoupl      = Parrinello-Rahman
pcoupltype  = isotropic
tau-p       = {tau_p}
ref-p       = {pressure}
compressibility = 4.5e-5
continuation = yes
gen-vel     = no
"""

RUN_SCRIPT = """\
#!/bin/bash
# GROMACS 模拟一键运行脚本（由 WebMD 自动生成）
# 要求：system.top 已含 POSRES、存在 index.ndx 与 posre_*.itp
set -euo pipefail

export PATH="/usr/local/gromacs/bin:${PATH}"
GMX="${GMX:-gmx}"

if [[ ! -f index.ndx ]]; then
  echo "错误：缺少 index.ndx（温控组 Protein_Ligand / Water_and_ions）" >&2
  exit 1
fi

echo "=== [1/4] 能量最小化（POSRES，参考坐标 system.gro）==="
$GMX grompp -f mdp/em.mdp -c system.gro -r system.gro -p system.top -n index.ndx -o em.tpr
$GMX mdrun -v -deffnm em -ntmpi 1

echo "=== [2/4] NVT 平衡（POSRES，参考坐标 em.gro）==="
$GMX grompp -f mdp/nvt.mdp -c em.gro -r em.gro -p system.top -n index.ndx -o nvt.tpr
$GMX mdrun -v -deffnm nvt -ntmpi 1

echo "=== [3/4] NPT 平衡（POSRES，参考坐标 nvt.gro，C-rescale）==="
$GMX grompp -f mdp/npt.mdp -c nvt.gro -r nvt.gro -t nvt.cpt -p system.top -n index.ndx -o npt.tpr
$GMX mdrun -v -deffnm npt -ntmpi 1

echo "=== [4/4] 生产 MD（无 POSRES，Parrinello-Rahman）==="
$GMX grompp -f mdp/md.mdp -c npt.gro -t npt.cpt -p system.top -n index.ndx -o md.tpr
$GMX mdrun -v -deffnm md -ntmpi 1

echo "=== 模拟完成 ==="
echo "轨迹文件: md.xtc  能量文件: md.edr  日志: md.log"
"""


def generate_gromacs_inputs(work_dir: str, params: dict) -> str:
    """生成 mdp/ 目录与 run_md.sh，返回脚本路径。"""
    work = Path(work_dir)
    mdp_dir = work / "mdp"
    mdp_dir.mkdir(exist_ok=True)

    dt = float(params.get("timestep", 0.002))  # ps
    temperature = params.get("temperature", 310.0)
    pressure = params.get("pressure", 1.0)
    cutoff = params.get("nonbonded_cutoff", 1.0)
    tau_t = params.get("tau_t", 0.1)
    # NPT/生产压耦时间常数默认 5.0 ps
    tau_p = params.get("tau_p", 5.0)
    constraints = _CONSTRAINT_MAP.get(
        params.get("constraints", "HBonds"), "h-bonds"
    )

    # 默认 NVT 500 ps、NPT 1000 ps
    nvt_time = float(params.get("nvt_time_ps", 500.0))
    npt_time = float(params.get("npt_time_ps", 1000.0))
    nvt_steps = int(round(nvt_time / dt))
    npt_steps = int(round(npt_time / dt))
    prod_steps = int(
        round(float(params.get("simulation_time_ns", 100.0)) * 1000.0 / dt)
    )
    report_ps = float(params.get("report_interval_ps", 100.0))
    nstxout = max(1, int(round(report_ps / dt)))
    nstenergy = max(1, int(round(report_ps / dt)))
    nstlog = max(1, int(round(report_ps / dt)))

    fmt = dict(
        dt=dt, temperature=temperature, pressure=pressure,
        cutoff=cutoff, constraints=constraints,
        tau_t=tau_t, tau_p=tau_p,
        nvt_steps=nvt_steps, npt_steps=npt_steps,
        prod_steps=prod_steps, nstxout=nstxout,
        nstenergy=nstenergy, nstlog=nstlog,
    )

    (mdp_dir / "em.mdp").write_text(EM_MDP.format(**fmt), encoding="utf-8")
    (mdp_dir / "nvt.mdp").write_text(NVT_MDP.format(**fmt), encoding="utf-8")
    (mdp_dir / "npt.mdp").write_text(NPT_MDP.format(**fmt), encoding="utf-8")
    (mdp_dir / "md.mdp").write_text(MD_MDP.format(**fmt), encoding="utf-8")

    script_path = work / "run_md.sh"
    script_path.write_text(RUN_SCRIPT, encoding="utf-8")
    script_path.chmod(0o755)

    logger.info(
        "GROMACS 输入已生成: NVT %d 步 (%.1f ps), NPT %d 步 (%.1f ps), "
        "生产 %d 步 (%.1f ns); tc-grps=Protein_Ligand Water_and_ions; "
        "NPT 压耦=C-rescale, 生产=Parrinello-Rahman",
        nvt_steps, nvt_time, npt_steps, npt_time,
        prod_steps, float(params.get("simulation_time_ns", 100.0)),
    )
    return str(script_path)
