---
name: wire-label-reader
description: Read BOM3 wire-harness label photos offline with zxing-cpp QR/barcode plus RapidOCR, extracting part_no, LOT, M-xx$nn machine, terminals and crimp heights. Use for harness label recognition tasks; do not use for generic OCR unrelated to BOM3 labels.
---

# 线束标签图片离线识别（BOM3）

识别车间线束标签照片中的二维码/条形码与文字，并提取结构化字段。全程本地运算，内网可用。

## 现成模块（不要重写）

`utils/image_code_reader.py` 已实现完整能力，优先直接调用：

- `read_label(source)` — 一键编排，返回 `{codes, text, fields, scan_detail, errors}`，码识别与 OCR 互不阻断
- `detect_codes(source, formats=None)` — 仅识别码，`formats` 如 `['QRCode','Code128','DataMatrix']`
- `recognize_text(source, merge_rotations=True)` — OCR；标签拍摄角度不确定时必须合并四方向
- `extract_label_fields(codes, ocr_result)` — 字段提取
- `to_scan_detail(fields)` — 映射为与 `WireScanDetail.to_dict()` 完全一致的键（circuit/part_no/jx_eo/wire/color/sq/length_cut2/tml1/tml2/rel1/rel2/seal1/seal2/strip1/strip2/remark1/frozen），另附 lot_no/machine_no/cut_no/drawing_mgmt_no/qty/color_name。注意：**strip1/strip2 放标签实测 CCH 压高（1.05-1.15），不是 sub_list 的 4.1/4.3 行号**——sub_list.Strip1/Strip2 存的是标签行号锚点，scan_entry 页面"压高"列直接渲染 strip 故显示行号，真实压高数据库没有、只在标签上
- `source` 支持中文路径、bytes（Flask `request.files[...].read()`）、文件对象、PIL.Image、numpy
- 自测命令：`python -m utils.image_code_reader template/aa.jpg`（用服务同款解释器跑；2026-10-19 起服务跑在 Python 3.12，识别依赖已装齐 3.12，旧 3.13 商店版环境曾被系统更新清空）
- **环境一致性教训**：服务换解释器启动（如 3.13→3.12）而新环境缺 zxing-cpp/rapidocr/opencv/Pillow 时，read_label 走 `_require` 优雅降级，页面表现为所有字段「未识别」且无报错——排查第一步先 `python -c "import zxingcpp, rapidocr_onnxruntime, cv2, PIL"` 确认依赖，再查照片质量

修改字段提取逻辑时，正则全部集中在该模块"四、线束标签业务字段提取"一节，按既有函数粒度小改并跑 aa.jpg 断言验证。

## 标签字段语义（车间确认，勿臆改）

| 字段 | 含义 | 示例 |
|---|---|---|
| part_no | 品番（形如 3位数字-字母-4位） | 916D1-L2250 |
| lot_no | LOT（字母数字混排 8 位） | Q827A10Q |
| circuit_no | 回路号 | ZZ222 |
| drawing_mgmt_no | SET: 后的图纸管理编号 | DLPE09_ZZ222_1 |
| cut_no | CUT 编号 BN 开头 | BN0000107961 |
| machine_no / machine_seq | 号机完整编码 / $后编号 | M-15$27 / 27 |
| terminal1 + crimp_height1 | 行 4.1：端子1（KC）+ 其压高 | TKC12-06422 / 1.05-1.15 |
| terminal2 + crimp_height2 | 行 4.3：端子2（KT）+ 其压高 | TKT12-C6622 / 0.95-1.05 |
| wire_material / spec / length / color | 线质 / 规格(线径) / 长度 / 颜色 | AVSS / 0.50 / 990 / B(黑) |

关键配对规则：端子与压高必须按同一物理行的 **4.1 / 4.3 锚点**配对，禁止把两条 CCH 按猜测顺序分配（会串位）。锚点缺失时才兜底：KC→端子1、KT→端子2，剩余压高顺序补位。号机入库必须保留完整格式 `M-xx$编号`（系统约定，见项目记忆 wire_scan_record.machine_no）。

