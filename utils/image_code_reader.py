#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线图片码识别 / OCR 工具模块（全程本地运算，不发起任何网络请求）

能力：
    1. 码识别 detect_codes()：QR / DataMatrix / Code128 / Code39 / EAN / PDF417 等
       底层使用 zxing-cpp（pip 包自带 C++ 运行时，Windows 免装额外 DLL）
    2. 文字识别 recognize_text()：中英混排 OCR
       底层使用 rapidocr-onnxruntime（ONNX 模型随 pip 包内置）
       支持四方向自动选向 / 四方向结果合并（标签拍摄角度不确定时提升召回）
    3. 线束标签字段提取 extract_label_fields()，字段语义（与车间标签一一对应）：
         part_no           品番           如 916D1-L2250
         lot_no            LOT            如 Q827A10Q
         eo_version        EO 版本        如 a4
         circuit_no        回路号         如 ZZ222
         drawing_mgmt_no   图纸管理编号   SET: 后的值，如 DLPE09_ZZ222_1
         cut_no            CUT 编号       如 BN0000107961
         qty               数量
         machine_no        号机完整编码   如 M-15$27（系统入库要求保留 $编号）
         machine_seq       号机编号       $ 后两位，如 27
         terminal1         端子1          如 TKC12-06422（行 4.1）
         crimp_height1     端子1压高      如 1.05-1.15
         terminal2         端子2          如 TKT12-C6622（行 4.3）
         crimp_height2     端子2压高      如 0.95-1.05
         wire_material     线质           如 AVSS
         spec              规格(线径)     如 0.50
         length            长度           如 990
         color_code        颜色代码       如 B
         color_name        颜色名称       如 黑
    4. 一键编排 read_label()：码 + 四方向 OCR 合并 + 结构化字段

依赖安装（内网离线方式）：
    # 有网机器：py -m pip download zxing-cpp rapidocr-onnxruntime opencv-python Pillow -d ./pkgs
    # 内网机器：py -m pip install --no-index --find-links=./pkgs zxing-cpp rapidocr-onnxruntime

命令行自测：
    py -m utils.image_code_reader template/aa.jpg
