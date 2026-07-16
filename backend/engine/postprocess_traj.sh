#!/bin/bash
# ==================================================
# 功能说明：MD 轨迹后处理（PBC 整分子 → nojump → 按骨架叠合复合物）
# 使用方法：在任务目录执行 bash postprocess_traj.sh
# 依赖环境：GROMACS (gmx)
# 生成时间：2026-07-14
# ==================================================
#
# 处理流程：
#   1) make_ndx → System / Backbone / Ligand / Complex(蛋白+配体)
#   2) trjconv -pbc mol      （输出 System）
#   3) trjconv -pbc nojump   （输出 System）
#   4) trjconv -fit rot+trans（先 Backbone 拟合，再输出 System；
#      使蛋白+配体随骨架刚体变换，且与 md.tpr/to.ndx 原子数一致）
#   5) dump 0 → complex.pdb （System）

set -uo pipefail

export PATH="/usr/local/gromacs/bin:${PATH}"
GMX="${GMX:-gmx}"
WD="$(cd "$(dirname "$0")" && pwd)"
cd "$WD"

GRO="md.gro"
if [ ! -f "$GRO" ]; then GRO="npt.gro"; fi
if [ ! -f "$GRO" ]; then GRO="system.gro"; fi
if [ ! -f "$GRO" ]; then
  echo "错误：未找到 gro 结构文件"
  exit 1
fi
if [ ! -f md.tpr ]; then
  echo "错误：未找到 md.tpr"
  exit 1
fi

echo "=== 轨迹后处理 ==="

