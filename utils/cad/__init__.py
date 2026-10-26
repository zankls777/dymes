"""CAD 图纸处理模块：RW 差异回路标记。

对外主要接口:
  mark_file(dxf_path, xlsx_path, out_path)  按 Excel 差异表标记 DXF
  mark_circuits(dxf_path, add_set, del_set, out_path)  直接按回路集合标记
"""
from .marker import mark, mark_circuits, read_diff

__all__ = ["mark", "mark_circuits", "read_diff"]
