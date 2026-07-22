# WebMD 结果使用指南（湿实验友好版）

> 面向：不太熟悉分子动力学（MD）的湿实验同学  
> 用途：看懂交付文件、在 PyMOL 里看轨迹、把分析图丢给大模型写论文段落  
> 建议：把本文件与分析结果包一起保存；写文章 Methods / Results 时可直接改编文中模板

---

## 1. 先说结论：你拿到了什么？

模拟结束后，邮件通常会附带（或提供网站下载链接）：

| 文件 | 是什么 | 你最可能用它做什么 |
|------|--------|-------------------|
| `*_simulation.zip` | **模拟数据包** | 用 PyMOL 看蛋白–配体结构和轨迹 |
| `*_analysis.zip` | **分析结果包** | 看图、看 CSV、写论文 Results |
| 本指南 `WebMD_结果使用指南.md` | 说明文档 | 按步骤操作 / 复制提示词 |

### 模拟数据包里最重要的两个文件

- **`complex.pdb`**：某一时刻的**蛋白 + 配体/肽**三维结构（一般**不含**水和离子）。  
  - 蛋白通常在 **A 链**，配体/肽在 **B 链**（便于 PyMOL 按链上色）。
- **`fit.xtc`**：一段时间上的**运动轨迹**（同样一般是蛋白+配体，已相对蛋白骨架叠合，方便观察相对运动）。

> 一句话：`complex.pdb` 是“一张照片”，`fit.xtc` 是“一段录像”。两者一起拖进 PyMOL，就能看结合位点怎么动。

同包里还有 `system.top` / `md.tpr` / `to.ndx`：给会用 GROMACS 的人做复现或二次分析用；**湿实验同学可以先忽略**。

### 分析结果包

- **`analysis_plots/`**：PNG 图（建议优先看这些）  
- **`analysis_csv/`**：数值表（画图软件、Excel、或给大模型精读用）

---

## 2. 五分钟上手：用 PyMOL 看结构和轨迹

### 2.1 安装

- 官网：https://pymol.org/（教育用途可申请免费教育版）  
- 或使用开源版 / Open-Source PyMOL（按你单位习惯安装即可）

### 2.2 打开结构 + 轨迹（推荐）

1. 解压 `*_simulation.zip`  
2. 打开 PyMOL  
3. **把 `complex.pdb` 拖进 PyMOL 窗口**（先出现静止结构）  
4. **再把 `fit.xtc` 拖进同一个窗口**（轨迹会挂到当前分子上）  
5. 下方会出现播放控件：点播放即可看轨迹

若拖拽无效，可在 PyMOL 命令行输入（路径改成你的实际路径）：

```text
load /你的路径/complex.pdb, complex
load /你的路径/fit.xtc, complex
```

注意：第二条 `load ...xtc` 的对象名要和 pdb 一致（上面都是 `complex`）。

### 2.3 常用可视化命令（复制即用）

```text
# 按链上色：蛋白 A、配体/肽 B
util.cbc
# 或手动：
color marine, chain A
color yellow, chain B

# 蛋白画卡通，配体画棍状
hide everything
show cartoon, chain A
show sticks, chain B
show lines, chain A and not name c+n+o

# 只看结合口袋附近（配体周围 5 Å 的残基）
select pocket, byres (chain A within 5 of chain B)
show sticks, pocket
orient pocket

# 播放轨迹
mplay
# 停止
mstop
```

### 2.4 导出一张“论文用”静图

把轨迹拖到你觉得代表性的一帧，然后：

```text
ray 1800, 1400
png binding_pose.png, dpi=300
```

---

## 3. 分析图怎么看？（不必会公式）

下面按常见文件名说明。**不必每张都写进论文**；选 2–4 张能支撑你结论的即可。

| 图片（常见文件名） | 它在回答什么 | 湿实验解读要点 |
|--------------------|--------------|----------------|
| `rmsd.png` / `rmsd_protein.png` | 结构相对初始构象偏了多少 | 曲线后期若在小范围内波动，通常说明体系已较稳定，可用来写“模拟达到平衡” |
| `rmsd_ligand.png` | 配体相对蛋白的位置漂移 | 若配体 RMSD 很大且持续爬升，可能提示结合不稳定或发生明显位移（需结合目视轨迹） |
| `rg.png` | 蛋白“抱得紧不紧”（回转半径） | 大致平稳 → 整体折叠没有明显垮塌/异常膨胀 |
| `rmsf.png` | 哪些残基晃得厉害 | 峰值常对应 loop、末端或结合附近柔性区；可与实验突变位点对照 |
| `hbond.png` | 蛋白–配体氢键数量随时间 | 看平均水平和是否稳定维持，不是看瞬时尖峰 |
| `hbond_residue_timeline_*.png` | **哪些残基**在哪些时间在形成氢键 | 深色/高亮条带对应“常客”残基，适合写“关键相互作用残基” |
| `sasa.png` | 溶剂可及表面积变化 | 结合后口袋是否更封闭等，辅助描述 |
| `gibbs_fel_*.png`（若有） | 自由能景观（二维投影） | 看有几个低能盆地；适合高级讨论，不确定时可少写 |

