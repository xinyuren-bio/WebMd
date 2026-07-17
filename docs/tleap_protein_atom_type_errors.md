# tleap 蛋白原子类型报错处理手册

> 用途：记录「PDBFixer / 清洗 → tleap」阶段常见 `FATAL: Atom ... does not have a type` 等问题的**症状 → 原因 → 处理位置 → 禁忌**。  
> 新问题修完后，**先在本文件追加一条**，再改代码，避免逻辑散落、互相覆盖。

相关代码：

| 阶段 | 文件 | 职责 |
|------|------|------|
| 蛋白修复 | `backend/engine/protein.py` | PDBFixer 补原子/氢；HIS→HID/HIE/HIP；ASP/GLU→ASH/GLH |
| tleap 前清洗 | `backend/engine/system_builder.py` → `_clean_pdb_for_tleap` | 残基名映射、断链 TER、末端原子修正 |
| 末端修正 | `system_builder.py` → `_fix_terminal_atoms_for_tleap` | N 端 H→H1、异常 OXT、C 端非法 HC |
| 断链 / altLoc | `backend/engine/pdb_sanitize.py` | 去双构象、空间断链插 TER、按片段修末端 |
| 错误提示 | `system_builder.py` → `_tleap_error_hint` | 从 tleap 输出抽 FATAL 给前端/日志 |

处理管线（顺序勿乱）：

```
protein.pdb
  → prepare_protein()          # 先 resolve_altloc → protein_fixed.pdb（质子化命名）
  → _clean_pdb_for_tleap()     # protein_clean.pdb（再次去 altLoc / 末端 / 断链 / 改名）
  → tleap (ff14SB)
```

原则：

1. **命名与模板一致**：残基名必须对应 Amber 模板里真实存在的原子集合。
2. **有质子就改名，不要先删氢再假装去质子化**（除非用户明确要求去质子）。
3. **`_clean_pdb_for_tleap` 的 rename 表不要把已修好的 ASH/GLH 改回 ASP/GLU**。
4. **正确性优先**：不为“少报错”而改变化学态（质子化状态）。
5. **双构象只留一套**：不做多构象系综；择优规则见下文「altLoc」。

---

## 如何排查新 FATAL

1. 打开任务目录 `leap.log` / 报错里的 `Atom .R<RES n>.A<NAME k>`。
2. 在 `protein_fixed.pdb` 与 `protein_clean.pdb` 中定位该残基，列出全部原子名。
3. 对照 ff14SB 模板（如 `ASP` 无 `HD2`，`ASH` 有；`CARG`/`ARG`+`OXT` 无 `HC`）。
4. 判断属于下面哪一类，按「推荐处理」改代码，并在本文件追加条目。
5. 用失败任务重跑验证；确认 `protein_clean` 已无非法原子、`system.prmtop` 非空。

快速检查示例：

```bash
# 某残基原子名
awk '$1~/^(ATOM|HETATM)$/ && $5==474 {print $3,$4}' protein_fixed.pdb

# 是否还有 ASP+HD2 / C端 HC
grep -E ' HD2 .*ASP | HC  .*ARG ' protein_clean.pdb
```

---

## 已登记案例

### 1. HIS / HID / HIE / HIP

| 项 | 内容 |
|----|------|
| 症状 | `HIS` 侧链氢与模板不符；或 `does not have a type` |
| 原因 | PDB 写 `HIS`，Amber 需要按质子化态区分 HID/HIE/HIP |
| 处理 | `protein.py` → `_fix_histidine_protonation`：按 HD1/HE2 改名，删不匹配氢 |
| 禁忌 | 不要在 clean 阶段一律改成 HIS |

---

### 2. ASP+HD2 → ASH；GLU+HE2 → GLH

| 项 | 内容 |
|----|------|
| 症状 | `Atom .R<ASP n>.A<HD2 k> does not have a type`（GLU/HE2 同理） |
| 原因 | PDBFixer `addMissingHydrogens(pH=7)` 仍可能给羧基加氢，但残基名仍是 ASP/GLU；ff14SB 的 ASP/GLU **没有** HD2/HE2 |
| 处理 | `protein.py` → `_fix_carboxylic_protonation`：**改名为 ASH/GLH，保留 HD2/HE2** |
| 禁忌 | ~~删除 HD2~~（会改变质子化态）；~~在 `_clean_pdb_for_tleap` 把 ASH→ASP~~（会抵消修复） |
| 案例任务 | `72f2d33c0501`（ASP 281 + HD2） |
| 提交 | `040234b` |

---

### 3. C 端 ARG（tleap 记为 CARG）上的 HC

| 项 | 内容 |
|----|------|
| 症状 | `Atom .R<CARG n>.A<HC k> does not have a type` |
| 原因 | 残基含 `OXT`（带电 C 端），PDBFixer 有时多写羧基氢 `HC`/`HOXT`/`HXT`；C* 模板无此原子 |
| 处理 | `system_builder.py` → `_fix_terminal_atoms_for_tleap`：同一残基存在 `OXT` 时删除 `HC`/`HOXT`/`HXT` |
| 禁忌 | 不要删 `OXT` 来“迁就”HC（除非 OXT 几何异常，见下条） |
| 案例任务 | `72f2d33c0501`（残基 474） |
| 提交 | `040234b` |

