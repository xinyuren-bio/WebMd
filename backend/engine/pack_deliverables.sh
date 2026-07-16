#!/bin/bash
# ==================================================
# 功能说明：打包模拟数据包与分析结果包（xtc 可选，适配短测试）
# 使用方法：在任务目录执行 bash pack_deliverables.sh
# 依赖环境：zip
# 生成时间：2026-07-13
# ==================================================

set -uo pipefail

WD="$(cd "$(dirname "$0")" && pwd)"
cd "$WD"

SIM_ZIP="simulation_deliverables.zip"
ANAL_ZIP="analysis_deliverables.zip"
README="交付说明.txt"

echo "=== 打包模拟数据压缩包 ==="

# 补全 ndx / pdb
if [ ! -f to.ndx ] && [ -f md.gro ]; then
  export PATH="/usr/local/gromacs/bin:${PATH}"
  echo q | gmx make_ndx -f md.gro -o to.ndx 2>/dev/null || true
fi
if [ ! -f complex.pdb ] && [ -f md.gro ]; then
  export PATH="/usr/local/gromacs/bin:${PATH}"
  gmx editconf -f md.gro -o complex.pdb 2>/dev/null || true
fi

MISS=""
for f in to.ndx system.top md.tpr complex.pdb; do
  if [ ! -f "$f" ]; then
    MISS="$MISS $f"
  fi
done
if [ -n "$MISS" ]; then
  echo "错误：缺少必要文件:$MISS"
  exit 1
fi

{
  echo "WebMD 模拟交付包"
  echo "任务目录: $(basename "$WD")"
  if [ -f fit.xtc ] && [ -s fit.xtc ]; then
    echo "轨迹: fit.xtc（已叠合）"
  elif [ -f md.xtc ] && [ -s md.xtc ]; then
    echo "轨迹: md.xtc（原始）"
  else
    echo "轨迹: 无（短测试或未输出 xtc，仅含拓扑/结构/ tpr）"
  fi
} > "$README"

rm -f "$SIM_ZIP"
ZIP_LIST=(to.ndx system.top md.tpr complex.pdb "$README")
if [ -f fit.xtc ] && [ -s fit.xtc ]; then
  ZIP_LIST+=(fit.xtc)
elif [ -f md.xtc ] && [ -s md.xtc ]; then
  ZIP_LIST+=(md.xtc)
fi
zip -j -q "$SIM_ZIP" "${ZIP_LIST[@]}"
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
rm -f "$ANAL_ZIP"
zip -r -q "$ANAL_ZIP" analysis_csv analysis_plots
if [ ! -s "$ANAL_ZIP" ]; then
  echo "错误：$ANAL_ZIP 为空"
  exit 1
fi
echo "已生成 $ANAL_ZIP"
