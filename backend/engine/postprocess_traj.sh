#!/bin/bash
# ==================================================
# 功能说明：MD 轨迹后处理（PBC 整分子 → nojump → 按骨架叠合；交付去溶剂）
# 使用方法：在任务目录执行 bash postprocess_traj.sh
# 依赖环境：GROMACS (gmx)；肽映射可选 peptide_resid_map.py
# 生成时间：2026-07-16
# ==================================================
#
# 处理流程：
#   1) make_ndx → System / Backbone / Ligand / Complex(蛋白+配体，无溶剂)
#   2) trjconv -pbc mol / nojump（输出 System）
#   3) trjconv -fit → fit_system.xtc（全体系，供分析与 md.tpr 原子数一致）
#   4) 抽取 Complex → fit.xtc + complex.pdb（仅蛋白+肽/配体，便于邮件发送）
#   注：分析脚本优先读 fit_system.xtc；交付包中的 fit.xtc 为溶质轨迹

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

# 肽实际残基号（按序列映射到 gro，禁止用设计号 9001）
_peptide_ri_range() {
  if [ -f peptide_resid_map.py ]; then
    python3 peptide_resid_map.py "$WD" 2>/dev/null || true
  elif [ -f webmd_cyclic_peptide.json ]; then
    python3 -c "
import json
d=json.load(open('webmd_cyclic_peptide.json'))
a=int(d.get('resid_gmx_start') or 0); b=int(d.get('resid_gmx_end') or 0)
print(f'{a} {b}' if a>0 and b>=a else '')
" 2>/dev/null || true
  fi
}

echo "=== 生成索引 to.ndx ==="
rm -f to.ndx
echo q | $GMX make_ndx -f "$GRO" -o to.ndx || {
  echo "错误：make_ndx 失败"
  exit 1
}

# 环肽/线形肽：按 gro 实际残基号建 Ligand / Receptor / Complex
if [ -f webmd_cyclic_peptide.json ]; then
  PEPR="$(_peptide_ri_range)"
  CYC_A=$(echo "$PEPR" | awk '{print $1}')
  CYC_B=$(echo "$PEPR" | awk '{print $2}')
  if [ -n "${CYC_A:-}" ] && [ -n "${CYC_B:-}" ] && [ "${CYC_A}" -gt 0 ] 2>/dev/null; then
    echo "检测到肽实际残基号范围: ${CYC_A}-${CYC_B}（已映射，非设计号）"
    PROT_ID="$(_group_id Protein)"
    BB0="$(_group_id Backbone)"
    PROT_ID="${PROT_ID:-1}"
    BB0="${BB0:-4}"
    NXT=$(( $(_last_group_id) + 1 ))
    {
      echo "ri ${CYC_A}-${CYC_B}"
      echo "name ${NXT} Ligand"
      echo "${PROT_ID} & ! ${NXT}"
      echo "name $((NXT+1)) Receptor"
      echo "$((NXT+1)) | ${NXT}"
      echo "name $((NXT+2)) Complex"
      echo "$((NXT+1)) & ${BB0}"
      echo "name $((NXT+3)) Receptor_Backbone"
      echo q
    } | $GMX make_ndx -f "$GRO" -n to.ndx -o to.ndx || true
  else
    echo "警告：无法将肽序列映射到 gro 残基号，跳过肽 Ligand 建组"
  fi
fi

LIG_ARR=()
while IFS= read -r _lig; do
  [ -n "$_lig" ] && LIG_ARR+=("$_lig")
done < <(_find_lig_res)
echo "检测到配体残基: ${LIG_ARR[*]:-（无）}"

# 已有肽 Ligand 时跳过小分子残基名建组
if [ -z "$(_group_id Ligand)" ] && [ "${#LIG_ARR[@]}" -gt 0 ]; then
  {
    for res in "${LIG_ARR[@]}"; do
      echo "r ${res}"
    done
    echo "q"
  } | $GMX make_ndx -f "$GRO" -n to.ndx -o to.ndx || true

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
BB_ID="$(_group_id Receptor_Backbone)"
[ -z "$BB_ID" ] && BB_ID="$(_group_id Backbone)"
CPX_ID="$(_group_id Complex)"
LIG_ID="$(_group_id Ligand)"
SYS_ID="${SYS_ID:-0}"
BB_ID="${BB_ID:-4}"

echo "组号: System=${SYS_ID}  FitRef=${BB_ID}  Ligand=${LIG_ID:-—}  Complex=${CPX_ID:-—}"
echo "----- to.ndx 组列表 -----"
awk '/^\[/{print}' to.ndx