说明：`protein_fixed.pdb` 里仍可能看到 HC；关键是 **`protein_clean.pdb` 进入 tleap 前必须去掉**。

---

### 4. N 端氢 H / HN → H1

| 项 | 内容 |
|----|------|
| 症状 | N 端无名类型原子；或 `FATAL: Atom .R<NASP n>.A<H …> does not have a type` |
| 原因 | OpenMM/PDBFixer/肽重建写 `H/H2/H3`，Amber N*（如 NASP）要 `H1/H2/H3` |
| 处理 | `_fix_terminal_atoms_for_tleap`：存在 H2+H3 且无 H1 时，将 H/HN/HT1 → H1；HT1/2/3 → H1/2/3。蛋白在 `_clean_pdb_for_tleap`；**线形肽**在 `prepare_linear_peptide` 与 `build_full_system_linear` 同样调用 |
| 禁忌 | 不要在无 H2/H3 时盲目把酰胺 H 改成 H1 |
| 案例任务 | ed41ec228f8d（线形肽 N 端 ASP→NASP 残留 H） |
| 日期 | 2026-07-17 |

---

### 5. 异常远的 C 端 OXT

| 项 | 内容 |
|----|------|
| 症状 | teLeap 长键警告 / 坐标异常 |
| 原因 | PDBFixer 放错 OXT，C–OXT > 2.0 Å |
| 处理 | `_fix_terminal_atoms_for_tleap`：删除该 OXT，交由 tleap 按 C* 模板重建 |
| 常量 | `_OXT_MAX_BOND_A = 2.0` |

---

### 6. 空间断链未插 TER

| 项 | 内容 |
|----|------|
| 症状 | 断点附近出现不该有的 H1/H2/H3；长键；`does not have a type` |
| 原因 | 一条链坐标上不连续，却仍按单链连肽键 |
| 处理 | `pdb_sanitize.sanitize_protein_lines` 插 TER + 按片段修末端；`_tleap_error_hint` 有对应中文提示 |
| 禁忌 | 不要只删氢而不分段 |

---

### 7. clean 阶段残基名映射（易踩坑）

`_clean_pdb_for_tleap` 当前映射（节选）：

- `CYM` → `CYS`，`LYN` → `LYS`，`HYP` → `PRO`
- **不要**再把 `ASH`→`ASP`、`GLH`→`GLU`（历史踩坑：会留下 HD2/HE2）

若新增「非标准 → 标准」映射：先确认目标残基模板是否包含当前全部原子；不包含则应改用 Amber 专用名（如 ASH），而不是硬改回标准名。

---

### 8. 晶体双构象（altLoc A/B）未去重

| 项 | 内容 |
|----|------|
| 症状 | 同一残基出现两套 `CA A`/`CA B`；或一套被写成 `C01`/`C02` 导致 `FATAL: Atom ... does not have a type` |
| 原因 | 晶体学交替构象；MD/tleap 一次只能用一套坐标 |
| 处理 | `pdb_sanitize.resolve_altloc_lines`：无标号原子保留；多套时优先标准原子名更多者，其次 occupancy，再字母序（A>B）；清空 altLoc 且 occupancy→1.00。`prepare_protein` 在 PDBFixer 前调用；`sanitize_protein_lines` 再保险一次 |
| 禁忌 | 不要两套都留给 tleap；不要在未择优时盲目只删 A 或只删 B（命名坏的那套可能是 A） |
| 案例任务 | c3b7b95e621e（ASN 双构象 / C01 命名） |
| 日期 | 2026-07-17 |

---

## 新增案例模板（复制后填写）

```markdown
### N. <残基/原子简短标题>

| 项 | 内容 |
|----|------|
| 症状 | tleap 原文或关键片段 |
| 原因 | 化学/命名/工具链哪一步写错 |
| 处理 | 文件 → 函数；改名还是删原子；为何这样选 |
| 禁忌 | 不要做什么（尤其避免改变质子化或互相覆盖） |
| 案例任务 | task_id（可选） |
| 提交 | commit hash（可选） |
| 日期 | YYYY-MM-DD |
```

代码改动清单建议：

1. 本文件追加条目  
2. 实现放在正确阶段（`protein.py` = 质子化命名；`_fix_terminal_*` = 末端几何/命名；不要塞进无关模块）  
3. 如有必要，更新 `_tleap_error_hint` 的中文提示  
4. 用原失败任务重跑，确认 `leap.log` 无同类 FATAL  

---

## 维护备忘

- 文档与代码冲突时：**以当前代码为准**，并立刻改本文对应条目。  
- 同类报错增多时，优先查本表是否已有「禁忌」被新代码违反（例如又把 ASH 改回 ASP）。  
- 配体 GAFF / antechamber 报错不在本手册范围；本手册仅覆盖 **蛋白进 tleap（ff14SB）**。