> 重要提醒：单次 100 ns 模拟给出的是**该条件下的动力学与相互作用线索**，一般**不能单独当作结合亲和力（Kd/IC50）的定量证明**。写论文时请表述为“提示 / 支持 / 与实验一致”，避免过度断言。

---

## 4. 把分析图扔给大模型：怎么提问？

### 4.1 推荐用法

1. 打开 ChatGPT / Claude / 通义 / Kimi 等（能传图更好）  
2. 上传 `analysis_plots/` 里你选中的 2–6 张图  
3. **先粘贴下面的「系统设定」**，再粘贴「写作任务」  
4. 把 AI 初稿当作草稿：核对数字、删掉它瞎编的实验细节，再改成你的文风

### 4.2 系统设定（每次可先发这一段）

```text
你是一位熟悉蛋白–配体分子动力学（GROMACS）的科研写作助手。
服务对象是湿实验背景的研究者。请：
1）只用我提供的图片/文字信息，不要编造未给出的数值、PDB ID、实验数据；
2）若信息不足，明确写“根据现有图无法判断……”而不是猜测；
3）表述适合中文 SCI/中文核心论文：克制、可核对、避免营销口吻；
4）区分“观察”（图上能看到的）与“解释”（可能机制，需用“提示/可能”）；
5）不要把单次 MD 说成已证明结合亲和力或药效。
```

### 4.3 模板 A：Results 段落（推荐）

```text
【任务】根据我上传的 MD 分析图，写 1 段可放入论文 Results 的中文（约 180–280 字）。

【体系信息】（请按实际情况改）
- 蛋白：____（名称/Uniprot/PDB 可选填）
- 配体/肽：____
- 模拟时长：____ ns
- 温度/压强：____ K / ____ bar（若不知可写“标准 NPT 条件”）

【写作要求】
- 先写体系是否趋于稳定（结合 RMSD/Rg）
- 再写配体相对蛋白的稳定性（配体 RMSD / 目视若有）
- 再写关键相互作用（氢键数量或氢键残基时间图中的高频残基）
- 最后用 1 句谨慎小结（与结合相关的动力学行为）
- 不要出现“显著优于”“证明活性”等过强措辞
- 若某张图看不清，请标注“该指标未展示/不清晰”

【我上传的图】（列出文件名）
1. ...
2. ...
```

### 4.4 模板 B：Figure legend（图注）

```text
请为下列每张 MD 分析图各写一条英文图注（或中文图注，我指定：____），每条 1–2 句：
- 说明纵轴/横轴含义（若图上可见）
- 说明该图支持的观察，不写过度结论
图文件：……
```

### 4.5 模板 C：Discussion 里 3 句话

```text
基于这些图，写 3 句 Discussion：
1）与结合稳定性相关的观察；
2）可能的关键残基/相互作用（仅限图上支持的）；
3）局限性（时长、单次轨迹、力场近似、未做自由能计算等）。
语气：谨慎、可发表。
```

### 4.6 模板 D：帮你“翻译”一张看不懂的图

```text
这是 WebMD 导出的分析图：文件名为 ____。
请用湿实验同学能懂的话解释：
1）横轴纵轴大概是什么；
2）怎样算“看起来正常/需警惕”；
3）写论文时这句话怎么写最稳妥（给 1 句中文）。
不要编造具体数值；图上读不到就说读不到。
```

---

## 5. 可放进论文的 Methods 模板（按配体类型选用）

**请先确认你的任务类型**（邮件/任务页/`FORCEFIELD.txt` 可查），**只复制对应那一节**，不要混用：

| 你的任务 | 请用 |
|----------|------|
| 蛋白 + **小分子**（MOL2 / GAFF2） | **§5.1** |
| 蛋白 + **线形肽** | **§5.2** |
| 蛋白 + **环肽** | **§5.3** |

括号（　）请换成该任务实际参数。温度、时长、盐种类若改过，**以任务设置为准**。

### 5.1 蛋白 + 小分子（GAFF2）

**中文**

