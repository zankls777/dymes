#!/bin/sh
# ============================================================
#  BOM3 一键打包并投递到香港目录（Linux / NAS）
#
#  用法:
#    ./pack_to_hk.sh                          使用默认/环境变量目标
#    ./pack_to_hk.sh /mnt/hk                  指定目标目录
#    BOM3_HK_DIR=/mnt/hk ./pack_to_hk.sh      用环境变量
# ============================================================
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)

if [ -n "$1" ]; then
    exec python3 "$SCRIPT_DIR/pack_to_hk.py" --dest "$1"
else
    exec python3 "$SCRIPT_DIR/pack_to_hk.py"
fi
