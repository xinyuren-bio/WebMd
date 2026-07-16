# ==================================================
# 功能说明：MD 前处理流水线（小分子 GAFF2 或标准氨基酸头尾环肽）
# 使用方法：由 API 后台任务调用 run_pipeline(...)
# 依赖环境：见 protein / ligand / cyclic_peptide / system_builder
# 生成时间：2026-07-16
# ==================================================

import logging
import tarfile
from pathlib import Path

from . import protein, ligand, system_builder, simulation, structure_export, ligand_ff
from . import cyclic_peptide
from .env_check import check_external_tools

logger = logging.getLogger(__name__)


def run_pipeline(
    work_dir: str,
    pdb_path: str,
    mol2_paths: list[str] | None,
    params: dict,
    status_callback,
    cyclic_pdb_path: str | None = None,
) -> str:
    """完整 MD 前处理流水线。

    小分子：PDBFixer → antechamber(GAFF2) → tleap → acpype → mdp。
    环肽：PDBFixer → 环肽 ff14SB+bond → tleap → acpype → mdp。
    返回 tar.gz 路径。
    """
    is_cyc = bool(params.get("is_cyclic_peptide"))
    if is_cyc:
        if not cyclic_pdb_path:
            raise ValueError("环肽模式需要上传环肽 PDB")
    else:
        if isinstance(mol2_paths, str):
            mol2_paths = [mol2_paths]
        if not mol2_paths:
            raise ValueError("至少需要一个 MOL2 文件")
        if len(mol2_paths) > 3:
            raise ValueError("最多支持 3 个配体")

    work = Path(work_dir)
    check_external_tools()

    # 1. 蛋白修复
    status_callback("processing_protein")
    protein_pdb = protein.prepare_protein(pdb_path, work_dir)
    logger.info("[1/5] 蛋白修复完成")

    # 2. 配体 / 环肽
    status_callback("processing_ligand")
    if is_cyc:
        cyc_meta = cyclic_peptide.prepare_cyclic_peptide(cyclic_pdb_path, work_dir)
        params["ligands"] = [{
            "index": 1,
            "resname": "CYC",
            "source": cyc_meta.get("source", "cyclic_peptide.pdb"),
            "type": "cyclic_peptide",
            "n_residues": cyc_meta["n_residues"],
            "resid_start": cyc_meta["resid_start"],
            "resid_end": cyc_meta["resid_end"],
        }]
        logger.info("[2/5] 环肽准备完成（ff14SB，头尾成环）")
    else:
        add_h = bool(params.get("ligand_add_hydrogens", True))
        ligand_list = ligand.parameterize_ligands(
            mol2_paths, work_dir, add_hydrogens=add_h,
        )
        params["ligands"] = [
            {"index": x["index"], "resname": x["resname"], "source": x["source"]}
            for x in ligand_list
        ]
        ligand_ff.export_ligand_forcefield_json(work_dir)
        logger.info("[2/5] 配体参数化完成 (%d 个)", len(ligand_list))

    # 3. tleap 构建 Amber 溶剂化体系
    status_callback("solvating")
    if is_cyc:
        prmtop, inpcrd = system_builder.build_full_system_cyclic(
            protein_pdb, cyc_meta, work_dir,
            box_padding=params.get("box_padding", 10.0),
            ion_conc=params.get("ion_conc", 0.15),
            salt_type=params.get("salt_type", "nacl"),
        )
    else:
        gaff0, frc0 = ligand_list[0]["gaff_mol2"], ligand_list[0]["frcmod"]
        prmtop, inpcrd = system_builder.build_full_system(
            protein_pdb, gaff0, frc0, work_dir,
            box_padding=params.get("box_padding", 10.0),
            ion_conc=params.get("ion_conc", 0.15),
            salt_type=params.get("salt_type", "nacl"),
            ligand_specs=ligand_list if len(ligand_list) > 1 else None,
        )
    logger.info("[3/5] tleap 体系构建完成")

    # 4. acpype 转换为 GROMACS 拓扑
    status_callback("converting_gmx")
    system_builder.convert_to_gromacs(prmtop, inpcrd, work_dir)
    logger.info("[4/5] GROMACS 拓扑转换完成")

    gro = work / "system.gro"
    if gro.exists():
        structure_export.export_complex_pdb(str(gro), str(work / "complex.pdb"))

    # 5. 生成 GROMACS mdp 与运行脚本
    status_callback("generating_mdp")
    simulation.generate_gromacs_inputs(work_dir, params)
    logger.info("[5/5] GROMACS 输入文件已生成")

    status_callback("packaging")
    output_tar = work / "md_simulation_package.tar.gz"
    with tarfile.open(output_tar, "w:gz") as tar:
        for f in work.iterdir():
            if f.name.endswith(".tar.gz"):
                continue
            tar.add(str(f), arcname=f.name)

    logger.info("结果包已就绪: %s", output_tar)
    return str(output_tar)
