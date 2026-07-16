#!/bin/bash
# ==================================================
# 功能说明：AutoDL 远程后处理 + 分析 + 打包
# 使用方法：bash run_traj_analysis.sh
# 生成时间：2026-07-13
# ==================================================

set -uo pipefail

export PATH="/usr/local/gromacs/bin:/usr/bin:/bin:/root/miniconda3/bin:$PATH"
WD="$(cd "$(dirname "$0")" && pwd)"
cd "$WD"
LOG="${WD}/md_analysis.log"

_log() { echo "$@" | tee -a "$LOG"; }

_log "=== 安装分析依赖（若缺失）==="
PY="python"
if ! command -v "$PY" >/dev/null 2>&1; then
  PY="/usr/bin/python"
fi
if command -v "$PY" >/dev/null 2>&1; then
  $PY -m pip install -q numpy matplotlib MDAnalysis 2>>"$LOG" || true
else
  _log "警告：未找到 python，将使用 GMX 备用分析"
  PY=""
fi

# 旧版 GROMACS do_dssp 需外部 mkdssp；2024+ 内置 gmx dssp 可跳过
if [ -f "$WD/install_dssp.sh" ]; then
  _log "=== 检查/安装 DSSP（do_dssp 用）==="
  eval "$(bash "$WD/install_dssp.sh" 2>>"$LOG")" || true
  if [ -n "${DSSP:-}" ]; then
    _log "DSSP 可执行文件: $DSSP"
  else
    _log "提示：未找到外部 DSSP；若 GROMACS>=2024 将使用内置 gmx dssp"
  fi
fi

_log "=== [1/3] 轨迹后处理 ==="
if [ -f postprocess_traj.sh ]; then
  bash postprocess_traj.sh >>"$LOG" 2>&1 || _log "警告：轨迹后处理部分失败"
fi

_log "=== [2/3] 轨迹分析 ==="
ANALYSIS_OK=0
if [ -n "$PY" ] && [ -f traj_analyze.py ]; then
  if $PY "$WD/traj_analyze.py" --workdir "$WD" --out "$WD/analysis_summary.txt" >>"$LOG" 2>&1; then
    ANALYSIS_OK=1
  fi
fi
if [ "$ANALYSIS_OK" -eq 0 ] && [ -f gmx_only_analyze.sh ]; then
  _log "使用 GMX 备用分析…"
  bash gmx_only_analyze.sh >>"$LOG" 2>&1 || true
  if [ ! -s analysis_summary.txt ]; then
    echo "=== WebMD 轨迹分析（GMX 模式）===" > analysis_summary.txt
    echo "提示：已生成 CSV；若需完整 Python 分析请检查 AutoDL Python 环境。" >> analysis_summary.txt
  fi
fi

_log "=== [3/3] 打包交付物 ==="
if [ -f pack_deliverables.sh ]; then
  bash pack_deliverables.sh >>"$LOG" 2>&1 || _log "警告：打包失败"
fi

_log "=== 完成 ==="
