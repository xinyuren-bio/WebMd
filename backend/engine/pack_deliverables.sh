#!/bin/bash
# ==================================================
# 功能说明：打包模拟数据包与分析结果包；交付 pdb/xtc 仅含蛋白+配体
# 使用方法：在任务目录执行 bash pack_deliverables.sh
# 依赖环境：zip；可选 GROMACS（用于从全体系轨迹抽取 Complex）
# 生成时间：2026-07-16
# ==================================================

set -uo pipefail

WD="$(cd "$(dirname "$0")" && pwd)"
cd "$WD"

export PATH="/usr/local/gromacs/bin:${PATH}"
GMX="${GMX:-gmx}"

SIM_ZIP="simulation_deliverables.zip"
ANAL_ZIP="analysis_deliverables.zip"
README="交付说明.txt"
STAGE=".deliver_stage"

# 从 ndx 按组名解析组号
_group_id() {
  local name="$1"
  local ndx="${2:-to.ndx}"
  [ -f "$ndx" ] || return 0
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

echo "=== 打包模拟数据压缩包 ==="

# 补全 ndx / pdb
if [ ! -f to.ndx ] && [ -f md.gro ]; then
  echo q | $GMX make_ndx -f md.gro -o to.ndx 2>/dev/null || true
fi
if [ ! -f complex.pdb ] && [ -f md.gro ]; then
  $GMX editconf -f md.gro -o complex.pdb 2>/dev/null || true
fi

MISS=""
for f in to.ndx system.top md.tpr; do
  if [ ! -f "$f" ]; then
    MISS="$MISS $f"
  fi
done
if [ -n "$MISS" ]; then
  echo "错误：缺少必要文件:$MISS"
  exit 1
fi

# ---------- 交付用：仅蛋白 + 配体（无水/离子）----------
# 分析用 fit_system.xtc；交付 fit.xtc/complex.pdb 仅含蛋白+配体（无水/离子）
CPX_ID="$(_group_id Complex)"
PROT_ID="$(_group_id Protein)"
LIG_ID="$(_group_id Ligand)"
REC_ID="$(_group_id Receptor)"
SOLUTE_ID=""
SOLUTE_NAME=""
if [ -n "${CPX_ID:-}" ]; then
  SOLUTE_ID="$CPX_ID"
  SOLUTE_NAME="Complex"
elif [ -n "${REC_ID:-}" ] && [ -n "${LIG_ID:-}" ]; then
  # 有 Receptor+Ligand 但无 Complex 时临时合并（少见）
  SOLUTE_ID=""
  SOLUTE_NAME="Receptor+Ligand"
elif [ -n "${PROT_ID:-}" ] && [ -n "${LIG_ID:-}" ]; then
  SOLUTE_ID=""
  SOLUTE_NAME="Protein+Ligand"
elif [ -n "${PROT_ID:-}" ]; then
  SOLUTE_ID="$PROT_ID"
  SOLUTE_NAME="Protein"
fi

rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -f to.ndx system.top md.tpr "$STAGE/"

DELIV_PDB="$STAGE/complex.pdb"
DELIV_XTC="$STAGE/fit.xtc"
HAVE_XTC=0

_extract_solute() {
  local src_xtc="$1"
  local out_xtc="$2"
  local out_pdb="$3"
  local gid="$4"
  echo "交付轨迹/结构：抽取组 ${SOLUTE_NAME} (id=${gid}) ← ${src_xtc}"
  echo "$gid" | $GMX trjconv -f "$src_xtc" -s md.tpr -n to.ndx -o "$out_xtc" 2>/dev/null || return 1
  echo "$gid" | $GMX trjconv -f "$src_xtc" -s md.tpr -n to.ndx -dump 0 -o "$out_pdb" 2>/dev/null || return 1
  return 0
}

_extract_by_merge() {
  # 无 Complex 组时：用 Protein|Ligand 或 Receptor|Ligand 写临时 ndx 再抽取
  local src_xtc="$1"
  local a="$2"
  local b="$3"
  local tmpn="to_deliver_tmp.ndx"
  cp -f to.ndx "$tmpn"
  printf "%s | %s\nq\n" "$a" "$b" | $GMX make_ndx -f md.tpr -n "$tmpn" -o "$tmpn" 2>/dev/null || return 1
  local last
  last=$(awk '/^\[/{n++} END{if(n>0) print n-1; else print 0}' "$tmpn")
  echo "$last" | $GMX trjconv -f "$src_xtc" -s md.tpr -n "$tmpn" -o "$DELIV_XTC" 2>/dev/null || {
    rm -f "$tmpn"
    return 1
  }
  echo "$last" | $GMX trjconv -f "$src_xtc" -s md.tpr -n "$tmpn" -dump 0 -o "$DELIV_PDB" 2>/dev/null || {
    rm -f "$tmpn"
    return 1
  }
  rm -f "$tmpn"
  return 0
}

SRC_XTC=""
# 抽取溶质必须从全体系轨迹出发（与 md.tpr 一致）
if [ -f fit_system.xtc ] && [ -s fit_system.xtc ]; then
  SRC_XTC="fit_system.xtc"
elif [ -f fit.xtc ] && [ -s fit.xtc ]; then
  SRC_XTC="fit.xtc"
elif [ -f md.xtc ] && [ -s md.xtc ]; then
  SRC_XTC="md.xtc"
fi

EXTRACT_OK=0
# 若工作区 fit.xtc 已是溶质且存在 Complex，优先直接复制（避免对溶质轨迹再套 tpr 失败）
if [ -f fit.xtc ] && [ -s fit.xtc ] && [ -f complex.pdb ] && [ -s complex.pdb ] && [ -n "${CPX_ID:-}" ] && [ -f fit_system.xtc ]; then
  # 新流程：postprocess 已写出溶质 fit.xtc / complex.pdb
  echo "交付轨迹/结构：使用后处理已生成的溶质 fit.xtc / complex.pdb"
  cp -f fit.xtc "$DELIV_XTC"
  cp -f complex.pdb "$DELIV_PDB"
  EXTRACT_OK=1
  HAVE_XTC=1
  SOLUTE_NAME="${SOLUTE_NAME:-Complex}"
fi

if [ "$EXTRACT_OK" -eq 0 ] && [ -n "$SRC_XTC" ] && [ -n "${SOLUTE_ID:-}" ]; then
  if _extract_solute "$SRC_XTC" "$DELIV_XTC" "$DELIV_PDB" "$SOLUTE_ID"; then
    EXTRACT_OK=1
    HAVE_XTC=1
  fi
elif [ "$EXTRACT_OK" -eq 0 ] && [ -n "$SRC_XTC" ] && [ -n "${LIG_ID:-}" ]; then
  A="${REC_ID:-$PROT_ID}"
  if [ -n "${A:-}" ] && _extract_by_merge "$SRC_XTC" "$A" "$LIG_ID"; then
    EXTRACT_OK=1
    HAVE_XTC=1
    SOLUTE_NAME="Protein+Ligand"
  fi
fi

# 仅有溶质 fit.xtc、无 fit_system 的旧任务：直接打包现有文件
if [ "$EXTRACT_OK" -eq 0 ] && [ -f fit.xtc ] && [ -s fit.xtc ] && [ -f complex.pdb ]; then
  echo "交付：直接打包现有 fit.xtc / complex.pdb"
  cp -f fit.xtc "$DELIV_XTC"
  cp -f complex.pdb "$DELIV_PDB"
  EXTRACT_OK=1
  HAVE_XTC=1
fi
if [ "$EXTRACT_OK" -eq 0 ]; then
  echo "警告：无法抽取蛋白+配体轨迹，回退为现有 complex.pdb / 全体系轨迹（可能含水）"
  if [ -f complex.pdb ]; then
    cp -f complex.pdb "$DELIV_PDB"
  elif [ -f md.gro ]; then
    $GMX editconf -f md.gro -o "$DELIV_PDB" 2>/dev/null || true
  fi
  if [ -n "$SRC_XTC" ]; then
    cp -f "$SRC_XTC" "$DELIV_XTC"
    HAVE_XTC=1
  fi
fi

if [ ! -f "$DELIV_PDB" ]; then
  echo "错误：未能生成交付用 complex.pdb"
  exit 1
fi

{
  echo "WebMD 模拟交付包"
  echo "任务目录: $(basename "$WD")"
  echo ""
  echo "【结构与轨迹】"
  if [ "$EXTRACT_OK" -eq 1 ]; then
    echo "complex.pdb / fit.xtc：仅含蛋白 + 配体/肽（组 ${SOLUTE_NAME}），不含溶剂与离子。"
  else
    echo "complex.pdb / fit.xtc：未能自动去溶剂，可能仍含全体系原子，请自行用 to.ndx 筛选。"
  fi
  if [ "$HAVE_XTC" -eq 1 ]; then
    echo "轨迹: fit.xtc（相对蛋白骨架叠合后的溶质轨迹）"
  else
    echo "轨迹: 无（短测试或未输出 xtc）"
  fi
  echo ""
  echo "【拓扑】"
  echo "system.top / md.tpr / to.ndx：仍为全体系（含水/离子），便于在 GROMACS 中复现或再分析。"
} > "$STAGE/$README"

rm -f "$SIM_ZIP"
(
  cd "$STAGE"
  ZIP_LIST=(to.ndx system.top md.tpr complex.pdb "$README")
  if [ "$HAVE_XTC" -eq 1 ] && [ -s fit.xtc ]; then
    ZIP_LIST+=(fit.xtc)
  fi
  zip -j -q "../$SIM_ZIP" "${ZIP_LIST[@]}"
)
rm -rf "$STAGE"

if [ ! -s "$SIM_ZIP" ]; then
  echo "错误：$SIM_ZIP 为空"
  exit 1
fi
echo "已生成 $SIM_ZIP ($(du -h "$SIM_ZIP" | awk '{print $1}'))"

echo "=== 打包分析结果压缩包 ==="
mkdir -p analysis_csv analysis_plots
NCSV=$(find analysis_csv -maxdepth 1 -name '*.csv' 2>/dev/null | wc -l | tr -d ' ')
if [ "${NCSV:-0}" -eq 0 ] && [ -f gmx_only_analyze.sh ]; then
  echo "分析 CSV 为空，尝试 GMX 备用分析…"
  bash gmx_only_analyze.sh >> md_analysis.log 2>&1 || true
fi
if [ ! -f analysis_csv/README.txt ] && [ "$(find analysis_csv -name '*.csv' | wc -l)" -eq 0 ]; then
  echo "未能生成分析 CSV（请检查轨迹与 Python/GROMACS 环境）。" > analysis_csv/README.txt
fi
# 兜底：CSV 不得留在 plots（历史逻辑曾把 hbond csv 写进 plots）
if [ -d analysis_plots ]; then
  find analysis_plots -maxdepth 1 -type f -name '*.csv' -print -delete 2>/dev/null || true
fi
rm -f "$ANAL_ZIP"
zip -r -q "$ANAL_ZIP" analysis_csv analysis_plots
if [ ! -s "$ANAL_ZIP" ]; then
  echo "错误：$ANAL_ZIP 为空"
  exit 1
fi
echo "已生成 $ANAL_ZIP"