## 技术选型（经实测验证，勿回退）

- 码识别用 **zxing-cpp**：原图直扫即可同时出 QR/Code128/DataMatrix 等，自动旋转反色。不要用 pyzbar（本机 libzbar DLL 依赖加载失败）；不要只依赖 OpenCV QRCodeDetector（模糊/透视下召回低，需放大+CLAHE 才能勉强识别单个）。
- OCR 用 **rapidocr-onnxruntime**：ONNX 模型随 pip 包内置，无需 Tesseract、无需联网下载。
- 标签为竖拍时文字在原图是横排的另一方向：先 OCR 四方向，选横排行占比最高的方向做行聚类配对；字段提取用四方向合并去重结果提升召回（本例单方向 31 行、合并 40 行）。
- zxing-cpp 1.x/2.x 的 `formats` 参数不同：2.x 传枚举按位或标志、1.x 传 set；`formats` 为 None 时省略该参数（C++ 绑定不接受 None）。模块内 `_read_barcodes` 已兼容，勿改坏。
- 读图用 `np.fromfile + cv2.imdecode`，不要用 `cv2.imread`（中文路径返回 None）。
- 长度提取要排除"066"这类 OCR 碎片：无前置零优先、置信度次之。
- 不要把 DataMatrix 当 QR 处理；码识别失败先确认码制再谈预处理，优先原图/多尺度直扫而非盲目二值化。

## 内网部署

依赖（requirements.txt 已列入可选段）：zxing-cpp、rapidocr-onnxruntime、opencv-python、Pillow、numpy。
有网机 `py -m pip download <包> -d ./pkgs`，内网机 `py -m pip install --no-index --find-links=./pkgs <包>`。识别全程无网络请求。

## 验证基线

`template/aa.jpg` 应稳定识别出：3 个码（2×QR + 1×Code128，内容 `Q827A10Q01ZZ222`、`Q827A10QM-15$27`）和 19 个字段全部正确。改动模块后必须重跑此基线；PowerShell 内联命令会对 `$27` 插值，断言脚本请用临时 .py 文件执行。

## 已接入录入流程（2026-10-17）

- 接口：`POST /wire/scan_entry/label_photo`（multipart 字段 `photo`，≤15MB，权限 `can_wire_scan_entry_scan`），返回 `{scan_detail, codes, applied, overridden, crimp_source, history_from, part_no, jx_eo}`。
- 压高知识库 `wire_label_crimp`：唯一键 **(drawing_mgmt_no 图纸管理编号, tml1, tml2)**，lot_no/circuit_no 仅为「最近拍照来源」。规则：本次识别到压高→upsert（单侧缺失用同键历史补全 partial）；本次识别不到压高→**不新增**，自动调取同图纸同端子历史（reused，刷新最近来源，history_from 透明回传）；无历史→none 不写库。图纸未识别时退化按 LOT+回路+端子对定位。
- 快照应用：`_wire_store_snapshot` 固化时一次 OR+IN 预取候选（`_wire_load_crimp_candidates`），按端子对宽松匹配（前导零不敏感，`_wire_pick_crimp`：来源行优先→同图纸 SetCode 行→单候选兜底），bulk insert 即为真实压高；拍照当时快照已存在走 `_wire_apply_label_crimp`。重复扫码重建快照不回退为 4.1/4.3。注意标签 drawing（SET: 后，如 DLPE09_ZZ222_1）与 sub_list.SetCode（如 DL3FL_ZZ222_2）字符串不同则不自动命中，属安全降级。
- 前端：wire_scan_entry.html「拍照识别」按钮→结果弹窗按 crimp_source 区分展示（绿=本次识别/单侧补全、蓝=调取历史并标来源 LOT、黄=未添加）；「等同扫码带入」按钮用码文本直接走 handleCircuitScan/handleMachineScan；applyCrimpToLocal 同步本地明细压高。
- 服务端 Python 3.13 进程内可直接 import 识别模块（OCR 引擎已是惰性单例 `_get_ocr_engine`），无需子进程。
