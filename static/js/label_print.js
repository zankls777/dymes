/* 标签打印：模版设计器 + 数据录入 + 批量打印
 * 单位约定：画布尺寸/坐标/宽高为毫米(mm)，字号为磅(pt)，屏幕换算 1mm = 96/25.4 px。
 */
(function () {
    'use strict';

    var MM_PX = 96 / 25.4;          // 1mm 对应的 CSS 像素
    var PT_PX = 96 / 72;            // 1pt 对应的 CSS 像素
    var MIN_SIZE = 4;               // 元素最小宽高(mm)

    // 页面元素
    var el = {};
    ['lpWidth', 'lpHeight', 'lpZoom', 'lpBorder', 'lpCanvas', 'lpCanvasHolder', 'lpCanvasSizeTip',
        'lpElementList', 'lpPropEmpty', 'lpPropForm', 'lpPropType', 'lpPropKeyWrap', 'lpPropKey',
        'lpPropTextWrap', 'lpPropText', 'lpPropX', 'lpPropY', 'lpPropW', 'lpPropH', 'lpPropFontWrap',
        'lpPropFont', 'lpPropAlignWrap', 'lpPropAlign', 'lpPropColor', 'lpPropBoldWrap', 'lpPropBold',
        'lpPropShowTextWrap', 'lpPropShowText', 'lpPropFont',
        'lpPropTextGroup', 'lpPropFamilyWrap', 'lpPropFamily', 'lpPropValignWrap', 'lpPropValign',
        'lpPropLineHeightWrap', 'lpPropLineHeight', 'lpPropLetterSpacingWrap', 'lpPropLetterSpacing',
        'lpPropItalicWrap', 'lpPropItalic', 'lpPropUnderlineWrap', 'lpPropUnderline',
        'lpPropWrapWrap', 'lpPropWrap',
        'lpPropQrGroup', 'lpPropEc', 'lpPropQrMargin',
        'lpPropImgGroup', 'lpPropImgUrl', 'lpPropImgUpload', 'lpPropImgClear', 'lpPropImgFit',
        'lpPropImgTip', 'lpPropImgFile',
        'lpPropBoxGroup', 'lpPropBg', 'lpPropBgNone', 'lpPropBorderW', 'lpPropBorderColor',
        'lpPropRadius', 'lpPropRotate', 'lpPropOpacity',
        'lpDupElBtn', 'lpDelElBtn',
        'lpPreset', 'lpTplSelect', 'lpTplName', 'lpSaveBtn', 'lpSaveAsBtn', 'lpNewBtn', 'lpDeleteTplBtn',
        'lpExportBtn', 'lpImportBtn', 'lpImportFile', 'lpStatus',
        'lpDataHead', 'lpDataBody', 'lpDataEmpty', 'lpRowTip', 'lpAddRowBtn', 'lpPasteBtn',
        'lpCsvBtn', 'lpCsvFile', 'lpClearRowsBtn',
        'lpPreviewBtn', 'lpPrintBtn', 'lpPreviewModal', 'lpPreviewBox', 'lpPreviewTip', 'lpPreviewPrint',
        'lpPasteModal', 'lpPasteText', 'lpPasteHeader', 'lpPasteConfirm',
        'lpPrintRoot'
    ].forEach(function (id) { el[id] = document.getElementById(id); });

    // 全局状态
    var state = {
        tplId: null,            // 当前模版 ID（null 表示未保存）
        width: 60,              // 标签宽(mm)
        height: 40,             // 标签高(mm)
        border: true,           // 外框线
        elements: [],           // 元素数组
        selectedId: null,       // 选中元素 ID
        rows: [],               // 数据行 [{_k, 字段...}]
        rowSeq: 1,              // 行主键自增
        templates: []           // 已保存模版列表
    };

    // 通用外观默认值（填充/边框/圆角/旋转/透明度 + 文本排版），近似 Excel 的自由度
    var STYLE_DEFAULTS = {
        fontFamily: '',        // 空 = 跟随全局字体
        italic: false,
        underline: false,
        valign: 'middle',      // top / middle / bottom
        lineHeight: 1.15,
        letterSpacing: 0,      // pt
        wrap: true,            // 自动换行
        bg: '',                // 空 = 无填充
        borderWidth: 0,        // mm
        borderColor: '#000000',
        radius: 0,             // mm
        rotate: 0,             // 度
        opacity: 100           // %
    };

    // ---------- 通用工具 ----------
    function uid() {
        return 'e' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    }

    function round1(n) {
        return Math.round(Number(n || 0) * 2) / 2;   // 取 0.5mm 精度
    }

    function num(v, def) {
        var n = parseFloat(v);
        return isNaN(n) ? def : n;
    }

    function clamp(v, min, max) {
        return Math.min(max, Math.max(min, v));
    }

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function status(msg, ok) {
        if (!el.lpStatus) { return; }
        el.lpStatus.textContent = msg || '';
        el.lpStatus.className = 'lp-status' + (msg ? (ok ? ' ok' : ' err') : '');
    }

    function csrfToken() {
        var m = document.querySelector('meta[name="csrf-token"]');
        return m ? m.getAttribute('content') : '';
    }

    // 统一 JSON 请求（接口已豁免 CSRF，仍带上 token 便于后续收紧）
    function request(url, options) {
        options = options || {};
        var headers = {'X-Requested-With': 'XMLHttpRequest'};
        if (options.body) { headers['Content-Type'] = 'application/json'; }
        var t = csrfToken();
        if (t) { headers['X-CSRFToken'] = t; }
        return fetch(url, {
            method: options.method || 'GET',
            headers: headers,
            body: options.body ? JSON.stringify(options.body) : undefined,
            credentials: 'same-origin'
        }).then(function (resp) {
            return resp.json().catch(function () { return {}; }).then(function (data) {
                return {ok: resp.ok, status: resp.status, data: data || {}};
            });
        });
    }

    // ---------- Code128B 条码 ----------
    var CODE128 = [
        '212222', '222122', '222221', '121223', '121322', '131222', '122213', '122312', '132212', '221213',
        '221312', '231212', '112232', '122132', '122231', '113222', '123122', '123221', '223211', '221132',
        '221231', '213212', '223112', '312131', '311222', '321122', '321221', '312212', '322112', '322211',
        '212123', '212321', '232121', '111323', '131123', '131321', '112313', '132113', '132311', '211313',
        '231113', '231311', '112133', '112331', '132131', '113123', '113321', '133121', '313121', '211331',
        '231131', '213113', '213311', '213131', '311123', '311321', '331121', '312113', '312311', '332111',
        '314111', '221411', '431111', '111224', '111422', '121124', '121421', '141122', '141221', '112214',
        '112412', '122114', '122411', '142112', '142211', '241211', '221114', '413111', '241112', '134111',
        '111242', '121142', '121241', '114212', '124112', '124211', '411212', '421112', '421211', '212141',
        '214121', '412121', '111143', '111341', '131141', '114113', '114311', '411113', '411311', '113141',
        '114131', '311141', '411131', '211412', '211214', '211232', '2331112'
    ];

    // 生成 Code128B 的 SVG 内容；含非 ASCII 字符时返回 null
    function barcodeSvg(code) {
        var text = String(code == null ? '' : code);
        if (!text) { return null; }
        var values = [104];                        // Start B
        for (var i = 0; i < text.length; i++) {
            var cc = text.charCodeAt(i);
            if (cc < 32 || cc > 126) { return null; }
            values.push(cc - 32);
        }
        var sum = 104;                             // 校验位：StartB + Σ(值×位置)
        for (var j = 1; j < values.length; j++) { sum += values[j] * j; }
        values.push(sum % 103);
        values.push(106);                          // Stop

        var widths = '';
        for (var k = 0; k < values.length; k++) { widths += CODE128[values[k]]; }

        var quiet = 10;                            // 两侧静区(模块)
        var total = quiet * 2;
        for (var m = 0; m < widths.length; m++) { total += parseInt(widths.charAt(m), 10); }

        var rects = '';
        var x = quiet;
        for (var n = 0; n < widths.length; n++) {
            var w = parseInt(widths.charAt(n), 10);
            if (n % 2 === 0) {                     // 偶数索引为黑条
                rects += '<rect x="' + x + '" y="0" width="' + w + '" height="100"/>';
            }
            x += w;
        }
        return '<svg viewBox="0 0 ' + total + ' 100" preserveAspectRatio="none" ' +
            'xmlns="http://www.w3.org/2000/svg" style="fill:#000">' + rects + '</svg>';
    }

    // ---------- 模版与元素 ----------
    function defaultTemplate() {
        return {
            width: 60,
            height: 40,
            border: true,
            elements: [
                {id: uid(), type: 'text', x: 2, y: 2, w: 56, h: 6, fontSize: 10, bold: true,
                    align: 'center', color: '#000000', key: '', text: '大焱电子有限公司', showText: true},
                {id: uid(), type: 'field', x: 2, y: 9, w: 56, h: 7, fontSize: 14, bold: true,
                    align: 'center', color: '#000000', key: 'part_no', text: '', showText: true},
                {id: uid(), type: 'field', x: 2, y: 17, w: 28, h: 5, fontSize: 9, bold: false,
                    align: 'left', color: '#000000', key: 'lot', text: '', showText: true},
                {id: uid(), type: 'field', x: 31, y: 17, w: 27, h: 5, fontSize: 9, bold: false,
                    align: 'right', color: '#000000', key: 'qty', text: '', showText: true},
                {id: uid(), type: 'barcode', x: 2, y: 23, w: 56, h: 15, fontSize: 8, bold: false,
                    align: 'center', color: '#000000', key: 'part_no', text: '', showText: true}
            ]
        };
    }

    // 元素类型默认属性（含通用外观字段）
    function newElement(type) {
        var base = {
            id: uid(), type: type, x: 4, y: 4, w: 30, h: 8,
            fontSize: 10, bold: false, align: 'left', color: '#000000',
            key: '', text: '', showText: true,
            ec: 'M', qrMargin: 4,        // 二维码：纠错级别 / 静区模块数
            src: '', fit: 'contain'      // 图片：数据源 / 填充方式
        };
        // 合并通用外观默认值（填充、边框、圆角、旋转、透明度、排版）
        Object.keys(STYLE_DEFAULTS).forEach(function (k) { base[k] = STYLE_DEFAULTS[k]; });
        if (type === 'text') {
            base.w = 40; base.text = '文本内容'; base.align = 'center';
        } else if (type === 'field') {
            base.w = 40; base.key = 'part_no'; base.h = 6;
        } else if (type === 'barcode') {
            base.w = 50; base.h = 15; base.key = 'part_no';
        } else if (type === 'qrcode') {
            base.w = 20; base.h = 20; base.showText = false; base.align = 'center';
        } else if (type === 'image') {
            base.w = 20; base.h = 15; base.bg = '#ffffff';
        } else if (type === 'rect') {
            base.w = 20; base.h = 10; base.bg = '#eef4ff'; base.borderWidth = 0.2;
        } else if (type === 'line') {
            base.w = 50; base.h = 0.5; base.x = 5; base.y = 20;
        }
        return base;
    }

    // 规范化元素：旧模版缺失字段时逐项回退到类型默认值
    function normalizeElement(raw) {
        raw = raw || {};
        var type = raw.type || 'text';
        var base = newElement(type);
        var e = {
            id: raw.id || base.id,
            type: type,
            x: num(raw.x, base.x), y: num(raw.y, base.y),
            w: num(raw.w, base.w), h: num(raw.h, base.h),
            fontSize: num(raw.fontSize, base.fontSize),
            bold: raw.bold != null ? !!raw.bold : base.bold,
            align: raw.align || base.align,
            color: raw.color || base.color,
            key: raw.key || '',
            text: raw.text || '',
            showText: raw.showText != null ? !!raw.showText : base.showText,
            ec: raw.ec || base.ec,
            qrMargin: num(raw.qrMargin, base.qrMargin),
            src: raw.src || '',
            fit: raw.fit || base.fit
        };
        // 通用外观字段：按默认值类型分别处理（布尔 / 数字 / 字符串）
        Object.keys(STYLE_DEFAULTS).forEach(function (k) {
            var def = base[k];
            if (typeof def === 'boolean') {
                e[k] = raw[k] != null ? !!raw[k] : def;
            } else if (typeof def === 'number') {
                e[k] = num(raw[k], def);
            } else {
                e[k] = raw[k] != null ? raw[k] : def;
            }
        });
        return e;
    }

    // 套用通用外观：填充 / 边框 / 圆角 / 旋转 / 透明度；unit 为 'mm'（打印）或 'px'（画布）
    function applyBoxStyle(node, e, unit) {
        var k = unit === 'px' ? MM_PX : 1;
        var suffix = unit === 'px' ? 'px' : 'mm';
        if (e.bg) { node.style.backgroundColor = e.bg; }
        var bw = num(e.borderWidth, 0);
        if (bw > 0) {
            node.style.border = (bw * k).toFixed(2) + suffix + ' solid ' + (e.borderColor || '#000000');
        }
        var radius = num(e.radius, 0);
        if (radius > 0) { node.style.borderRadius = (radius * k).toFixed(2) + suffix; }
        var rot = num(e.rotate, 0);
        if (rot) { node.style.transform = 'rotate(' + rot + 'deg)'; }
        var op = num(e.opacity, 100);
        if (op < 100) { node.style.opacity = String(clamp(op, 0, 100) / 100); }
    }

    // 套用文字排版：字号 / 粗细 / 斜体 / 下划线 / 行高 / 字距 / 对齐
    function applyTextStyle(node, e) {
        node.style.fontSize = num(e.fontSize, 10) + 'pt';
        node.style.fontWeight = e.bold ? '700' : '400';
        node.style.fontStyle = e.italic ? 'italic' : 'normal';
        node.style.textDecoration = e.underline ? 'underline' : 'none';
        node.style.color = e.color || '#000000';
        node.style.lineHeight = String(num(e.lineHeight, 1.15));
        var ls = num(e.letterSpacing, 0);
        node.style.letterSpacing = ls ? (ls + 'pt') : 'normal';
        if (e.fontFamily) { node.style.fontFamily = e.fontFamily; }
        node.style.textAlign = e.align || 'left';
        // 垂直对齐用 flex 的 align-items 实现
        node.style.alignItems = e.valign === 'top' ? 'flex-start' :
            (e.valign === 'bottom' ? 'flex-end' : 'center');
        node.style.justifyContent = e.align === 'center' ? 'center' :
            (e.align === 'right' ? 'flex-end' : 'flex-start');
        // 自动换行开关
        node.style.whiteSpace = e.wrap === false ? 'nowrap' : 'pre-wrap';
        node.style.wordBreak = e.wrap === false ? 'normal' : 'break-all';
    }

    // 二维码内容：绑定字段优先，否则取文本占位符
    function qrContent(e, row) {
        if (e.key) { return row && row[e.key] != null ? String(row[e.key]) : ''; }
        return fillPlaceholders(e.text, row);
    }

    // 生成二维码 SVG（纯前端离线编码），失败返回 null
    function qrSvg(text, e) {
        if (!text || !window.QRCodeGen) { return null; }
        try {
            return window.QRCodeGen.svg(text, {
                ec: e.ec || 'M',
                margin: num(e.qrMargin, 4),
                color: e.color || '#000000',
                bg: 'none'
            });
        } catch (err) {
            return null;
        }
    }

    // 条码/二维码下方说明文字
    function captionNode(e, text) {
        var cap = document.createElement('div');
        cap.className = 'lp-bc-text';
        cap.style.fontSize = num(e.fontSize, 8) + 'pt';
        cap.style.fontWeight = e.bold ? '700' : '400';
        cap.style.fontStyle = e.italic ? 'italic' : 'normal';
        if (e.fontFamily) { cap.style.fontFamily = e.fontFamily; }
        cap.textContent = text;
        return cap;
    }

    // 读取图片文件：小图保留原图（保留透明通道），大图压缩为 JPEG，控制模版体积
    function handleImageFile(file, done) {
        if (!file || !/^image\//.test(file.type || '')) {
            status('请选择图片文件', false);
            return;
        }
        var reader = new FileReader();
        reader.onload = function () {
            var dataUrl = String(reader.result || '');
            var img = new Image();
            img.onload = function () {
                var maxSide = Math.max(img.width, img.height);
                if (file.size <= 200 * 1024 && maxSide <= 800) {
                    done(dataUrl);          // 体积可控，保留原始格式
                    return;
                }
                var scale = Math.min(1, 600 / maxSide);
                var cv = document.createElement('canvas');
                cv.width = Math.max(1, Math.round(img.width * scale));
                cv.height = Math.max(1, Math.round(img.height * scale));
                var ctx = cv.getContext('2d');
                ctx.fillStyle = '#ffffff';  // JPEG 无透明通道，补白底
                ctx.fillRect(0, 0, cv.width, cv.height);
                ctx.drawImage(img, 0, 0, cv.width, cv.height);
                done(cv.toDataURL('image/jpeg', 0.85));
            };
            img.onerror = function () { status('图片解析失败', false); };
            img.src = dataUrl;
        };
        reader.readAsDataURL(file);
    }

    // 生成元素内容；mode: 'print' 用真实数据，'design' 用占位预览
    function fillElementNode(node, e, mode, row) {
        var isDesign = mode === 'design';
        if (e.type === 'line') {
            var line = document.createElement('div');
            line.className = 'lp-el-line';
            line.style.background = e.color || '#000';
            node.appendChild(line);
            return;
        }
        if (e.type === 'rect') { return; }       // 矩形只有盒样式，无内容

        if (e.type === 'image') {
            var imgWrap = document.createElement('div');
            imgWrap.className = 'lp-el-img';
            if (e.src) {
                var img = document.createElement('img');
                img.src = e.src;
                img.alt = '';
                img.style.objectFit = e.fit || 'contain';
                imgWrap.appendChild(img);
            } else if (isDesign) {
                var ph = document.createElement('div');
                ph.className = 'lp-el-img-ph';
                ph.textContent = '未设置图片';
                imgWrap.appendChild(ph);
            }
            node.appendChild(imgWrap);
            return;
        }

        if (e.type === 'barcode') {
            var code = isDesign ? (e.key || 'SAMPLE') : elementText(e, row);
            var bw = document.createElement('div');
            bw.className = 'lp-el-barcode';
            var svgHtml = barcodeSvg(code);
            if (svgHtml) {
                bw.innerHTML = svgHtml;
            } else {
                // 无法编码时给出明确提示，避免打印出空白
                var warn = document.createElement('div');
                warn.style.cssText = 'flex:1;display:flex;align-items:center;justify-content:center;' +
                    'font-size:7pt;color:#000;text-align:center;';
                warn.textContent = isDesign ? '条码预览' : (code ? '条码仅支持ASCII' : '（无条码数据）');
                bw.appendChild(warn);
            }
            if (e.showText) {
                bw.appendChild(captionNode(e, isDesign ? (e.key ? '{' + e.key + '}' : '条码') : code));
            }
            node.appendChild(bw);
            return;
        }

        if (e.type === 'qrcode') {
            var text = isDesign ? (e.key ? '{' + e.key + '}' : fillPlaceholders(e.text, null))
                : qrContent(e, row);
            var qw = document.createElement('div');
            qw.className = 'lp-el-qr';
            var qrHtml = qrSvg(text, e);
            if (qrHtml) {
                qw.innerHTML = qrHtml;
            } else {
                var qWarn = document.createElement('div');
                qWarn.style.cssText = 'flex:1;display:flex;align-items:center;justify-content:center;' +
                    'font-size:7pt;color:#000;text-align:center;';
                qWarn.textContent = isDesign ? '（无二维码内容）' : '（无数据）';
                qw.appendChild(qWarn);
            }
            if (e.showText) { qw.appendChild(captionNode(e, text)); }
            node.appendChild(qw);
            return;
        }

        var body = document.createElement('div');
        body.className = 'lp-el-body';
        applyTextStyle(body, e);
        if (isDesign) {
            if (e.type === 'field') { body.textContent = e.key ? '{' + e.key + '}' : '（未绑定字段）'; }
            else { body.textContent = e.text || '（空文本）'; }
        } else {
            body.textContent = elementText(e, row);
        }
        node.appendChild(body);
    }

    // 数据列：由 field/barcode/qrcode 元素的 key 去重得到
    function dataKeys() {
        var seen = {}, keys = [];
        state.elements.forEach(function (e) {
            if ((e.type === 'field' || e.type === 'barcode' || e.type === 'qrcode') && e.key) {
                if (!seen[e.key]) { seen[e.key] = 1; keys.push(e.key); }
            }
        });
        return keys;
    }

    function selectedElement() {
        for (var i = 0; i < state.elements.length; i++) {
            if (state.elements[i].id === state.selectedId) { return state.elements[i]; }
        }
        return null;
    }

    // 把 {字段} 占位替换为当前行数据（无数据时保留占位符，便于设计时识别）
    function fillPlaceholders(text, row) {
        return String(text || '').replace(/\{([\w\u4e00-\u9fa5.-]+)\}/g, function (all, key) {
            if (row && row[key] != null && row[key] !== '') { return row[key]; }
            return all;
        });
    }

    // 计算元素实际展示文本
    function elementText(e, row) {
        if (e.type === 'text') { return fillPlaceholders(e.text, row); }
        if (e.type === 'field') { return row && row[e.key] != null ? String(row[e.key]) : ''; }
        if (e.type === 'barcode') { return row && row[e.key] != null ? String(row[e.key]) : ''; }
        if (e.type === 'qrcode') { return qrContent(e, row); }
        return '';
    }

    // 生成一个标签的 DOM（预览与打印共用）
    function buildLabel(row, forPrint) {
        var box = document.createElement('div');
        box.className = 'lp-print-label' + (state.border ? ' lp-bordered' : '');
        box.style.width = state.width + 'mm';
        box.style.height = state.height + 'mm';
        if (!forPrint) { box.style.transform = 'scale(1.5)'; box.style.transformOrigin = 'top left'; }

        state.elements.forEach(function (e) {
            var node = document.createElement('div');
            node.style.position = 'absolute';
            node.style.left = e.x + 'mm';
            node.style.top = e.y + 'mm';
            node.style.width = e.w + 'mm';
            node.style.height = e.h + 'mm';
            node.style.boxSizing = 'border-box';
            node.style.overflow = 'hidden';           // 打印态裁切超出元素框的内容
            applyBoxStyle(node, e, 'mm');             // 填充 / 边框 / 圆角 / 旋转 / 透明度
            fillElementNode(node, e, 'print', row);   // 内容按类型统一渲染
            box.appendChild(node);
        });
        return box;
    }

    // ---------- 画布渲染 ----------
    function renderCanvas(skipData) {
        var zoom = num(el.lpZoom && el.lpZoom.value, 2);
        var wPx = state.width * MM_PX;
        var hPx = state.height * MM_PX;

        // 外层容器撑开缩放后的实际占位，保证滚动条正确
        el.lpCanvasHolder.style.width = Math.ceil(wPx * zoom) + 'px';
        el.lpCanvasHolder.style.height = Math.ceil(hPx * zoom) + 'px';

        var canvas = el.lpCanvas;
        canvas.style.width = wPx + 'px';
        canvas.style.height = hPx + 'px';
        canvas.style.transform = 'scale(' + zoom + ')';
        canvas.className = 'lp-canvas' + (state.border ? ' lp-bordered' : '');
        canvas.innerHTML = '';

        state.elements.forEach(function (e) {
            var node = document.createElement('div');
            node.className = 'lp-el' + (e.id === state.selectedId ? ' selected' : '');
            node.setAttribute('data-id', e.id);
            node.style.left = (e.x * MM_PX) + 'px';
            node.style.top = (e.y * MM_PX) + 'px';
            node.style.width = (e.w * MM_PX) + 'px';
            node.style.height = (e.h * MM_PX) + 'px';
            node.style.boxSizing = 'border-box';
            // 注意：设计态外层不能设 overflow:hidden，否则会裁掉右下角缩放手柄
            applyBoxStyle(node, e, 'px');             // 填充 / 边框 / 圆角 / 旋转 / 透明度
            fillElementNode(node, e, 'design', null); // 占位预览内容

            var handle = document.createElement('div');
            handle.className = 'lp-el-handle';
            handle.setAttribute('data-role', 'resize');
            node.appendChild(handle);
            canvas.appendChild(node);
        });

        if (el.lpCanvasSizeTip) {
            el.lpCanvasSizeTip.textContent = state.width + ' × ' + state.height + ' mm';
        }
        renderElementList();
        // 拖拽/微调时无需重建数据表，避免输入焦点与性能损耗
        if (!skipData) { renderDataTable(); }
        syncPropPanel();
    }

    function renderElementList() {
        var list = el.lpElementList;
        if (!list) { return; }
        list.innerHTML = '';
        if (!state.elements.length) {
            list.innerHTML = '<div class="lp-empty">暂无元素，请从上方添加</div>';
            return;
        }
        var typeName = TYPE_NAMES;
        state.elements.forEach(function (e) {
            var item = document.createElement('div');
            item.className = 'lp-el-item' + (e.id === state.selectedId ? ' active' : '');
            item.setAttribute('data-id', e.id);
            var label = typeName[e.type] || e.type;
            // 列表摘要按类型取最有辨识度的信息
            var detail = '';
            if (e.type === 'text') { detail = e.text || ''; }
            else if (e.type === 'image') { detail = e.src ? '已设置' : '未设置'; }
            else if (e.type === 'rect') { detail = e.bg || '无填充'; }
            else if (e.type === 'qrcode') { detail = e.key || e.text || ''; }
            else if (e.type !== 'line') { detail = e.key || ''; }
            item.innerHTML = '<i class="fa fa-arrows"></i><span>' + esc(label) + '</span>' +
                '<span class="lp-el-item-key">' + esc(detail) + '</span>';
            item.addEventListener('click', function () {
                state.selectedId = e.id;
                renderCanvas();
            });
            list.appendChild(item);
        });
    }

    // ---------- 画布交互：拖动 / 缩放 ----------
    var drag = null;

    function onCanvasPointerDown(ev) {
        var node = ev.target.closest ? ev.target.closest('.lp-el') : null;
        if (!node) {
            // 点击空白处取消选中
            if (state.selectedId) {
                state.selectedId = null;
                renderCanvas();
            }
            return;
        }
        var id = node.getAttribute('data-id');
        var target = null;
        state.elements.forEach(function (e) { if (e.id === id) { target = e; } });
        if (!target) { return; }

        state.selectedId = id;
        // 仅切换选中样式，避免重绘导致拖拽目标节点丢失
        Array.prototype.forEach.call(el.lpCanvas.querySelectorAll('.lp-el'), function (n) {
            n.classList.toggle('selected', n.getAttribute('data-id') === id);
        });
        syncPropPanel();

        var zoom = num(el.lpZoom && el.lpZoom.value, 2);
        var resizing = ev.target.getAttribute && ev.target.getAttribute('data-role') === 'resize';
        drag = {
            mode: resizing ? 'resize' : 'move',
            startX: ev.clientX,
            startY: ev.clientY,
            orig: {x: target.x, y: target.y, w: target.w, h: target.h},
            el: target,
            zoom: zoom
        };
        ev.preventDefault();
    }

    function onDocumentPointerMove(ev) {
        if (!drag) { return; }
        var dx = (ev.clientX - drag.startX) / (MM_PX * drag.zoom);
        var dy = (ev.clientY - drag.startY) / (MM_PX * drag.zoom);
        var t = drag.el;
        if (drag.mode === 'move') {
            t.x = round1(clamp(drag.orig.x + dx, 0, state.width - t.w));
            t.y = round1(clamp(drag.orig.y + dy, 0, state.height - t.h));
        } else {
            t.w = round1(clamp(drag.orig.w + dx, MIN_SIZE, state.width - t.x));
            t.h = round1(clamp(drag.orig.h + dy, t.type === 'line' ? 0.5 : MIN_SIZE, state.height - t.y));
        }
        // 拖动时直接更新样式，结束后再整体刷新，保证流畅
        var node = el.lpCanvas.querySelector('.lp-el[data-id="' + t.id + '"]');
        if (node) {
            node.style.left = (t.x * MM_PX) + 'px';
            node.style.top = (t.y * MM_PX) + 'px';
            node.style.width = (t.w * MM_PX) + 'px';
            node.style.height = (t.h * MM_PX) + 'px';
        }
        syncPropPanel(true);
    }

    function onDocumentPointerUp() {
        if (!drag) { return; }
        drag = null;
        renderElementList();
        renderDataTable();
    }

    // ---------- 属性面板 ----------
    var TYPE_NAMES = {
        text: '文本', field: '字段', barcode: '条码', qrcode: '二维码',
        image: '图片', rect: '矩形', line: '分隔线'
    };

    // 按需显隐属性块
    function toggle(node, hide) {
        if (node) { node.classList.toggle('d-none', !!hide); }
    }

    function syncPropPanel(onlyGeometry) {
        var e = selectedElement();
        if (!e) {
            el.lpPropEmpty.classList.remove('d-none');
            el.lpPropForm.classList.add('d-none');
            return;
        }
        el.lpPropEmpty.classList.add('d-none');
        el.lpPropForm.classList.remove('d-none');

        if (!onlyGeometry) {
            el.lpPropType.textContent = TYPE_NAMES[e.type] || e.type;

            // 按元素类型决定各属性块的可见性
            var hasKey = e.type === 'field' || e.type === 'barcode' || e.type === 'qrcode';
            var hasText = e.type === 'text' || e.type === 'qrcode';
            var isTextLike = e.type === 'text' || e.type === 'field' || e.type === 'qrcode';
            var hasFont = isTextLike || e.type === 'barcode';

            toggle(el.lpPropKeyWrap, !hasKey);
            toggle(el.lpPropTextWrap, !hasText);
            toggle(el.lpPropFontWrap, !hasFont);
            toggle(el.lpPropAlignWrap, !isTextLike);
            toggle(el.lpPropWrapWrap, !isTextLike);
            toggle(el.lpPropFamilyWrap, !isTextLike);
            toggle(el.lpPropValignWrap, !isTextLike);
            toggle(el.lpPropLineHeightWrap, !isTextLike);
            toggle(el.lpPropLetterSpacingWrap, !isTextLike);
            toggle(el.lpPropBoldWrap, !hasFont);
            toggle(el.lpPropItalicWrap, !hasFont);
            toggle(el.lpPropUnderlineWrap, !isTextLike);
            toggle(el.lpPropShowTextWrap, e.type !== 'barcode' && e.type !== 'qrcode');
            toggle(el.lpPropQrGroup, e.type !== 'qrcode');
            toggle(el.lpPropImgGroup, e.type !== 'image');
            toggle(el.lpPropBoxGroup, e.type === 'line');   // 分隔线用自身颜色，不显示盒样式
            // 图片/矩形没有文字，整组隐藏（line 保留以显示线条颜色）
            toggle(el.lpPropTextGroup, e.type === 'image' || e.type === 'rect');

            // 基础属性回填
            el.lpPropKey.value = e.key || '';
            el.lpPropText.value = e.text || '';
            el.lpPropFont.value = num(e.fontSize, 10);
            el.lpPropAlign.value = e.align || 'left';
            el.lpPropColor.value = e.color || '#000000';
            el.lpPropBold.checked = !!e.bold;
            el.lpPropShowText.checked = !!e.showText;

            // 文字排版
            el.lpPropFamily.value = e.fontFamily || '';
            el.lpPropValign.value = e.valign || 'middle';
            el.lpPropLineHeight.value = num(e.lineHeight, 1.15);
            el.lpPropLetterSpacing.value = num(e.letterSpacing, 0);
            el.lpPropItalic.checked = !!e.italic;
            el.lpPropUnderline.checked = !!e.underline;
            el.lpPropWrap.checked = e.wrap !== false;

            // 二维码
            el.lpPropEc.value = e.ec || 'M';
            el.lpPropQrMargin.value = num(e.qrMargin, 4);

            // 图片：dataURL 体积大，输入框只回显 http(s) 外链
            var isData = /^data:/i.test(e.src || '');
            el.lpPropImgUrl.value = isData ? '' : (e.src || '');
            el.lpPropImgFit.value = e.fit || 'contain';
            el.lpPropImgTip.textContent = !e.src ? '未设置图片'
                : (isData ? '已上传本地图片（约 ' + Math.max(1, Math.round(e.src.length / 1024)) + ' KB）'
                    : '使用外链图片');

            // 通用盒样式
            var noBg = !e.bg;
            el.lpPropBg.value = e.bg || '#ffffff';
            el.lpPropBg.disabled = noBg;
            el.lpPropBgNone.checked = noBg;
            el.lpPropBorderW.value = num(e.borderWidth, 0);
            el.lpPropBorderColor.value = e.borderColor || '#000000';
            el.lpPropRadius.value = num(e.radius, 0);
            el.lpPropRotate.value = num(e.rotate, 0);
            el.lpPropOpacity.value = num(e.opacity, 100);
        }
        el.lpPropX.value = e.x;
        el.lpPropY.value = e.y;
        el.lpPropW.value = e.w;
        el.lpPropH.value = e.h;
    }

    // 属性输入 → 模型；opts: {min,max} 限幅、{fine} 保留两位小数、{lazy} 强制 change 事件
    function bindProp(input, field, isNum, opts) {
        if (!input) { return; }
        opts = opts || {};
        var evt = (opts.lazy || input.tagName === 'SELECT' || input.type === 'checkbox' ||
            input.type === 'color' || isNum) ? 'change' : 'input';
        input.addEventListener(evt, function () {
            var e = selectedElement();
            if (!e) { return; }
            if (input.type === 'checkbox') {
                e[field] = input.checked;
            } else if (isNum) {
                var v = parseFloat(input.value);
                if (isNaN(v)) { return; }
                if (opts.min != null || opts.max != null) {
                    v = clamp(v, opts.min == null ? -Infinity : opts.min,
                        opts.max == null ? Infinity : opts.max);
                }
                e[field] = opts.fine ? Math.round(v * 100) / 100 : round1(v);
                // 约束在标签范围内
                if (field === 'x') { e.x = clamp(e.x, 0, Math.max(0, state.width - e.w)); }
                if (field === 'y') { e.y = clamp(e.y, 0, Math.max(0, state.height - e.h)); }
                if (field === 'w') { e.w = clamp(e.w, MIN_SIZE, state.width - e.x); }
                if (field === 'h') { e.h = clamp(e.h, e.type === 'line' ? 0.5 : MIN_SIZE, state.height - e.y); }
            } else {
                e[field] = input.value;
            }
            // 位置/尺寸变化不重建数据表；字段 key 变化需重建列
            var geometry = field === 'x' || field === 'y' || field === 'w' || field === 'h';
            renderCanvas(geometry);
        });
    }

    // ---------- 数据表格 ----------
    function addRow(values) {
        var row = {_k: 'r' + (state.rowSeq++)};
        dataKeys().forEach(function (k) {
            row[k] = values && values[k] != null ? String(values[k]) : '';
        });
        state.rows.push(row);
        return row;
    }

    function renderDataTable() {
        var keys = dataKeys();
        var head = el.lpDataHead;
        var body = el.lpDataBody;
        if (!head || !body) { return; }

        head.innerHTML = '';
        var tr = document.createElement('tr');
        keys.forEach(function (k) {
            var th = document.createElement('th');
            th.textContent = k;
            tr.appendChild(th);
        });
        var thQty = document.createElement('th');
        thQty.className = 'lp-col-qty';
        thQty.textContent = '份数';
        tr.appendChild(thQty);
        var thOp = document.createElement('th');
        thOp.className = 'lp-col-op';
        thOp.textContent = '';
        tr.appendChild(thOp);
        head.appendChild(tr);

        body.innerHTML = '';
        state.rows.forEach(function (row) {
            var line = document.createElement('tr');
            line.setAttribute('data-k', row._k);
            keys.forEach(function (k) {
                var td = document.createElement('td');
                var input = document.createElement('input');
                input.type = 'text';
                input.className = 'form-control form-control-sm';
                input.value = row[k] == null ? '' : row[k];
                input.addEventListener('input', function () { row[k] = input.value; });
                td.appendChild(input);
                line.appendChild(td);
            });
            var tdQty = document.createElement('td');
            tdQty.className = 'lp-col-qty';
            var qty = document.createElement('input');
            qty.type = 'number';
            qty.min = '1';
            qty.step = '1';
            qty.className = 'form-control form-control-sm lp-qty-input';
            qty.value = row._qty || 1;
            qty.addEventListener('input', function () {
                row._qty = Math.max(1, parseInt(qty.value, 10) || 1);
            });
            tdQty.appendChild(qty);
            line.appendChild(tdQty);

            var tdOp = document.createElement('td');
            tdOp.className = 'lp-col-op';
            var del = document.createElement('a');
            del.className = 'lp-row-del';
            del.href = 'javascript:void(0)';
            del.title = '删除该行';
            del.innerHTML = '<i class="fa fa-trash"></i>';
            del.addEventListener('click', function () {
                state.rows = state.rows.filter(function (r) { return r._k !== row._k; });
                renderDataTable();
            });
            tdOp.appendChild(del);
            line.appendChild(tdOp);
            body.appendChild(line);
        });

        el.lpDataEmpty.classList.toggle('d-none', state.rows.length > 0);
        el.lpRowTip.textContent = state.rows.length + ' 行';
    }

    // 解析粘贴文本：Tab 优先，其次逗号；支持带标题行
    function parseTable(text, hasHeader) {
        var lines = String(text || '').replace(/\r\n?/g, '\n').split('\n')
            .filter(function (l) { return l.trim() !== ''; });
        if (!lines.length) { return {rows: [], header: []}; }
        var sep = lines[0].indexOf('\t') >= 0 ? '\t' : ',';
        var cells = lines.map(function (l) { return l.split(sep).map(function (c) { return c.trim(); }); });
        var header = null;
        if (hasHeader) { header = cells.shift(); }
        return {rows: cells, header: header || []};
    }

    function importRows(text, hasHeader) {
        var parsed = parseTable(text, hasHeader);
        if (!parsed.rows.length) { return 0; }
        var keys = dataKeys();
        var added = 0;
        parsed.rows.forEach(function (cells) {
            var values = {};
            if (parsed.header.length) {
                // 按列名匹配模版字段，未命中的列忽略
                parsed.header.forEach(function (name, i) {
                    if (keys.indexOf(name) >= 0) { values[name] = cells[i] != null ? cells[i] : ''; }
                });
            } else {
                keys.forEach(function (k, i) { values[k] = cells[i] != null ? cells[i] : ''; });
            }
            addRow(values);
            added++;
        });
        renderDataTable();
        return added;
    }

    // CSV 解析：支持双引号包裹与转义
    function parseCsv(text) {
        var out = [];
        var row = [];
        var cell = '';
        var inQuote = false;
        for (var i = 0; i < text.length; i++) {
            var ch = text.charAt(i);
            if (inQuote) {
                if (ch === '"') {
                    if (text.charAt(i + 1) === '"') { cell += '"'; i++; }
                    else { inQuote = false; }
                } else { cell += ch; }
            } else if (ch === '"') {
                inQuote = true;
            } else if (ch === ',') {
                row.push(cell); cell = '';
            } else if (ch === '\n') {
                row.push(cell); out.push(row); row = []; cell = '';
            } else if (ch !== '\r') {
                cell += ch;
            }
        }
        if (cell !== '' || row.length) { row.push(cell); out.push(row); }
        return out.filter(function (r) { return r.some(function (c) { return c.trim() !== ''; }); });
    }

    // ---------- 模版管理 ----------
    function applyTemplate(cfg, tplId, name) {
        cfg = cfg || {};
        state.tplId = tplId || null;
        state.width = num(cfg.width, 60);
        state.height = num(cfg.height, 40);
        state.border = cfg.border !== false;
        state.elements = (Array.isArray(cfg.elements) ? cfg.elements : defaultTemplate().elements)
            .map(normalizeElement);   // 旧模版缺失的新字段自动回退默认值
        state.selectedId = null;
        state.rows = [];
        state.rowSeq = 1;

        el.lpWidth.value = state.width;
        el.lpHeight.value = state.height;
        el.lpBorder.checked = state.border;
        el.lpTplName.value = name || '';
        el.lpTplSelect.value = state.tplId ? String(state.tplId) : '';

        // 重建数据行，保证字段列可用
        addRow();
        renderCanvas();
    }

    // ---------- 预设模版（数据见 label_presets.js） ----------
    function presetById(id) {
        var list = window.LabelPresets || [];
        for (var i = 0; i < list.length; i++) {
            if (list[i].id === id) { return list[i]; }
        }
        return null;
    }

    // 套用预设：覆盖画布为预设版式，名称自动填入，点「保存」即可入库
    function applyPreset(id) {
        var p = presetById(id);
        if (!p) { return; }
        if (state.elements.length &&
            !window.confirm('套用预设「' + p.name + '」将覆盖当前画布内容，继续？')) {
            el.lpPreset.value = '';
            return;
        }
        applyTemplate({
            width: p.width,
            height: p.height,
            border: p.border !== false,
            elements: p.elements
        }, null, p.name);
        status('已套用预设「' + p.name + '」，编辑后点「保存」入库', true);
        el.lpPreset.value = '';      // 重置下拉，便于再次选择同一预设
    }

    // 填充预设下拉并绑定切换事件
    function initPresets() {
        if (!el.lpPreset) { return; }
        (window.LabelPresets || []).forEach(function (p) {
            var op = document.createElement('option');
            op.value = p.id;
            op.textContent = p.name;
            el.lpPreset.appendChild(op);
        });
        el.lpPreset.addEventListener('change', function () {
            if (el.lpPreset.value) { applyPreset(el.lpPreset.value); }
        });
    }

    function currentConfig() {
        return {
            width: state.width,
            height: state.height,
            border: state.border,
            // 全字段输出，保证新增的样式/二维码/图片字段不丢失
            elements: state.elements.map(function (e) {
                var out = {};
                Object.keys(e).forEach(function (k) { out[k] = e[k]; });
                out.bold = !!e.bold;
                out.showText = e.showText !== false;
                return out;
            })
        };
    }

    function loadTemplates(keepCurrent) {
        return request('/api/label_templates').then(function (res) {
            if (!res.ok || !res.data.success) {
                status('模版加载失败：' + (res.data.error || res.status), false);
                return;
            }
            state.templates = res.data.items || [];
            var sel = el.lpTplSelect;
            sel.innerHTML = '<option value="">-- 新建模版 --</option>';
            state.templates.forEach(function (t) {
                var op = document.createElement('option');
                op.value = String(t.id);
                op.textContent = t.name;
                sel.appendChild(op);
            });
            if (keepCurrent && state.tplId) { sel.value = String(state.tplId); }
        }).catch(function (err) {
            status('模版加载异常：' + err.message, false);
        });
    }

    function saveTemplate(asNew) {
        var name = (el.lpTplName.value || '').trim();
        if (!name) {
            status('请先填写模版名称', false);
            el.lpTplName.focus();
            return;
        }
        var payload = {name: name, config: currentConfig()};
        if (!asNew && state.tplId) { payload.id = state.tplId; }

        request('/api/label_templates', {method: 'POST', body: payload}).then(function (res) {
            if (res.status === 409) {
                var dupId = res.data.conflict_id;
                if (dupId && window.confirm('已存在同名模版「' + name + '」，是否覆盖？')) {
                    request('/api/label_templates', {
                        method: 'POST',
                        body: {id: dupId, name: name, config: currentConfig()}
                    }).then(function (r2) {
                        if (r2.ok && r2.data.success) {
                            state.tplId = r2.data.item.id;
                            status('已覆盖保存', true);
                            loadTemplates(true);
                        } else {
                            status('保存失败：' + (r2.data.error || r2.status), false);
                        }
                    });
                }
                return;
            }
            if (res.ok && res.data.success) {
                state.tplId = res.data.item.id;
                status('保存成功', true);
                loadTemplates(true);
            } else {
                status('保存失败：' + (res.data.error || res.status), false);
            }
        }).catch(function (err) {
            status('保存异常：' + err.message, false);
        });
    }

    function deleteTemplate() {
        if (!state.tplId) {
            status('当前模版尚未保存', false);
            return;
        }
        var name = el.lpTplName.value || '当前模版';
        if (!window.confirm('确定删除模版「' + name + '」？')) { return; }
        request('/api/label_templates/' + state.tplId, {method: 'DELETE'}).then(function (res) {
            if (res.ok && res.data.success) {
                status('已删除', true);
                state.tplId = null;
                loadTemplates();
            } else {
                status('删除失败：' + (res.data.error || res.status), false);
            }
        }).catch(function (err) {
            status('删除异常：' + err.message, false);
        });
    }

    // 导出/导入模版 JSON，便于跨环境复用
    function exportTemplate() {
        var data = {name: el.lpTplName.value || '标签模版', config: currentConfig()};
        var blob = new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'});
        var a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = (data.name || 'label_template') + '.json';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
    }

    function importTemplateFile(file) {
        var reader = new FileReader();
        reader.onload = function () {
            try {
                var data = JSON.parse(reader.result);
                var cfg = data.config || data;
                if (!cfg || !Array.isArray(cfg.elements)) { throw new Error('缺少 elements'); }
                applyTemplate(cfg, null, data.name || '导入的模版');
                status('模版已导入，保存后生效', true);
            } catch (e) {
                status('导入失败：' + e.message, false);
            }
        };
        reader.readAsText(file, 'utf-8');
    }

    // ---------- 预览与打印 ----------
    function collectPrintRows() {
        var keys = dataKeys();
        if (!state.rows.length) { return []; }
        var list = [];
        state.rows.forEach(function (r) {
            var qty = Math.max(1, parseInt(r._qty, 10) || 1);
            // 无任何字段内容且无绑定字段时跳过空行
            var hasValue = keys.some(function (k) { return String(r[k] || '').trim() !== ''; });
            if (!hasValue && keys.length) { return; }
            for (var i = 0; i < qty; i++) { list.push(r); }
        });
        return list;
    }

    function openPreview() {
        var rows = collectPrintRows();
        if (!rows.length) {
            status('请先录入数据行', false);
            return;
        }
        var box = el.lpPreviewBox;
        box.innerHTML = '';
        el.lpPreviewTip.textContent = '共 ' + rows.length + ' 张标签（含份数展开）';
        rows.forEach(function (r) {
            var item = document.createElement('div');
            item.className = 'lp-preview-item';
            // 预览按 1.5 倍放大，外层占位同步放大后的尺寸
            var label = buildLabel(r, false);
            label.style.transform = 'none';
            label.style.width = state.width + 'mm';
            label.style.height = state.height + 'mm';
            item.style.width = 'calc(' + state.width + 'mm * 1.5 + 16px)';
            item.style.height = 'calc(' + state.height + 'mm * 1.5 + 16px)';
            item.appendChild(label);
            label.style.transform = 'scale(1.5)';
            label.style.transformOrigin = 'top left';
            box.appendChild(item);
        });
        var modal = bootstrap.Modal.getOrCreateInstance(el.lpPreviewModal);
        modal.show();
    }

    function doPrint() {
        var rows = collectPrintRows();
        if (!rows.length) {
            status('请先录入数据行', false);
            return;
        }
        var root = el.lpPrintRoot;
        root.innerHTML = '';

        // @page 尺寸必须在打印前写入（CSS 变量在 @page 中不可用）
        var style = document.getElementById('lpPageStyle');
        if (!style) {
            style = document.createElement('style');
            style.id = 'lpPageStyle';
            document.head.appendChild(style);
        }
        style.textContent = '@page { size: ' + state.width + 'mm ' + state.height + 'mm; margin: 0; }';

        rows.forEach(function (r) { root.appendChild(buildLabel(r, true)); });

        status('正在打印 ' + rows.length + ' 张...', true);
        // 等待布局与字体就绪后再触发打印，避免条码尺寸错位
        setTimeout(function () {
            window.print();
        }, 120);
    }

    // ---------- 初始化 ----------
    function bindEvents() {
        // 标签尺寸与缩放
        el.lpWidth.addEventListener('change', function () {
            state.width = clamp(num(el.lpWidth.value, 60), 10, 300);
            el.lpWidth.value = state.width;
            // 元素越界时收回到标签内
            state.elements.forEach(function (e) {
                e.w = Math.min(e.w, state.width);
                e.x = clamp(e.x, 0, Math.max(0, state.width - e.w));
            });
            renderCanvas();
        });
        el.lpHeight.addEventListener('change', function () {
            state.height = clamp(num(el.lpHeight.value, 40), 10, 300);
            el.lpHeight.value = state.height;
            state.elements.forEach(function (e) {
                e.h = Math.min(e.h, state.height);
                e.y = clamp(e.y, 0, Math.max(0, state.height - e.h));
            });
            renderCanvas();
        });
        el.lpBorder.addEventListener('change', function () {
            state.border = el.lpBorder.checked;
            renderCanvas();
        });
        el.lpZoom.addEventListener('change', renderCanvas);

        // 添加元素
        document.querySelectorAll('[data-add]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var type = btn.getAttribute('data-add');
                var e = newElement(type);
                // 依次向下错开，避免完全重叠
                var offset = (state.elements.length % 5) * 2;
                e.x = clamp(e.x + offset, 0, Math.max(0, state.width - e.w));
                e.y = clamp(e.y + offset, 0, Math.max(0, state.height - e.h));
                state.elements.push(e);
                state.selectedId = e.id;
                renderCanvas();
            });
        });

        // 画布拖动/缩放
        el.lpCanvas.addEventListener('pointerdown', onCanvasPointerDown);
        document.addEventListener('pointermove', onDocumentPointerMove);
        document.addEventListener('pointerup', onDocumentPointerUp);

        // 属性面板
        bindProp(el.lpPropKey, 'key');
        bindProp(el.lpPropText, 'text');
        bindProp(el.lpPropX, 'x', true);
        bindProp(el.lpPropY, 'y', true);
        bindProp(el.lpPropW, 'w', true);
        bindProp(el.lpPropH, 'h', true);
        bindProp(el.lpPropFont, 'fontSize', true, {min: 4, max: 72});
        bindProp(el.lpPropAlign, 'align');
        bindProp(el.lpPropColor, 'color');
        bindProp(el.lpPropBold, 'bold');
        bindProp(el.lpPropShowText, 'showText');
        // 文字排版扩展
        bindProp(el.lpPropFamily, 'fontFamily');
        bindProp(el.lpPropValign, 'valign');
        bindProp(el.lpPropLineHeight, 'lineHeight', true, {min: 0.6, max: 3, fine: true});
        bindProp(el.lpPropLetterSpacing, 'letterSpacing', true, {min: -2, max: 20, fine: true});
        bindProp(el.lpPropItalic, 'italic');
        bindProp(el.lpPropUnderline, 'underline');
        bindProp(el.lpPropWrap, 'wrap');
        // 二维码参数
        bindProp(el.lpPropEc, 'ec');
        bindProp(el.lpPropQrMargin, 'qrMargin', true, {min: 0, max: 10});
        // 图片来源 / 填充方式
        bindProp(el.lpPropImgUrl, 'src', false, {lazy: true});
        bindProp(el.lpPropImgFit, 'fit');
        // 通用盒样式
        bindProp(el.lpPropBorderW, 'borderWidth', true, {min: 0, max: 3, fine: true});
        bindProp(el.lpPropBorderColor, 'borderColor');
        bindProp(el.lpPropRadius, 'radius', true, {min: 0, max: 20, fine: true});
        bindProp(el.lpPropRotate, 'rotate', true, {min: -180, max: 180});
        bindProp(el.lpPropOpacity, 'opacity', true, {min: 10, max: 100});

        // 背景填充：勾选「无填充」时禁用取色器
        if (el.lpPropBg) {
            el.lpPropBg.addEventListener('change', function () {
                var cur = selectedElement();
                if (!cur) { return; }
                cur.bg = el.lpPropBg.value;
                el.lpPropBgNone.checked = false;
                el.lpPropBg.disabled = false;
                renderCanvas(true);
            });
        }
        if (el.lpPropBgNone) {
            el.lpPropBgNone.addEventListener('change', function () {
                var cur = selectedElement();
                if (!cur) { return; }
                if (el.lpPropBgNone.checked) {
                    cur.bg = '';
                    el.lpPropBg.disabled = true;
                } else {
                    cur.bg = el.lpPropBg.value || '#ffffff';
                    el.lpPropBg.disabled = false;
                }
                renderCanvas(true);
            });
        }

        // 图片：本地上传（压缩为 dataURL）/ 清除
        if (el.lpPropImgUpload) {
            el.lpPropImgUpload.addEventListener('click', function () { el.lpPropImgFile.click(); });
        }
        if (el.lpPropImgFile) {
            el.lpPropImgFile.addEventListener('change', function () {
                var file = this.files && this.files[0];
                var cur = selectedElement();
                this.value = '';
                if (!file || !cur || cur.type !== 'image') { return; }
                handleImageFile(file, function (dataUrl) {
                    cur.src = dataUrl;
                    renderCanvas(true);
                    status('图片已载入', true);
                });
            });
        }
        if (el.lpPropImgClear) {
            el.lpPropImgClear.addEventListener('click', function () {
                var cur = selectedElement();
                if (!cur) { return; }
                cur.src = '';
                renderCanvas(true);
            });
        }

        // 复制 / 删除元素
        el.lpDupElBtn.addEventListener('click', function () {
            var src = selectedElement();
            if (!src) { return; }
            var copy = JSON.parse(JSON.stringify(src));
            copy.id = uid();
            copy.x = clamp(copy.x + 2, 0, Math.max(0, state.width - copy.w));
            copy.y = clamp(copy.y + 2, 0, Math.max(0, state.height - copy.h));
            state.elements.push(copy);
            state.selectedId = copy.id;
            renderCanvas();
        });
        el.lpDelElBtn.addEventListener('click', function () {
            var e = selectedElement();
            if (!e) { return; }
            state.elements = state.elements.filter(function (x) { return x.id !== e.id; });
            state.selectedId = null;
            renderCanvas();
        });

        // 键盘：Delete 删除选中元素，方向键微调
        document.addEventListener('keydown', function (ev) {
            var tag = (ev.target.tagName || '').toLowerCase();
            if (tag === 'input' || tag === 'textarea' || tag === 'select') { return; }
            var e = selectedElement();
            if (!e) { return; }
            if (ev.key === 'Delete' || ev.key === 'Backspace') {
                state.elements = state.elements.filter(function (x) { return x.id !== e.id; });
                state.selectedId = null;
                renderCanvas();
                ev.preventDefault();
                return;
            }
            var step = ev.shiftKey ? 1 : 0.5;
            var moved = true;
            if (ev.key === 'ArrowLeft') { e.x = round1(clamp(e.x - step, 0, state.width - e.w)); }
            else if (ev.key === 'ArrowRight') { e.x = round1(clamp(e.x + step, 0, state.width - e.w)); }
            else if (ev.key === 'ArrowUp') { e.y = round1(clamp(e.y - step, 0, state.height - e.h)); }
            else if (ev.key === 'ArrowDown') { e.y = round1(clamp(e.y + step, 0, state.height - e.h)); }
            else { moved = false; }
            if (moved) {
                ev.preventDefault();
                renderCanvas();
            }
        });

        // 模版操作
        el.lpTplSelect.addEventListener('change', function () {
            var id = el.lpTplSelect.value;
            if (!id) {
                applyTemplate(defaultTemplate(), null, '');
                status('已新建空白模版', true);
                return;
            }
            var tpl = null;
            state.templates.forEach(function (t) { if (String(t.id) === id) { tpl = t; } });
            if (!tpl) { return; }
            applyTemplate(tpl.config, tpl.id, tpl.name);
            status('已载入模版「' + tpl.name + '」', true);
        });
        el.lpSaveBtn.addEventListener('click', function () { saveTemplate(false); });
        el.lpSaveAsBtn.addEventListener('click', function () {
            state.tplId = null;
            saveTemplate(true);
        });
        el.lpNewBtn.addEventListener('click', function () {
            applyTemplate(defaultTemplate(), null, '');
            status('已新建模版，填写名称后保存', true);
        });
        el.lpDeleteTplBtn.addEventListener('click', deleteTemplate);
        el.lpExportBtn.addEventListener('click', exportTemplate);
        el.lpImportBtn.addEventListener('click', function () { el.lpImportFile.click(); });
        el.lpImportFile.addEventListener('change', function () {
            if (this.files && this.files[0]) { importTemplateFile(this.files[0]); }
            this.value = '';
        });

        // 数据行
        el.lpAddRowBtn.addEventListener('click', function () {
            addRow();
            renderDataTable();
        });
        el.lpClearRowsBtn.addEventListener('click', function () {
            if (!state.rows.length) { return; }
            if (!window.confirm('确定清空所有数据行？')) { return; }
            state.rows = [];
            renderDataTable();
        });
        el.lpPasteBtn.addEventListener('click', function () {
            el.lpPasteText.value = '';
            bootstrap.Modal.getOrCreateInstance(el.lpPasteModal).show();
        });
        el.lpPasteConfirm.addEventListener('click', function () {
            var n = importRows(el.lpPasteText.value, el.lpPasteHeader.checked);
            bootstrap.Modal.getOrCreateInstance(el.lpPasteModal).hide();
            status(n ? ('已导入 ' + n + ' 行') : '未识别到有效数据', n > 0);
        });
        el.lpCsvBtn.addEventListener('click', function () { el.lpCsvFile.click(); });
        el.lpCsvFile.addEventListener('change', function () {
            var f = this.files && this.files[0];
            if (!f) { return; }
            var reader = new FileReader();
            reader.onload = function () {
                var table = parseCsv(String(reader.result || ''));
                if (!table.length) { status('CSV 内容为空', false); return; }
                var keys = dataKeys();
                var header = table[0];
                // 首行与字段名匹配则视为标题行
                var isHeader = header.some(function (h) { return keys.indexOf(h.trim()) >= 0; });
                var body = isHeader ? table.slice(1) : table;
                var added = 0;
                body.forEach(function (cells) {
                    var values = {};
                    if (isHeader) {
                        header.forEach(function (name, i) {
                            if (keys.indexOf(name.trim()) >= 0) { values[name.trim()] = cells[i] || ''; }
                        });
                    } else {
                        keys.forEach(function (k, i) { values[k] = cells[i] || ''; });
                    }
                    addRow(values);
                    added++;
                });
                renderDataTable();
                status('已从 CSV 导入 ' + added + ' 行', added > 0);
            };
            reader.readAsText(f, 'utf-8');
            this.value = '';
        });

        // 预览与打印
        el.lpPreviewBtn.addEventListener('click', openPreview);
        el.lpPrintBtn.addEventListener('click', doPrint);
        el.lpPreviewPrint.addEventListener('click', function () {
            bootstrap.Modal.getOrCreateInstance(el.lpPreviewModal).hide();
            setTimeout(doPrint, 200);
        });
    }

    function init() {
        bindEvents();
        initPresets();
        applyTemplate(defaultTemplate(), null, '');
        loadTemplates().then(function () {
            status('就绪：可编辑模版或直接录入数据', true);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
