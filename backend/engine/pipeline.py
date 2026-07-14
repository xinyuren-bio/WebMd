import logging
import tarfile
from pathlib import Path

from . import protein, ligand, system_builder, simulation, structure_export, ligand_ff
from .env_check import check_external_tools

logger = logging.getLogger(__name__)


def run_pipeline(work_dir: str, pdb_path: str, mol2_paths: list[str],
                 params: dict, status_callback) -> str:
    """完整 MD 前处理流水线。

    PDBFixer → antechamber(GAFF2) → tleap → acpype(GROMACS) → mdp 文件。
    mol2_paths 支持 1~3 个配体 MOL2。
    返回 tar.gz 路径。
    """
    if isinstance(mol2_paths, str):
        mol2_paths = [mol2_paths]
    if not mol2_paths:
        raise ValueError("至少需要一个 MOL2 文件")
    if len(mol2_paths) > 3:
        raise ValueError("最多支持 3 个配体")

    work = Path(work_dir)

    # 启动前检测外部工具
    check_external_tools()

    # 1. 蛋白修复
    status_callback("processing_protein")
    protein_pdb = protein.prepare_protein(pdb_path, work_dir)
    logger.info("[1/5] 蛋白修复完成")

    # 2. 配体 GAFF2 参数化（LIG1/LIG2/LIG3）
    status_callback("processing_ligand")
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

    # 导出蛋白-配体复合物 PDB（供网页 NGL 可视化，不含水/离子）
    gro = work / "system.gro"
    if gro.exists():
        structure_export.export_complex_pdb(str(gro), str(work / "complex.pdb"))

    # 5. 生成 GROMACS mdp 与运行脚本
    status_callback("generating_mdp")
    simulation.generate_gromacs_inputs(work_dir, params)
    logger.info("[5/5] GROMACS 输入文件已生成")

    # 打包
    status_callback("packaging")
    output_tar = work / "md_simulation_package.tar.gz"
    with tarfile.open(output_tar, "w:gz") as tar:
        for f in work.iterdir():
            if f.name.endswith(".tar.gz"):
                continue
            tar.add(str(f), arcname=f.name)

    logger.info("结果包已就绪: %s", output_tar)
    return str(output_tar)