# 无轨迹：只保留 ndx / pdb（溶质优先）
if [ ! -f md.xtc ] || [ ! -s md.xtc ]; then
  rm -f complex.pdb
  if [ -n "${CPX_ID:-}" ]; then
    echo "$CPX_ID" | $GMX trjconv -f "$GRO" -s md.tpr -o complex.pdb -n to.ndx 2>/dev/null || \
      $GMX editconf -f "$GRO" -o complex.pdb 2>/dev/null || true
  else
    $GMX editconf -f "$GRO" -o complex.pdb 2>/dev/null || cp -f "$GRO" complex.pdb 2>/dev/null || true
  fi
  echo "提示：未找到有效 md.xtc，已跳过 PBC/叠合"
  echo "=== 后处理完成（无轨迹模式）==="
  exit 0
fi

echo "=== md.xtc → mol.xtc (-pbc mol, System) ==="
rm -f mol.xtc nojump.xtc fit_system.xtc fit.xtc
echo "$SYS_ID" | $GMX trjconv -f md.xtc -s md.tpr -pbc mol -o mol.xtc -n to.ndx || {
  echo "错误：-pbc mol 失败"
  exit 1
}

echo "=== mol.xtc → nojump.xtc (-pbc nojump, System) ==="
echo "$SYS_ID" | $GMX trjconv -f mol.xtc -s md.tpr -pbc nojump -n to.ndx -o nojump.xtc || {
  echo "错误：-pbc nojump 失败"
  exit 1
}

echo "=== nojump.xtc → fit_system.xtc (-fit rot+trans, 参考骨架 → System) ==="
printf "%s\n%s\n" "$BB_ID" "$SYS_ID" | $GMX trjconv \
  -f nojump.xtc -s md.tpr -fit rot+trans -o fit_system.xtc -n to.ndx || {
  echo "错误：-fit rot+trans 失败"
  exit 1
}

if [ ! -f fit_system.xtc ] || [ ! -s fit_system.xtc ]; then
  echo "错误：未生成有效 fit_system.xtc"
  exit 1
fi

echo "=== 抽取蛋白+配体 → fit.xtc / complex.pdb（去溶剂与离子）==="
rm -f complex.pdb fit.xtc
EXTRACT_GID="${CPX_ID:-}"
if [ -z "$EXTRACT_GID" ] && [ -n "${LIG_ID:-}" ]; then
  PROT_ID="$(_group_id Receptor)"
  [ -z "$PROT_ID" ] && PROT_ID="$(_group_id Protein)"
  if [ -n "${PROT_ID:-}" ]; then
    printf "%s | %s\nq\n" "$PROT_ID" "$LIG_ID" | $GMX make_ndx -f "$GRO" -n to.ndx -o to.ndx || true
    EXTRACT_GID="$(_last_group_id)"
    printf "name %s Complex\nq\n" "$EXTRACT_GID" | $GMX make_ndx -f "$GRO" -n to.ndx -o to.ndx || true
    CPX_ID="$(_group_id Complex)"
    EXTRACT_GID="${CPX_ID:-$EXTRACT_GID}"
  fi
fi

if [ -n "${EXTRACT_GID:-}" ]; then
  echo "$EXTRACT_GID" | $GMX trjconv -f fit_system.xtc -s md.tpr -n to.ndx -o fit.xtc || {
    echo "警告：抽取溶质轨迹失败"
  }
  echo "$EXTRACT_GID" | $GMX trjconv -f fit_system.xtc -s md.tpr -n to.ndx -dump 0 -o complex.pdb || true
fi

if [ ! -f fit.xtc ] || [ ! -s fit.xtc ]; then
  echo "警告：未能生成仅溶质 fit.xtc，回退复制 fit_system.xtc（含水）"
  cp -f fit_system.xtc fit.xtc
fi
if [ ! -f complex.pdb ] || [ ! -s complex.pdb ]; then
  echo "$SYS_ID" | $GMX trjconv -f fit_system.xtc -s md.tpr -dump 0 -o complex.pdb -n to.ndx 2>/dev/null || \
    $GMX editconf -f "$GRO" -o complex.pdb 2>/dev/null || true
fi

rm -f mol.xtc nojump.xtc

echo "=== 轨迹后处理完成：fit_system.xtc(分析) / fit.xtc(溶质) / complex.pdb(溶质) / to.ndx ==="