```text
蛋白–小分子配体体系的分子动力学模拟在 WebMD 自动化流程中完成。
蛋白结构经 PDBFixer 进行缺失残基与氢原子修复，并采用 Amber ff14SB 力场描述。
小分子配体采用 GAFF2 力场，并以 AM1-BCC 方法计算部分电荷。
溶剂化采用 TIP3P 水模型，并在中和体系净电荷后按约 0.15 M 加入 NaCl
（或 KCl，以任务设置为准）。体系在周期性边界条件下构建，随后将 Amber 拓扑
转换为 GROMACS 格式。

模拟流程包括：能量最小化；NVT 平衡（默认约 500 ps，溶质重原子位置约束）；
NPT 平衡（默认约 1 ns，C-rescale 压耦，溶质重原子位置约束）；以及无位置约束的
生产动力学（Parrinello–Rahman 压耦，V-rescale 温耦；长程静电采用 PME，
并开启长程色散校正 DispCorr）。生产模拟时长为（　）ns，积分步长 2 fs，
温度与压强目标分别为（　）K 与（　）bar。

轨迹经周期性边界条件处理并相对蛋白骨架叠合后，提取蛋白–配体溶质轨迹用于分析。
分析指标包括骨架/配体/复合物 RMSD、回转半径、残基 RMSF、蛋白–配体氢键及
（如有）相关自由能景观投影等。除非另有说明，上述参数均为 WebMD 默认方案。
```

**English**

```text
Protein–small-molecule MD simulations were prepared and executed via the WebMD
automated pipeline. The protein was repaired with PDBFixer and described with
Amber ff14SB. The ligand was parameterized with GAFF2 and AM1-BCC partial charges.
The system was solvated with TIP3P water, neutralized, and supplemented with
~0.15 M NaCl/KCl under periodic boundary conditions. Amber topologies were
converted to GROMACS.

The protocol comprised energy minimization; NVT equilibration (~500 ps) and NPT
equilibration (~1 ns) with heavy-atom position restraints on the solute; and
unrestrained production MD (V-rescale thermostat; C-rescale then
Parrinello–Rahman barostat; PME electrostatics; dispersion correction).
Production length was ( ) ns with a 2 fs time step at ( ) K and ( ) bar.
Trajectories were processed for PBC and fitted to the protein backbone;
solute-only trajectories were analyzed for RMSD/Rg/RMSF/hydrogen bonds as
provided by WebMD.
```

### 5.2 蛋白 + 线形肽（ff14SB）

**中文**

```text
蛋白–线形肽体系的分子动力学模拟在 WebMD 自动化流程中完成。
蛋白与线形肽均采用 Amber ff14SB 力场描述；蛋白结构经 PDBFixer 进行缺失残基
与氢原子修复；线形肽保留标准 N/C 末端（不成环）。
溶剂化采用 TIP3P 水模型，并在中和体系净电荷后按约 0.15 M 加入 NaCl
（或 KCl，以任务设置为准）。体系在周期性边界条件下构建，随后将 Amber 拓扑
转换为 GROMACS 格式。

模拟流程包括：能量最小化；NVT 平衡（默认约 500 ps，溶质重原子位置约束）；
NPT 平衡（默认约 1 ns，C-rescale 压耦，溶质重原子位置约束）；以及无位置约束的
生产动力学（Parrinello–Rahman 压耦，V-rescale 温耦；长程静电采用 PME，
并开启长程色散校正 DispCorr）。生产模拟时长为（　）ns，积分步长 2 fs，
温度与压强目标分别为（　）K 与（　）bar。

轨迹经周期性边界条件处理并相对蛋白骨架叠合后，提取蛋白–肽溶质轨迹用于分析。
分析指标包括骨架/肽/复合物 RMSD、回转半径、残基 RMSF、蛋白–肽氢键及
（如有）相关自由能景观投影等。除非另有说明，上述参数均为 WebMD 默认方案。
```

**English**

```text
Protein–linear-peptide MD simulations were prepared and executed via the WebMD
automated pipeline. Both the protein and the linear peptide were described with
Amber ff14SB. The protein was repaired with PDBFixer; the peptide retained
canonical N- and C-termini (no cyclization). The system was solvated with TIP3P
water, neutralized, and supplemented with ~0.15 M NaCl/KCl under periodic
boundary conditions. Amber topologies were converted to GROMACS.

The protocol comprised energy minimization; NVT (~500 ps) and NPT (~1 ns)
equilibration with heavy-atom position restraints on the solute; and unrestrained
production MD (V-rescale; C-rescale then Parrinello–Rahman; PME; dispersion
correction). Production length was ( ) ns with a 2 fs time step at ( ) K and
( ) bar. Trajectories were processed for PBC and fitted to the protein backbone;
solute-only trajectories were analyzed for RMSD/Rg/RMSF/hydrogen bonds as
provided by WebMD.
```

### 5.3 蛋白 + 环肽（ff14SB，头尾成环）

**中文**