# 从 ndx 按组名解析组号（按出现顺序从 0 递增）
_group_id() {
  local name="$1"
  local ndx="${2:-to.ndx}"
  awk -v name="$name" '
    BEGIN { i = 0 }
    /^\[/ {
      g = $0
      gsub(/^\[[[:space:]]*/, "", g)
      gsub(/[[:space:]]*\]$/, "", g)
      if (g == name) { print i; exit }
      i++
    }
  ' "$ndx"
}

# ndx 中最后一个组号
_last_group_id() {
  awk '/^\[/{n++} END{if(n>0) print n-1; else print 0}' to.ndx
}

# 从 gro 推断配体残基名（优先 LIG1/LIG2/LIG3）
_find_lig_res() {
  awk '
    BEGIN {
      aa = "ALA ARG ASN ASP CYS GLN GLU GLY HIS HID HIE HIP ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL ASH GLH LYN CYM"
      sol = "SOL WAT HOH TIP3 TIP4 NA CL NA+ CL- K K+ MG MG2 CA ZN ZN2"
    }
    NR <= 2 { next }
    {
      rn = toupper(substr($0, 6, 5))
      gsub(/ /, "", rn)
      if (rn == "") next
      if (index(" " aa " ", " " rn " ") || index(" " sol " ", " " rn " ")) next
      c[rn]++
    }
    END {
      n = split("LIG1 LIG2 LIG3", ord, " ")
      for (i = 1; i <= n; i++) if (c[ord[i]] > 0) { print ord[i]; found = 1 }
      if (found) exit
      n2 = split("UNL LIG MOL UNK UN1", pref, " ")
      for (i = 1; i <= n2; i++) if (c[pref[i]] > 0) { print pref[i]; exit }
      best = ""; bestn = 0
      for (k in c) if (c[k] > bestn) { bestn = c[k]; best = k }
      if (best != "") print best
    }
  ' "$GRO"
}

echo "=== 生成索引 to.ndx ==="
rm -f to.ndx
echo q | $GMX make_ndx -f "$GRO" -o to.ndx || {
  echo "错误：make_ndx 失败"
  exit 1
}

# 环肽：按 webmd_cyclic_peptide.json 中残基号范围建 Ligand / Receptor / Complex
if [ -f webmd_cyclic_peptide.json ]; then
  CYC_A=$(python3 -c "import json;d=json.load(open('webmd_cyclic_peptide.json'));print(int(d['resid_start']))" 2>/dev/null || true)
  CYC_B=$(python3 -c "import json;d=json.load(open('webmd_cyclic_peptide.json'));print(int(d['resid_end']))" 2>/dev/null || true)
  if [ -n "${CYC_A:-}" ] && [ -n "${CYC_B:-}" ]; then
    echo "检测到环肽残基号范围: ${CYC_A}-${CYC_B}"
    PROT_ID="$(_group_id Protein)"
    PROT_ID="${PROT_ID:-1}"
    NXT=$(( $(_last_group_id) + 1 ))
    {
      echo "ri ${CYC_A}-${CYC_B}"
      echo "name ${NXT} Ligand"
      echo "${PROT_ID} & ! ${NXT}"
      echo "name $((NXT+1)) Receptor"
      echo "$((NXT+1)) | ${NXT}"
      echo "name $((NXT+2)) Complex"
      echo q
    } | $GMX make_ndx -f "$GRO" -n to.ndx -o to.ndx || true
  fi
fi

LIG_ARR=()
while IFS= read -r _lig; do
  [ -n "$_lig" ] && LIG_ARR+=("$_lig")
done < <(_find_lig_res)
echo "检测到配体残基: ${LIG_ARR[*]:-（无）}"

# 已有环肽 Ligand 时跳过小分子残基名建组
if [ -z "$(_group_id Ligand)" ] && [ "${#LIG_ARR[@]}" -gt 0 ]; then
  # 为每个配体残基建组
  {
    for res in "${LIG_ARR[@]}"; do
      echo "r ${res}"
    done
    echo "q"
  } | $GMX make_ndx -f "$GRO" -n to.ndx -o to.ndx || true

  # 按残基名重命名为 Ligand / Ligand_RES
  RENAME=""
  for res in "${LIG_ARR[@]}"; do
    rid="$(_group_id "$res")"
    if [ -z "$rid" ]; then
      echo "警告：to.ndx 中未找到残基组 $res"
      continue
    fi
    if [ "${#LIG_ARR[@]}" -eq 1 ]; then
      gname="Ligand"
    else
      gname="Ligand_${res}"
    fi
    RENAME="${RENAME}name ${rid} ${gname}\n"
  done
  if [ -n "$RENAME" ]; then
    printf "%b" "${RENAME}q\n" | $GMX make_ndx -f "$GRO" -n to.ndx -o to.ndx || true
  fi

  # 多配体合并为 Ligand
  if [ "${#LIG_ARR[@]}" -gt 1 ] && [ -z "$(_group_id Ligand)" ]; then
    MERGE=""
    for res in "${LIG_ARR[@]}"; do
      lid="$(_group_id "Ligand_${res}")"
      [ -z "$lid" ] && continue
      if [ -z "$MERGE" ]; then MERGE="$lid"; else MERGE="${MERGE} | ${lid}"; fi
    done
    if [ -n "$MERGE" ]; then
      printf "%s\nq\n" "$MERGE" | $GMX make_ndx -f "$GRO" -n to.ndx -o to.ndx || true
      LAST="$(_last_group_id)"
      printf "name %s Ligand\nq\n" "$LAST" | $GMX make_ndx -f "$GRO" -n to.ndx -o to.ndx || true
    fi
  fi

  # Protein | Ligand → Complex（供后续分析选组；fit 输出仍用 System 以兼容 md.tpr）
  if [ -z "$(_group_id Complex)" ]; then
    PROT_ID="$(_group_id Protein)"
    LIG_ID="$(_group_id Ligand)"
    PROT_ID="${PROT_ID:-1}"
    if [ -n "$LIG_ID" ]; then
      printf "%s | %s\nq\n" "$PROT_ID" "$LIG_ID" | $GMX make_ndx -f "$GRO" -n to.ndx -o to.ndx || true
      LAST="$(_last_group_id)"
      printf "name %s Complex\nq\n" "$LAST" | $GMX make_ndx -f "$GRO" -n to.ndx -o to.ndx || true
    fi
  fi
fi

SYS_ID="$(_group_id System)"
BB_ID="$(_group_id Backbone)"
CPX_ID="$(_group_id Complex)"
LIG_ID="$(_group_id Ligand)"
SYS_ID="${SYS_ID:-0}"
BB_ID="${BB_ID:-4}"

echo "组号: System=${SYS_ID}  Backbone=${BB_ID}  Ligand=${LIG_ID:-—}  Complex=${CPX_ID:-—}"
echo "----- to.ndx 组列表 -----"
awk '/^\[/{print}' to.ndx

# 无轨迹：只保留 ndx / pdb
if [ ! -f md.xtc ] || [ ! -s md.xtc ]; then
  if [ ! -f complex.pdb ]; then
    $GMX editconf -f "$GRO" -o complex.pdb 2>/dev/null || cp -f "$GRO" complex.pdb 2>/dev/null || true
  fi
  echo "提示：未找到有效 md.xtc，已跳过 PBC/叠合"
  echo "=== 后处理完成（无轨迹模式）==="
  exit 0
fi

echo "=== md.xtc → mol.xtc (-pbc mol, System) ==="
rm -f mol.xtc nojump.xtc fit.xtc
echo "$SYS_ID" | $GMX trjconv -f md.xtc -s md.tpr -pbc mol -o mol.xtc -n to.ndx || {
  echo "错误：-pbc mol 失败"
  exit 1
}

echo "=== mol.xtc → nojump.xtc (-pbc nojump, System) ==="
echo "$SYS_ID" | $GMX trjconv -f mol.xtc -s md.tpr -pbc nojump -n to.ndx -o nojump.xtc || {
  echo "错误：-pbc nojump 失败"
  exit 1
}

echo "=== nojump.xtc → fit.xtc (-fit rot+trans, Backbone → System) ==="
# 第一选择：拟合参考 = Backbone
# 第二选择：输出 = System（全原子与 md.tpr/to.ndx 一致；蛋白+配体随骨架刚体变换，
#   避免「仅输出 Complex」导致原子数不匹配而使后续 RMSD 失败）
printf "%s\n%s\n" "$BB_ID" "$SYS_ID" | $GMX trjconv \
  -f nojump.xtc -s md.tpr -fit rot+trans -o fit.xtc -n to.ndx || {
  echo "错误：-fit rot+trans 失败"
  exit 1
}

if [ ! -f fit.xtc ] || [ ! -s fit.xtc ]; then
  echo "错误：未生成有效 fit.xtc"
  exit 1
fi

echo "=== 导出 complex.pdb (dump 0, System) ==="
rm -f complex.pdb
echo "$SYS_ID" | $GMX trjconv -f md.xtc -s md.tpr -dump 0 -o complex.pdb -n to.ndx || {
  echo 0 | $GMX trjconv -f md.xtc -s md.tpr -dump 0 -o complex.pdb 2>/dev/null || \
    $GMX editconf -f "$GRO" -o complex.pdb 2>/dev/null || true
}

# 清理中间轨迹，节省磁盘；保留原始 md.xtc 与分析用 fit.xtc
rm -f mol.xtc nojump.xtc

echo "=== 轨迹后处理完成：fit.xtc / to.ndx / complex.pdb ==="
