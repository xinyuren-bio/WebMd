# GROMACS 分子动力学模拟体系搭建工具

**当前版本：v1.0**

Web 界面驱动的蛋白-配体 MD 前处理流水线，自动生成 GROMACS 可运行的模拟文件包。

## 版本与回溯

| 版本 | 说明 |
|------|------|
| **v1.0** | 首版 UI：CB-Dock3 风格主页、体系准备、轨迹分析双工作区 |

回溯到 v1.0：

```bash
git checkout v1.0          # 只读查看该版本
git checkout -b restore-v1.0 v1.0   # 基于 v1.0 开新分支继续改
git restore --source=v1.0 -- frontend/   # 仅恢复前端目录到 v1.0
```

## 功能

1. **蛋白修复** — PDBFixer 补残基/原子/氢，自动修正组氨酸质子化 (HID/HIE/HIP)
2. **配体参数化** — antechamber GAFF2 + AM1-BCC 电荷（自动检测净电荷）
3. **体系构建** — tleap 合并蛋白-配体、TIP3P 溶剂化、按浓度加盐
4. **格式转换** — acpype 将 Amber 拓扑转为 GROMACS gro/top
5. **模拟输入** — 生成 em/nvt/npt/md 四套 mdp 文件 + `run_md.sh` 一键脚本

## 依赖环境

### Python 包

```bash
cd backend
pip install -r requirements.txt
```

> `pdbfixer` 会间接依赖 OpenMM，但**仅用于蛋白结构修复**，模拟引擎为 GROMACS。

### 系统工具（必需）

| 工具 | 用途 | 安装方式 |
|------|------|----------|
| **AmberTools** | antechamber, parmchk2, tleap | `conda install -c conda-forge ambertools` |
| **acpype** | Amber → GROMACS 转换 | `pip install acpype`（已在 requirements 中） |
| **GROMACS** | 运行模拟 | `conda install -c conda-forge gromacs` |

推荐 Conda 环境：

```bash
conda create -n md_web -c conda-forge ambertools gromacs python=3.11
conda activate md_web
pip install -r backend/requirements.txt
```

## 启动

```bash
conda activate md_web
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

浏览器访问 http://localhost:8000

> **重要：** 必须在 `md_web` 环境中启动服务，否则找不到 antechamber/tleap/gmx 等命令：
> ```bash
> conda activate md_web
> cd backend && uvicorn main:app --host 0.0.0.0 --port 8000 --reload
> ```

## 故障排查

### antechamber: wrapped_progs/antechamber: No such file or directory

AmberTools 安装不完整，常见原因是 **acpype 安装后覆盖了 `wrapped_progs/`**。

**方法一（推荐）：** 流水线已内置自动修复，重启服务后重新提交任务即可。

**方法二（手动修复）：**

```bash
conda activate md_web
conda install -c conda-forge ambertools --force-reinstall -y
```

> 安装顺序建议：先装 `ambertools`，再装 `gromacs` 和 `acpype`，避免二进制被覆盖。

验证：

```bash
ls $CONDA_PREFIX/bin/wrapped_progs/antechamber
antechamber -h
gmx --version
acpype -h
```

## 使用流程

1. 上传蛋白 PDB 文件和小分子 MOL2 文件
2. 设置模拟参数（温度、压强、时长、离子浓度等）
3. 点击「开始准备模拟体系」，等待流水线完成
4. 下载 `tar.gz` 结果包

## 结果包内容

```
system.gro          # GROMACS 坐标
system.top          # GROMACS 拓扑
system.prmtop       # Amber 拓扑（参考）
system.inpcrd       # Amber 坐标（参考）
mdp/
  em.mdp            # 能量最小化
  nvt.mdp           # NVT 平衡
  npt.mdp           # NPT 平衡
  md.mdp            # 生产 MD
run_md.sh           # 一键运行脚本
```

## 运行模拟

解压后在目录中执行：

```bash
tar xzf gromacs_md_*.tar.gz -C md_run && cd md_run
bash run_md.sh
```

可通过环境变量调整：

```bash
GMX=gmx_mpi NTOMP=8 bash run_md.sh
```

## API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/tasks` | POST | 创建任务（multipart 上传） |
| `/api/tasks/{id}` | GET | 查询任务状态 |
| `/api/tasks/{id}/download` | GET | 下载结果包 |
| `/api/tasks/{id}/logs` | GET | 获取运行日志 |

任务元数据持久化在 `backend/tasks/{id}/task_meta.json`，服务重启后可恢复历史任务状态。

## 力场

- 蛋白：Amber ff14SB
- 配体：GAFF2
- 水模型：TIP3P
- 模拟引擎：GROMACS（PME, V-rescale 温控, Parrinello-Rahman 压控）