```text
蛋白–环肽体系的分子动力学模拟在 WebMD 自动化流程中完成。
蛋白与环肽均采用 Amber ff14SB 力场描述；蛋白结构经 PDBFixer 进行缺失残基
与氢原子修复；环肽在头尾残基之间施加 N–C 酰胺键成环约束。
溶剂化采用 TIP3P 水模型，并在中和体系净电荷后按约 0.15 M 加入 NaCl
（或 KCl，以任务设置为准）。体系在周期性边界条件下构建，随后将 Amber 拓扑
转换为 GROMACS 格式。

模拟流程包括：能量最小化；NVT 平衡（默认约 500 ps，溶质重原子位置约束）；
NPT 平衡（默认约 1 ns，C-rescale 压耦，溶质重原子位置约束）；以及无位置约束的
生产动力学（Parrinello–Rahman 压耦，V-rescale 温耦；长程静电采用 PME，
并开启长程色散校正 DispCorr）。生产模拟时长为（　）ns，积分步长 2 fs，
温度与压强目标分别为（　）K 与（　）bar。

轨迹经周期性边界条件处理并相对蛋白骨架叠合后，提取蛋白–环肽溶质轨迹用于分析。
分析指标包括骨架/环肽/复合物 RMSD、回转半径、残基 RMSF、蛋白–环肽氢键及
（如有）相关自由能景观投影等。除非另有说明，上述参数均为 WebMD 默认方案。
```

**English**

```text
Protein–cyclic-peptide MD simulations were prepared and executed via the WebMD
automated pipeline. Both the protein and the cyclic peptide were described with
Amber ff14SB. The protein was repaired with PDBFixer; an N-to-C amide bond
restraint was applied to cyclize the peptide. The system was solvated with TIP3P
water, neutralized, and supplemented with ~0.15 M NaCl/KCl under periodic
boundary conditions. Amber topologies were converted to GROMACS.

The protocol comprised energy minimization; NVT (~500 ps) and NPT (~1 ns)
equilibration with heavy-atom position restraints on the solute; and unrestrained
production MD (V-rescale; C-rescale then Parrinello–Rahman; PME; dispersion
correction). Production length was ( ) ns with a 2 fs time step at ( ) K and
( ) bar. Trajectories were processed for PBC and fitted to the protein backbone;
solute-only trajectories were analyzed for RMSD/Rg/RMSF/hydrogen bonds as
provided by WebMD.
```

### 5.4 力场与流程速查（写论文时对照）

| 项目 | 小分子任务 | 线形肽任务 | 环肽任务 |
|------|------------|------------|----------|
| 蛋白力场 | Amber **ff14SB** | 同左 | 同左 |
| 配体力场 | **GAFF2** + **AM1-BCC** | **ff14SB**（保留末端） | **ff14SB**（头尾成环） |
| 水模型 | **TIP3P** | 同左 | 同左 |
| 盐 | 中和 + 约 **0.15 M** NaCl/KCl | 同左 | 同左 |
| 引擎 | **GROMACS** | 同左 | 同左 |
| 平衡 | EM → NVT（约束）→ NPT（约束）→ 生产（无约束） | 同左 | 同左 |
| 长程静电 / 色散 | **PME** / **DispCorr=EnerPres** | 同左 | 同左 |

---

## 6. 写 Results 时可用的“稳妥句式”

- “在（　）ns 生产模拟中，蛋白骨架 RMSD 在平衡后于一定范围内波动，提示整体构象相对稳定。”  
- “配体相对蛋白的 RMSD 维持在较低水平 / 出现升高，提示结合姿态保持稳定 / 发生明显重排（需结合轨迹目视确认）。”  
- “氢键分析显示蛋白与配体之间平均维持约（　）个氢键；残基时间图提示（残基名）等位点较高频参与相互作用。”  
- “上述 MD 结果从动力学角度支持……，但仍需结合结合实验 / 结构实验进一步验证。”

---

## 7. 常见问题

**Q：为什么 PDB 里看不到水？**  
A：交付的 `complex.pdb` / `fit.xtc` 默认是**去溶剂**的蛋白+配体，便于看结合和发邮件。全体系文件在同包的拓扑/轨迹相关文件中，一般湿实验可视化用不到。

**Q：PyMOL 打开 xtc 报错？**  
A：请确认先 load 了对应的 `complex.pdb`，再 load `fit.xtc` 到**同一对象名**；两者原子数需一致（都应为溶质轨迹）。

**Q：我能不能只把图发给合作者？**  
A：可以。建议同时附上本指南 + 任务 ID，方便对方写 Methods；并注明配体类型（小分子 / 线形肽 / 环肽），避免抄错模板。

---

## 8. 售后

- 微信：`biomd777`  
- 邮件里会带任务状态页链接；反馈时请提供 **任务 ID**

祝实验顺利。若本指南有看不懂的步骤，把截图和任务 ID 发给售后即可。
