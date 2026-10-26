#!/usr/bin/env python3
"""在 DXF 图纸上标记 RW 差异回路号。

标记规则（同一张图同时体现增减，靠形状区分）：
  增加 -> 红色方框
  删除 -> 红色圆圈
统一加大边距 1.5，线宽 0.5mm，红色。

用法:
  python utils/cad/marker.py <dxf> <xlsx> <输出dxf>

依赖: ezdxf, openpyxl
"""
import re
import sys

import ezdxf
import openpyxl

# 回路号文字特征：字母开头 + 数字（可含 -数字 后缀）
CIRCUIT_RX = re.compile(r'^[A-Za-z]{1,4}\d+([A-Za-z]|-\d+)?$')

MARK_LAYER = "RW_MARK"
MARK_COLOR = 1          # 1 = 红
PAD = 1.5               # 边距
LINE_WEIGHT = 0.5       # 方框线宽 (mm)，用 const_width 实现
CIRCLE_WIDTH = 0.8      # 圆圈线宽 (mm)，加粗以突出删除标记
DIFF_SHEET = "SubList差异"

# 回路号文字所在图层：
#   - 部分图纸将回路号置于 Circuit 层；
#   - 另一部分图纸按 GRxxx 分组分层（Circuit 层为空），编号范围不固定，
#     实测同一张图可延伸到 GR001~GR011，故按前缀模式匹配而非写死枚举。
CIRCUIT_LAYER_NAMES = {"circuit"}
CIRCUIT_LAYER_RX = re.compile(r'^GR\d+$', re.IGNORECASE)


def is_circuit_layer(layer_name):
    """判断图层是否为承载回路号文字的图层（Circuit 层 或 GRxxx 分组层）。"""
    name = (layer_name or '').strip()
    return name.lower() in CIRCUIT_LAYER_NAMES or bool(CIRCUIT_LAYER_RX.match(name))


def read_diff(xlsx_path):
    """从 SubList差异 表读取 增加/删除 的 Circuit 值集合（去掉 -N 去重后缀）。"""
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    ws = wb[DIFF_SHEET]
    rows = list(ws.iter_rows(values_only=True))
    hdr = rows[0]
    ic, it = hdr.index("Circuit"), hdr.index("类型")
    add, dele = set(), set()
    for r in rows[1:]:
        if r[ic] is None:
            continue
        val = re.sub(r'-\d+$', '', str(r[ic]).strip())
        kind = r[it]
        if kind == "增加":
            add.add(val)
        elif kind == "删除":
            dele.add(val)
    wb.close()
    return add, dele


def ensure_layer(doc):
    if MARK_LAYER not in doc.layers:
        doc.layers.add(MARK_LAYER, color=MARK_COLOR)


def text_width(text, height):
    """粗略估算文字宽度（等宽近似）。"""
    return len(text) * height * 0.62


def _add_box(msp, x0, y0, x1, y1):
    msp.add_lwpolyline(
        [(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
        close=True,
        dxfattribs={"layer": MARK_LAYER, "color": MARK_COLOR,
                    "const_width": LINE_WEIGHT},
    )


def _add_circle(msp, cx, cy, r):
    """用闭合 LWPOLYLINE 绘制圆，以便通过 const_width 实现实际可见的加粗。

    CIRCLE 实体仅有 lineweight 属性，多数查看器默认不显示线宽，故改用多段线圆。
    """
    import math
    seg = 64  # 圆周分段数，兼顾平滑与文件大小
    pts = [(cx + r * math.cos(2 * math.pi * i / seg),
            cy + r * math.sin(2 * math.pi * i / seg)) for i in range(seg)]
    msp.add_lwpolyline(
        pts,
        close=True,
        dxfattribs={"layer": MARK_LAYER, "color": MARK_COLOR,
                    "const_width": CIRCLE_WIDTH},
    )


def mark(dxf_path, xlsx_path, out_path):
    """按 Excel 差异表标记 DXF。"""
    add, dele = read_diff(xlsx_path)
    return mark_circuits(dxf_path, add, dele, out_path)


def mark_circuits(dxf_path, add, dele, out_path):
    """直接按 增加/删除 回路集合标记 DXF。

    add/dele: 回路号字符串集合。
    返回统计 dict。
    """
    add = set(add or ())
    dele = set(dele or ())

    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    ensure_layer(doc)

    n_add = n_del = 0
    hit = set()
    for e in msp:
        # 仅处理承载回路号的图层上的单行文字实体
        if e.dxftype() != "TEXT" or not is_circuit_layer(e.dxf.layer):
            continue
        txt = e.dxf.text.strip()
        if txt not in add and txt not in dele:
            continue
        hit.add(txt)
        p = e.dxf.insert
        h = e.dxf.height
        w = text_width(txt, h)
        if txt in add:
            _add_box(msp, p.x - PAD, p.y - PAD, p.x + w + PAD, p.y + h + PAD)
            n_add += 1
        if txt in dele:
            # 圆半径覆盖文字对角线的一半 + 边距
            cx, cy = p.x + w / 2.0, p.y + h / 2.0
            r = ((w / 2.0) ** 2 + (h / 2.0) ** 2) ** 0.5 + PAD
            _add_circle(msp, cx, cy, r)
            n_del += 1

    doc.saveas(out_path)
    stat = {
        "add_circuits": len(add), "add_marks": n_add,
        "del_circuits": len(dele), "del_marks": n_del,
        "add_missing": sorted(add - hit),
        "del_missing": sorted(dele - hit),
        "output": out_path,
    }
    print("增加(红框): %d 个回路, %d 处" % (len(add), n_add))
    print("删除(红圈): %d 个回路, %d 处" % (len(dele), n_del))
    if stat["add_missing"]:
        print("  增加未命中 (%d): %s" % (len(stat["add_missing"]), stat["add_missing"]))
    if stat["del_missing"]:
        print("  删除未命中 (%d): %s" % (len(stat["del_missing"]), stat["del_missing"]))
    print("  输出: %s" % out_path)
    return stat


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    mark(sys.argv[1], sys.argv[2], sys.argv[3])
