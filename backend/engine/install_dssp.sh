#!/bin/bash
# ==================================================
# 功能说明：为 GROMACS do_dssp 安装/定位外部 DSSP 可执行文件（mkdssp/dssp）
# 使用方法：eval "$(bash install_dssp.sh)"  或  source <(bash install_dssp.sh)
# 说明：GROMACS 2024+ 内置 gmx dssp，无需本脚本；2021–2023 需 mkdssp
# 依赖环境：可选 apt-get、conda（AutoDL 常用）
# 生成时间：2026-07-13
# ==================================================

set -uo pipefail

_find_dssp() {
  local p=""
  for b in mkdssp dssp; do
    p="$(command -v "$b" 2>/dev/null || true)"
    if [ -n "$p" ] && [ -x "$p" ]; then
      echo "$p"
      return 0
    fi
  done
  for p in /usr/bin/mkdssp /usr/bin/dssp /usr/local/bin/mkdssp /usr/local/bin/dssp; do
    if [ -x "$p" ]; then
      echo "$p"
      return 0
    fi
  done
  return 1
}

_p="$( _find_dssp )" || true
if [ -n "${_p:-}" ]; then
  echo "export DSSP='$_p'"
  exit 0
fi

# Debian/Ubuntu：apt 包名 dssp，二进制多为 mkdssp
if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq >/dev/null 2>&1 || true
  apt-get install -y -qq dssp >/dev/null 2>&1 || true
  _p="$( _find_dssp )" || true
  if [ -n "${_p:-}" ]; then
    echo "export DSSP='$_p'"
    exit 0
  fi
fi

# Conda（AutoDL / miniconda3）
CONDA=""
for c in /root/miniconda3/bin/conda /opt/conda/bin/conda; do
  if [ -x "$c" ]; then CONDA="$c"; break; fi
done
if [ -n "$CONDA" ]; then
  "$CONDA" install -y -c conda-forge dssp >/dev/null 2>&1 || \
  "$CONDA" install -y -c salilab dssp >/dev/null 2>&1 || true
  _p="$( _find_dssp )" || true
  if [ -n "${_p:-}" ]; then
    echo "export DSSP='$_p'"
    exit 0
  fi
fi

# 未找到：旧版 do_dssp 将不可用；2024+ 仍可用内置 gmx dssp
echo "# DSSP 外部程序未安装（若 GROMACS>=2024 可忽略，使用内置 gmx dssp）" >&2
exit 0
