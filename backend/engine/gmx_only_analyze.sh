#!/bin/bash
# ==================================================
# 功能说明：无 Python 时的 GROMACS 轨迹分析（输出 CSV）
# 使用方法：bash gmx_only_analyze.sh
# 依赖环境：GROMACS gmx、awk
# 生成时间：2026-07-13
# ==================================================

set -uo pipefail

export PATH="/usr/local/gromacs/bin:/usr/bin:/bin:/root/miniconda3/bin:$PATH"
GMX="${GMX:-gmx}"
WD="$(cd "$(dirname "$0")" && pwd)"
cd "$WD"

mkdir -p analysis_csv analysis_plots

XTCP="fit_system.xtc"
if [ ! -s "$XTCP" ]; then XTCP="fit.xtc"; fi
if [ ! -s "$XTCP" ]; then XTCP="md.xtc"; fi
if [ ! -f md.tpr ] || [ ! -s "$XTCP" ]; then
  echo "无有效轨迹，跳过 GMX 分析"
  exit 0
fi

_xvg转csv() {
  local xvg="$1" csv="$2" h1="$3" h2="$4"
  [ -f "$xvg" ] || return 1
  echo "$h1,$h2" > "$csv"
  awk '!/^[#@]/ && NF>=2 {print $1","$2}' "$xvg" >> "$csv"
}

echo "=== GMX 备用分析（CSV）==="
if echo -e "1\n1" | $GMX rms -s md.tpr -f "$XTCP" -o analysis_rmsd.xvg -tu ns 2>/dev/null; then
  _xvg转csv analysis_rmsd.xvg analysis_csv/rmsd.csv time_ns rmsd_nm
fi
if echo 1 | $GMX gyrate -s md.tpr -f "$XTCP" -o analysis_rg.xvg 2>/dev/null; then
  _xvg转csv analysis_rg.xvg analysis_csv/rg.csv time_ns rg_nm
fi
if echo 1 | $GMX rmsf -s md.tpr -f "$XTCP" -o analysis_rmsf.xvg -res 2>/dev/null; then
  _xvg转csv analysis_rmsf.xvg analysis_csv/rmsf.csv residue rmsf_nm
fi
if [ -f md.edr ]; then
  if echo -e "10\n0" | $GMX energy -f md.edr -o analysis_energy.xvg 2>/dev/null; then
    _xvg转csv analysis_energy.xvg analysis_csv/energy.csv time_ps potential_kj_mol
  fi
fi

# 使用 AutoDL 默认 python（3.8）出图
PY="python"
if ! command -v "$PY" >/dev/null 2>&1; then PY="/usr/bin/python"; fi
if command -v "$PY" >/dev/null 2>&1 && [ -f traj_analyze.py ]; then
  $PY traj_analyze.py --workdir "$WD" --out analysis_summary_gmx.txt 2>/dev/null || true
  # traj_analyze 会覆盖 summary；此处仅补图表
  for xvg in analysis_rmsd.xvg analysis_rg.xvg analysis_rmsf.xvg; do
    [ -f "$xvg" ] || continue
    $PY - "$xvg" "analysis_plots/${xvg%.xvg}.png" <<'PYEOF' 2>/dev/null || true
import sys
from pathlib import Path
xvg, png = sys.argv[1], sys.argv[2]
xs, ys = [], []
for ln in Path(xvg).read_text().splitlines():
    s = ln.strip()
    if not s or s[0] in "#@": continue
    p = s.split()
    if len(p) >= 2:
        xs.append(float(p[0])); ys.append(float(p[1]))
if not xs: raise SystemExit
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.figure(figsize=(6,3)); plt.plot(xs, ys, lw=0.8); plt.tight_layout(); plt.savefig(png, dpi=120)
PYEOF
  done
fi

echo "=== GMX 备用分析完成 ==="
