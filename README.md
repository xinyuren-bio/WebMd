<p align="center">
  <img src="frontend/assets/images/logo.png" alt="WebMD" width="72" height="72">
</p>

<h1 align="center">WebMD</h1>

<p align="center">
  <b>蛋白–配体分子动力学 · 在线体系搭建与结果交付</b>
</p>

<p align="center">
  上传结构 → 自动力场与溶剂化 → 下载 GROMACS 体系包<br>
  支持小分子 · 环肽 · 线形肽
</p>

<p align="center">
  <a href="http://8.219.168.5:8000/"><img src="https://img.shields.io/badge/进入网站-WebMD-0d9488?style=for-the-badge&logo=googlechrome&logoColor=white" alt="Enter WebMD"></a>
  &nbsp;
  <img src="https://img.shields.io/badge/Engine-GROMACS-6366f1?style=for-the-badge" alt="GROMACS">
  &nbsp;
  <img src="https://img.shields.io/badge/Force%20Field-ff14SB%20%2B%20GAFF2-0891b2?style=for-the-badge" alt="Force Field">
</p>

<p align="center">
  <a href="http://8.219.168.5:8000/"><strong>👉 http://8.219.168.5:8000/</strong></a>
</p>

---

## 一键进入

| 步骤 | 操作 |
|:----:|------|
| 1 | 打开 **[WebMD 网站](http://8.219.168.5:8000/)** |
| 2 | 右上角 **注册 / 登录**（提交任务与下载需要账号） |
| 3 | 点击 **「体系准备」** 或首页 **「开始体系准备」** |
| 4 | 按下方流程上传结构并提交 |

> 网页端暂不提供轨迹在线分析。模拟完成后，分析报告会发送到注册邮箱。

---

## 使用流程

```text
登录  →  选择配体类型  →  上传结构  →  3D 预览核对
     →  设置参数  →  提交构建  →  下载结果包 / 云端 MD
```

### 1. 选择配体类型

在「上传文件」处选择：

| 类型 | 上传文件 | 力场 |
|------|----------|------|
| **小分子** | 蛋白 PDB + 配体 MOL2（1–3 个） | 蛋白 ff14SB · 配体 GAFF2 / AM1-BCC |
| **环肽** | 蛋白 PDB + **仅环肽** PDB | 双方 ff14SB · 自动头尾 N–C 成环 |
| **线形肽** | 蛋白 PDB + **仅线形肽** PDB | 双方 ff14SB · 保留 N/C 末端 |

### 2. 准备你的结构文件

**小分子**
- 蛋白与配体请在 PyMOL / ChimeraX 中**预先摆好相对位置**再分别导出
- 配体需为 **MOL2**（若是 SDF：`obabel ligand.sdf -O ligand.mol2`）
- 对接复合物可先按站内「使用教程」拆成蛋白 PDB + 配体文件

**环肽 / 线形肽**
- 第二个文件必须是**肽本身**，不要上传整条蛋白或复合物
- 目前仅支持**标准氨基酸**；肽 PDB 建议使用规范 ATOM 原子名
- 环肽：请保证首尾几何已适合成键；线形肽：保留末端即可，无需手动成环

### 3. 3D 预览

两个文件都选好后，页面会自动显示复合物预览：
- 蛋白：**Cartoon**
- 配体 / 肽：**球棍**（按元素着色）

可用工具栏切换显示样式，确认口袋与姿态无误后再提交。

### 4. 模拟参数

常用默认已可用，按需调整例如：
- 温度、压强、溶剂盒子边距
- 离子浓度与盐种类（NaCl / KCl）
- 模拟时长（10 / 100 / 200 ns，用于后续云端 MD）

### 5. 提交与下载

1. 点击开始构建，等待流水线完成（蛋白修复 → 参数化 → 溶剂化 → GROMACS 转换）
2. 小分子若自动净电荷失败，页面会弹出**净电荷确认**（不会静默改电荷）
3. 完成后下载 `tar.gz` 结果包；也可按站内指引进行付费云端 MD
4. 云端模拟结束后，分析报告发至邮箱

---

## 你将获得什么

解压结果包后，典型内容包括：

```text
system.gro / system.top     GROMACS 坐标与拓扑
system.prmtop / inpcrd      Amber 参考拓扑
mdp/                        em · nvt · npt · md
run_md.sh                   一键运行脚本
FORCEFIELD.txt              力场与净电荷说明（若有配体）
```

本地有 GROMACS 时可自行运行：

```bash
tar xzf gromacs_md_*.tar.gz -C md_run && cd md_run
bash run_md.sh
```

---

## 科学设置一览

| 项目 | 设置 |
|------|------|
| 蛋白 / 肽 | Amber **ff14SB** |
| 小分子 | **GAFF2** + **AM1-BCC** |
| 溶剂 | TIP3P · 可加盐 |
| 引擎 | GROMACS（PME · V-rescale · Parrinello–Rahman） |

---

## 小贴士

- 提交前请先**登录**
- 肽类文件原子数应远小于蛋白；若预览满屏棍状，多半是把复合物当成肽上传了
- 站内 **「使用教程」** 含拆分对接复合物等说明与视频
- 任务状态页可收藏，便于回查进度

---

<p align="center">
  <a href="http://8.219.168.5:8000/"><strong>打开 WebMD →</strong></a>
  &nbsp;·&nbsp;
  © 2026 WebMD
</p>