"""

import os
import re
import json
import argparse
from threading import Lock

import numpy as np

# ---------------------------------------------------------------------------
# 可选依赖说明：cv2/PIL 为图像处理基础库，zxing/rapidocr 按需延迟导入，
# 未安装时抛出带安装指引的明确错误，而不是在 import 本模块时直接失败。
# ---------------------------------------------------------------------------
_INSTALL_HINT = ("离线安装：pip install zxing-cpp rapidocr-onnxruntime Pillow "
                 "opencv-python-headless（Linux/NAS 无桌面环境必须用 headless 版）")


def _require(module_name, pip_name=None):
    """延迟导入可选依赖，缺失时给出可执行的安装提示"""
    try:
        return __import__(module_name)
    except ImportError as exc:
        pkg = pip_name or module_name
        # 带上底层错误：可区分“没装包”与“装了但加载失败”（如 Linux 缺 libGL.so.1）
        raise RuntimeError(
            "缺少可选依赖 '%s'，请先安装（%s）；底层错误：%s"
            % (pkg, _INSTALL_HINT, exc)
        ) from exc


# ===========================================================================
# 一、图像加载：兼容 路径(含中文)/字节流/文件对象/PIL/numpy 多种输入
# ===========================================================================

def load_image(source):
    """
    将多种来源统一加载为 OpenCV BGR 图像(numpy.ndarray)
    :param source: 图片路径(str/PathLike)、bytes、file-like、PIL.Image 或 numpy 数组
    """
    cv2 = _require("cv2", "opencv-python")

    # 已经是 numpy 数组（假定 BGR 或灰度），直接返回
    if isinstance(source, np.ndarray):
        return source

    # PIL.Image：RGB 转 BGR
    try:
        from PIL import Image
        if isinstance(source, Image.Image):
            rgb = np.array(source.convert("RGB"))
            return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    except ImportError:
        pass

    # 其余输入统一转为字节流
    if isinstance(source, (bytes, bytearray)):
        buf = np.frombuffer(bytes(source), dtype=np.uint8)
    elif isinstance(source, str) or hasattr(source, "__fspath__"):
        # np.fromfile 读取可绕过 cv2.imread 不支持中文路径的问题
        path = os.fspath(source)
        if not os.path.isfile(path):
            raise FileNotFoundError("图片不存在：%s" % path)
        buf = np.fromfile(path, dtype=np.uint8)
    elif hasattr(source, "read"):
        buf = np.frombuffer(source.read(), dtype=np.uint8)
    else:
        raise TypeError("不支持的图片输入类型：%s" % type(source).__name__)

    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("图片解码失败（文件损坏或格式不支持）")
    return img


# ===========================================================================
# 二、码识别：zxing-cpp（自动支持旋转 / 反色，覆盖一维码与二维码）
# ===========================================================================

def _resolve_formats(zxing, formats):
    """把字符串格式名(如 'QRCode')解析为 zxing 的 BarcodeFormat 枚举列表"""
    if not formats:
        return []
    result = []
    for name in formats:
        fmt = getattr(zxing.BarcodeFormat, name, None)
        if fmt is None:
            raise ValueError("不支持的码制名称：%s" % name)
        result.append(fmt)
    return result


def _read_barcodes(zxing, img, fmt_list, try_rotate, try_invert):
    """调用 zxing 读码，兼容 1.x(传枚举集合) 与 2.x(传位或标志) 两种接口"""
    if not fmt_list:
        return zxing.read_barcodes(img, try_rotate=try_rotate,
                                   try_invert=try_invert)
    # 新版 zxing-cpp 2.x：formats 为按位或组合的 BarcodeFormats 标志
    combined = fmt_list[0]
    for fmt in fmt_list[1:]:
        combined |= fmt
    try:
        return zxing.read_barcodes(img, formats=combined,
                                   try_rotate=try_rotate, try_invert=try_invert)
    except TypeError:
        # 旧版 zxing-cpp 1.x：formats 接受枚举 set/frozenset
        return zxing.read_barcodes(img, formats=set(fmt_list),
                                   try_rotate=try_rotate, try_invert=try_invert)


def _position_to_dict(position):
    """zxing Position(四个角点) 转成可 JSON 序列化的 polygon + 外接矩形"""
    pts = [(int(p.x), int(p.y)) for p in (
        position.top_left, position.top_right,
        position.bottom_right, position.bottom_left,
    )]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return {
        "polygon": pts,
        "rect": {"x": min(xs), "y": min(ys),
                 "w": max(xs) - min(xs), "h": max(ys) - min(ys)},
    }


def detect_codes(source, formats=None, try_rotate=True, try_invert=True):
    """
    识别图片中的二维码 / 条形码
    :param formats: 仅识别指定码制，如 ['QRCode', 'Code128', 'DataMatrix']；None 为全部
    :param try_rotate: 是否自动尝试旋转 90/180/270 度（竖拍标签建议开启）
    :param try_invert: 是否自动尝试反色
    :return: [{'type': 码制, 'text': 文本, 'bytes': 原始字节(可空), 'position': 位置}]
    """
    zxing = _require("zxingcpp", "zxing-cpp")
    img = load_image(source)
    fmt_list = _resolve_formats(zxing, formats)
    hits = _read_barcodes(zxing, img, fmt_list, try_rotate, try_invert)

    results = []
    for r in hits:
        item = {
            "type": r.format.name,
            "text": r.text,
            "bytes": None,
            "position": _position_to_dict(r.position),
        }
        try:
            # 部分码制内容非 UTF-8 文本，保留原始字节供调用方自行解码
            item["bytes"] = bytes(r.bytes).hex()
        except Exception:
            pass
        results.append(item)
    return results


# ===========================================================================
# 三、OCR 文字识别：RapidOCR 引擎单例 + 多方向识别/合并
# ===========================================================================

_ocr_engine = None        # 进程内复用，避免重复加载模型
_ocr_lock = Lock()
# 四个待选方向：(标签名, OpenCV 旋转码或 None)
_ROTATIONS = (
    ("0", None),
    ("90_cw", "ROTATE_90_CLOCKWISE"),
    ("180", "ROTATE_180"),
    ("270_cw", "ROTATE_90_COUNTERCLOCKWISE"),
)


def _get_ocr_engine():
    """懒加载 OCR 引擎（首次调用约 1 秒），全进程共享一个实例"""
    global _ocr_engine
    if _ocr_engine is None:
        with _ocr_lock:
            if _ocr_engine is None:
                rapidocr = _require(
                    "rapidocr_onnxruntime", "rapidocr-onnxruntime")
                _ocr_engine = rapidocr.RapidOCR()
    return _ocr_engine


def _run_ocr(engine, img):
    """对单张图执行 OCR，返回行列表 [{text, score, box}]"""
    result, _ = engine(img)
    lines = []
    if result:
        for box, text, score in result:
            lines.append({
                "text": text,
                "score": round(float(score), 4),
                "box": [[round(float(p[0]), 1), round(float(p[1]), 1)]
                        for p in box],
            })
    return lines


def _rotate(img, rot_name):
    """按 _ROTATIONS 中的旋转码旋转图像"""
    if rot_name is None:
        return img
    cv2 = _require("cv2", "opencv-python")
    return cv2.rotate(img, getattr(cv2, rot_name))


def _ocr_all_orientations(img):
    """对四个拍摄方向各做一次 OCR，返回 [(方向名, 行列表)]（按固定顺序）"""
    engine = _get_ocr_engine()
    return [(name, _run_ocr(engine, _rotate(img, rot)))
            for name, rot in _ROTATIONS]


def _ocr_score(lines):
    """OCR 质量打分：置信度均值为主，行数过少时降权（用于方向选优）"""
    if not lines:
        return 0.0
    avg = sum(l["score"] for l in lines) / len(lines)
    return avg * (1.0 - 1.0 / (len(lines) + 1))


def _horizontal_ratio(lines):
    """横排文字行占比：框宽明显大于框高的行越多，说明该方向越接近横排正向"""
    if not lines:
        return 0.0
    horiz = 0
    for l in lines:
        box = l["box"]
        w = max(p[0] for p in box) - min(p[0] for p in box)
        h = max(p[1] for p in box) - min(p[1] for p in box)
        if w > h * 1.5:
            horiz += 1
    return horiz / len(lines)


def _line_key(text):
    """OCR 行归一化键：去空白/星标并大写，用于跨方向去重"""
    return re.sub(r"[\s★*＊]+", "", text).upper()


def _merge_lines(oriented_lines):
    """
    合并多方向 OCR 行：归一化后去重，保留首次出现且置信度更高的版本。
    不同方向漏识别的内容可互补，显著提高标签字段召回率。
    """
    merged = {}
    for _name, lines in oriented_lines:
        for line in lines:
            key = _line_key(line["text"])
            if not key:
                continue
            old = merged.get(key)
            if old is None or line["score"] > old["score"]:
                merged[key] = dict(line)
    return list(merged.values())


def _pick_best_orientation(oriented_lines):
    """选择最佳单方向：优先横排占比高，其次 OCR 质量分"""
    best_name, best_lines, best_key = "0", [], (-1, -1)
    for name, lines in oriented_lines:
        key = (_horizontal_ratio(lines), _ocr_score(lines))
        if key > best_key:
            best_name, best_lines, best_key = name, lines, key
    return best_name, best_lines


def recognize_text(source, auto_rotate=True, merge_rotations=False,
                   min_score=0.6, min_lines=3):
    """
    识别图片文字
    :param auto_rotate: True 时允许使用旋转方向（关闭则只识别原图）
    :param merge_rotations: True 时合并四个方向的 OCR 结果去重返回，
                            适合标签字段提取（以多一次推理换召回率）
    :param min_score/min_lines: 原图方向达到该质量即不再尝试旋转，节省耗时
    :return: {'orientation': 最佳方向, 'lines': 行列表,
              'oriented': [(方向, 行列表), ...](合并模式下供行锚点配对)}
    """
    img = load_image(source)

    # 先识别原图，非自动旋转或质量达标时直接返回（避免无谓推理）
    first = _run_ocr(_get_ocr_engine(), img)
    if not auto_rotate:
        return {"orientation": "0", "lines": first, "oriented": [("0", first)]}
    if not merge_rotations and (len(first) >= min_lines
                                and _ocr_score(first) >= min_score):
        return {"orientation": "0", "lines": first, "oriented": [("0", first)]}

    # 补齐其余三个方向（首方向复用已有结果，不重复推理）
    oriented = [("0", first)]
    for name, rot in _ROTATIONS[1:]:
        oriented.append((name, _run_ocr(_get_ocr_engine(), _rotate(img, rot))))

    best_name, best_lines = _pick_best_orientation(oriented)
    if merge_rotations:
        return {"orientation": best_name,
                "lines": _merge_lines(oriented),
                "oriented": oriented}
    return {"orientation": best_name, "lines": best_lines, "oriented": oriented}


# ===========================================================================
# 四、线束标签业务字段提取
# ===========================================================================

# ---- 基础字段正则（集中管理，便于按标签样式调整）-------------------------
RE_MACHINE = re.compile(r"M\s*[-－]\s*(\d{1,2})\s*[\$＄(（]\s*(\d{1,2})", re.I)
RE_CUT = re.compile(r"(BN\d{6,})")                           # CUT 编号
RE_QTY = re.compile(r"QTY[:：]?\s*(\d{1,6})", re.I)          # 数量
RE_PART = re.compile(r"\b(\d{3}[A-Z]\d-[A-Z]\d{4})\b")       # 品番 916D1-L2250
RE_LOT = re.compile(r"\b([A-Z]\d{2,3}[A-Z]\d{2}[A-Z])\b")    # LOT Q827A10Q
RE_LOT_HEAD = re.compile(r"^([A-Z]\d{2,3}[A-Z]\d{2}[A-Z])")  # 码文本开头的 LOT
RE_CIRCUIT = re.compile(r"\b(ZZ\d{3})\b")                    # 回路号 ZZ222
RE_SEQ = re.compile(r"\b(\d{3}-\d{3})\b")                    # 序号 001-033
RE_EO = re.compile(r"(?<![A-Za-z0-9])([a-z]\d)(?![A-Za-z0-9])")  # EO 版本 a4
RE_SET = re.compile(
    r"SET[:：]?\s*([A-Z0-9]{2,}(?:_[A-Z0-9]+)+)", re.I)       # 图纸管理编号
RE_COLOR = re.compile(
    r"([A-Z])\s*[（(]\s*([黑白红蓝绿黄灰棕橙紫粉])\s*[)）]")  # B(黑)
# 端子号：T KC 12-06422 / T KT12-C6622（OCR 常把字母切开，需容错空格）
RE_TERMINAL = re.compile(
    r"T\s*K\s*([CT])\s*(\d{2})\s*[-－]\s*([0-9A-Z]{4,5})", re.I)
# 压高 CCH：1.05~1.15 / 0.95-1.05 等（~ ～ - — 均可能出现）
RE_CCH = re.compile(
    r"CCH\s*([0-9]+(?:\.[0-9]+)?)\s*[~～\-—]\s*([0-9]+(?:\.[0-9]+)?)", re.I)
# 需要从规格/长度候选中屏蔽的区间型文本（CCH/ICH 数值范围）
RE_RANGE_ANY = re.compile(
    r"[0-9]+(?:\.[0-9]+)?\s*[~～\-—]\s*[0-9]+(?:\.[0-9]+)?(?:/[A-Z0-9]+)?", re.I)
RE_ROW_ANCHOR = re.compile(r"(?:^|[^\d])4\s*[\.．]\s*([13])(?:[^\d]|$)")
# 常见汽车线束线质（按厂内实际可继续补充）
WIRE_MATERIALS = ("AESSX", "AVSS", "AVS", "AEX", "AVX", "CIVUS", "FLRY", "AV")


def _first_match(pattern, texts, group=0):
    """在多个文本中按顺序找第一个正则命中"""
    for text in texts:
        m = pattern.search(text)
        if m:
            return m.group(group)
    return None


def _norm_terminal(text):
    """把 'T KC 12-06422' 归一化为 'TKC12-06422'，并返回(端子号, 行号)"""
    m = RE_TERMINAL.search(text)
    if not m:
        return None
    token = "TK%s%s-%s" % (
        m.group(1).upper(), m.group(2), m.group(3).upper())
    # KC 开头为端子1（行 4.1），KT 开头为端子2（行 4.3）
    row = "1" if m.group(1).upper() == "C" else "2"
    return token, row


def _norm_drawing_no(raw):
    """修正图纸管理编号的典型 OCR 混淆：字母后接数字位上的 O 实为 0
    如 'DLPEO9_ZZ222_1' -> 'DLPE09_ZZ222_1'"""
    if not raw:
        return None
    return re.sub(r"(?<=[A-Z])O(?=\d)", "0", raw.upper())


def _norm_machine(raw):
    """从 M-15(27) / M-15$27 文本提取 (完整号机 M-15$27, 编号 27)"""
    if not raw:
        return None, None
    m = RE_MACHINE.search(raw)
    if not m:
        return None, None
    return "M-%s$%s" % (m.group(1).zfill(2), m.group(2)), m.group(2)


def _cluster_rows(lines, y_tol=28):
    """
    把 OCR 行按 y 坐标聚成物理行（同一标签行的各列内容 y 中心接近）
    :return: 每行拼接后的文本列表，自上而下有序
    """
    items = []
    for line in lines:
        ys = [p[1] for p in line["box"]]
        xs = [p[0] for p in line["box"]]
        items.append((sum(ys) / len(ys), min(xs), line["text"]))
    items.sort(key=lambda it: (it[0], it[1]))

    rows, cur_y, cur_texts = [], None, []
    for y, _x, text in items:
        if cur_y is None or abs(y - cur_y) <= y_tol:
            cur_texts.append(text)
            cur_y = y if cur_y is None else (cur_y + y) / 2
        else:
            rows.append(" ".join(cur_texts))
            cur_texts, cur_y = [text], y
    if cur_texts:
        rows.append(" ".join(cur_texts))
    return rows


def _build_terminal_rows(ocr_result):
    """
    配对「端子 <-> 压高」：
    1) 在最接近横排正向的方向，按物理行找 4.1/4.3 锚点，同行配对（最可靠）
    2) 兜底：KC 归端子1、KT 归端子2；剩余压高按出现顺序补空位
    :return: {'1': {'terminal': x, 'crimp_height': y}, '2': {...}}
    """
    paired = {"1": {}, "2": {}}

    # 按横排占比从高到低尝试各方向的行锚点配对
    oriented = ocr_result.get("oriented") or [
        (ocr_result.get("orientation", "0"), ocr_result.get("lines", []))]
    for _name, lines in sorted(
            oriented, key=lambda nl: _horizontal_ratio(nl[1]), reverse=True):
        for row_text in _cluster_rows(lines):
            m_anchor = RE_ROW_ANCHOR.search(" " + row_text + " ")
            if not m_anchor:
                continue
            # 锚点 4.1 -> 端子1，4.3 -> 端子2
            row_no = "1" if m_anchor.group(1) == "1" else "2"
            if paired[row_no]:
                continue
            m_term = RE_TERMINAL.search(row_text)
            m_cch = RE_CCH.search(row_text)
            if m_term:
                paired[row_no]["terminal"] = "TK%s%s-%s" % (
                    m_term.group(1).upper(), m_term.group(2),
                    m_term.group(3).upper())
            if m_cch:
                paired[row_no]["crimp_height"] = "%s-%s" % (
                    m_cch.group(1), m_cch.group(2))

    # 兜底：用合并行按 KC/KT 归属端子，未归属的压高顺序补空位
    texts = [l["text"] for l in ocr_result.get("lines", [])]
    for text in texts:
        parsed = _norm_terminal(text)
        if parsed and "terminal" not in paired[parsed[1]]:
            paired[parsed[1]]["terminal"] = parsed[0]
    free_cch = [("%s-%s" % (m.group(1), m.group(2)))
                for text in texts for m in [RE_CCH.search(text)] if m]
    for value in free_cch:
        for row_no in ("1", "2"):
            if "crimp_height" not in paired[row_no]:
                paired[row_no]["crimp_height"] = value
                break
    return paired


def _mask_noise(text):
    """屏蔽已知编码片段，避免干扰规格/长度这类弱特征数字的提取"""
    for pattern in (RE_CUT, RE_SEQ, RE_RANGE_ANY, RE_TERMINAL, RE_SET,
                    RE_QTY, RE_PART, RE_LOT, RE_ROW_ANCHOR):
        text = pattern.sub(" ", text)
    return text


def _extract_spec_and_length(lines):
    """
    提取规格(线径)与长度：先屏蔽编码/区间/锚点等噪声，再逐行精确匹配。
    长度若存在多个三位候选，优先无前置零者（线长一般不以 0 开头）。
    """
    spec, length = None, None
    length_candidates = []  # (OCR置信度, 值, 是否有前置零)
    for line in lines:
        clean = _mask_noise(line["text"])
        if spec is None:
            m = re.search(r"(?<![\d.])(\d\.\d{2})(?!\d)", clean)
            if m:
                spec = m.group(1)
        # 整行去空格后恰好是三位数字的，作为长度候选
        stripped = clean.strip()
        m = re.fullmatch(r"0*(\d{2,4})", stripped)
        if m:
            val = m.group(1)
            length_candidates.append(
                (line["score"], val, stripped.startswith("0")))
    if length_candidates:
        # 无前置零优先(c[2]=False 排前)；其次置信度高；其次更接近常见线长（3 位）
        length_candidates.sort(key=lambda c: (c[2], -c[0],
                                              abs(len(c[1]) - 3)))
        length = length_candidates[0][1]
    return spec, length


def extract_label_fields(codes, ocr_result):
    """
    从码识别结果 + OCR 结果中提取线束标签业务字段（码内容优先于 OCR）
    :param codes: detect_codes() 的返回
    :param ocr_result: recognize_text() 的返回（dict，含 lines / oriented）
    :return: 结构化字段 dict（字段语义见模块头注释）
    """
    if not isinstance(ocr_result, dict):
        ocr_result = {"lines": ocr_result, "oriented": []}
    lines = ocr_result.get("lines", [])
    code_texts = [c["text"] for c in codes if c.get("text")]
    ocr_texts = [l["text"] for l in lines]
    # 码在前：LOT/回路号等以码识别（100% 准确）优先命中
    all_sources = code_texts + ocr_texts

    # 号机：码 Q827A10QM-15$27 携带完整号机编码
    machine_raw = _first_match(RE_MACHINE, all_sources)
    machine_no, machine_seq = _norm_machine(machine_raw)

    # LOT：优先 OCR 独立文本，其次码文本开头
    lot_no = _first_match(RE_LOT, ocr_texts, group=1)
    if not lot_no:
        lot_no = _first_match(RE_LOT_HEAD, code_texts, group=1)

    # 线质：在已知线质表中查找
    joined_upper = " ".join(ocr_texts).upper()
    wire_material = next((w for w in WIRE_MATERIALS if w in joined_upper), None)

    color_code = color_name = None
    color_raw = _first_match(RE_COLOR, ocr_texts)
    if color_raw is not None:
        m_color = RE_COLOR.search(color_raw)
        color_code, color_name = m_color.group(1), m_color.group(2)

    spec, length = _extract_spec_and_length(lines)
    term_rows = _build_terminal_rows(ocr_result)

    return {
        "part_no": _first_match(RE_PART, ocr_texts, group=1),
        "lot_no": lot_no,
        "eo_version": _first_match(RE_EO, ocr_texts, group=1),
        "circuit_no": _first_match(RE_CIRCUIT, all_sources, group=1),
        "drawing_mgmt_no": _norm_drawing_no(
            _first_match(RE_SET, ocr_texts, group=1)),
        "cut_no": _first_match(RE_CUT, all_sources, group=1),
        "qty": _first_match(RE_QTY, ocr_texts, group=1),
        "machine_no": machine_no,
        "machine_seq": machine_seq,
        "terminal1": term_rows["1"].get("terminal"),
        "crimp_height1": term_rows["1"].get("crimp_height"),
        "terminal2": term_rows["2"].get("terminal"),
        "crimp_height2": term_rows["2"].get("crimp_height"),
        "wire_material": wire_material,
        "spec": spec,
        "length": length,
        "color_code": color_code,
        "color_name": color_name,
        "seq_range": _first_match(RE_SEQ, ocr_texts, group=1),
        "code_texts": code_texts,
    }


# ===========================================================================
# 五、字段结构对齐：与 wire_scan_detail.to_dict() / scan_entry 拉取结果一致
# ===========================================================================

def to_scan_detail(fields, frozen=False):
    """
    把标签识别字段映射为与扫码拉取明细完全一致的结构
    （键名对齐 models/wire_scan_detail.py 的 WireScanDetail.to_dict()，
    前端 rowItem 可直接渲染，亦可并入 sublistMap / 快照入库）。

    关键差异：sub_list.Strip1/Strip2 列存的是标签行号（4.1/4.3），
    并非压高；此处 strip1/strip2 放从标签扫描到的真实 CCH 压高值。
    标签上没有的字段（继电器/防水栓/配线区分）给空字符串，保持键齐全。
    """
    return {
        # —— 与 WireScanDetail.to_dict() 完全一致的键 ——
        'circuit': fields.get('circuit_no') or '',
        'part_no': fields.get('part_no') or '',
        'jx_eo': fields.get('eo_version') or '',
        'wire': fields.get('wire_material') or '',
        'color': fields.get('color_code') or '',
        'sq': fields.get('spec') or '',
        'length_cut2': fields.get('length') or '',
        'tml1': fields.get('terminal1') or '',
        'tml2': fields.get('terminal2') or '',
        'rel1': '', 'rel2': '',
        'seal1': '', 'seal2': '',
        # 压高=标签 CCH 实测规格（端子1/端子2 两头），不是 sub_list 的 4.1/4.3 行号
        'strip1': fields.get('crimp_height1') or '',
        'strip2': fields.get('crimp_height2') or '',
        'remark1': '',
        'frozen': bool(frozen),
        # —— 标签额外携带、to_dict 没有但录入流程可直接利用的键 ——
        'lot_no': fields.get('lot_no') or '',
        'machine_no': fields.get('machine_no') or '',
        'cut_no': fields.get('cut_no') or '',
        'drawing_mgmt_no': fields.get('drawing_mgmt_no') or '',
        'qty': fields.get('qty') or '',
        'color_name': fields.get('color_name') or '',
    }


# ===========================================================================
# 六、一键编排：码识别 + 四方向 OCR 合并 + 字段提取（单能力失败互不影响）
# ===========================================================================

def read_label(source):
    """
    识别一张线束标签图片，返回结构化结果
    :return: {'codes': 码列表, 'text': OCR结果, 'fields': 业务字段,
              'errors': 缺失依赖等非致命错误}
    """
    result = {"codes": [], "text": None, "fields": None, "errors": []}

    # 码识别失败（如未装 zxing-cpp）记录错误但不阻断 OCR
    try:
        result["codes"] = detect_codes(source)
    except Exception as e:
        result["errors"].append({"stage": "codes", "message": str(e)})

    try:
        # 标签拍摄角度不确定：合并四方向 OCR，保证端子/压高等字段召回
        result["text"] = recognize_text(source, merge_rotations=True)
    except Exception as e:
        result["errors"].append({"stage": "ocr", "message": str(e)})

    result["fields"] = extract_label_fields(
        result["codes"], result["text"] or {"lines": []})

    # 与 wire/scan_entry 扫码拉取结果一致的结构（键对齐 WireScanDetail.to_dict()）
    # strip1/strip2 放标签实测 CCH 压高，而非 sub_list 的 4.1/4.3 行号
    result["scan_detail"] = to_scan_detail(result["fields"])
    return result


# ===========================================================================
# 六、命令行入口：py -m utils.image_code_reader <图片路径>
# ===========================================================================

def _main():
    parser = argparse.ArgumentParser(description="离线图片码识别 / OCR 工具")
    parser.add_argument("image", help="图片路径（支持中文路径）")
    parser.add_argument("--codes-only", action="store_true",
                        help="仅识别二维码/条形码，跳过 OCR")
    parser.add_argument("--no-merge", action="store_true",
                        help="OCR 只选最佳单方向，不合并四个方向结果")
    args = parser.parse_args()

    if args.codes_only:
        output = {"codes": detect_codes(args.image)}
    elif args.no_merge:
        output = {"codes": detect_codes(args.image),
                  "text": recognize_text(args.image)}
    else:
        output = read_label(args.image)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
