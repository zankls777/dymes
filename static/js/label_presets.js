/* 标签模版预设库
 * 只声明尺寸与元素骨架（type/x/y/w/h + 关键属性），
 * 其余样式（填充、边框、圆角、行高等）由 label_print.js 的 normalizeElement() 补齐。
 * 新增预设：复制一项，改 id / name / width / height / elements 即可。
 */
(function (global) {
    'use strict';

    // 元素简写：E(类型, x, y, 宽, 高, {附加属性})，单位 mm
    function E(type, x, y, w, h, extra) {
        var o = {type: type, x: x, y: y, w: w, h: h};
        if (extra) { Object.keys(extra).forEach(function (k) { o[k] = extra[k]; }); }
        return o;
    }

    global.LabelPresets = [
        {
            id: 'mini40x30',
            name: '40×30 小标签（料号+条码）',
            width: 40,
            height: 30,
            border: true,
            elements: [
                E('text', 1, 1, 38, 4.5, {text: '大焱电子有限公司', fontSize: 8, bold: true, align: 'center'}),
                E('field', 1, 5.5, 38, 6, {key: 'part_no', fontSize: 12, bold: true, align: 'center'}),
                E('field', 1, 12, 24, 4.5, {key: 'lot', fontSize: 7, align: 'left'}),
                E('field', 25, 12, 14, 4.5, {key: 'qty', fontSize: 7, align: 'right'}),
                E('barcode', 1, 17, 38, 12, {key: 'part_no', fontSize: 6, align: 'center'})
            ]
        },
        {
            id: 'std60x40',
            name: '60×40 标准（二维码+条码）',
            width: 60,
            height: 40,
            border: true,
            elements: [
                E('text', 1.5, 1.5, 38, 5, {text: '大焱电子有限公司', fontSize: 9, bold: true, align: 'center'}),
                E('qrcode', 41.5, 1.5, 17, 17, {key: 'qr_text', ec: 'M', qrMargin: 2}),
                E('field', 1.5, 7, 38, 6.5, {key: 'part_no', fontSize: 13, bold: true, align: 'center'}),
                E('field', 1.5, 14.5, 24, 4.5, {key: 'lot', fontSize: 8, align: 'left'}),
                E('field', 26, 14.5, 14, 4.5, {key: 'qty', fontSize: 8, align: 'right'}),
                E('barcode', 1.5, 19.5, 57, 14, {key: 'part_no', fontSize: 7, align: 'center'}),
                E('field', 1.5, 34, 40, 4.5, {key: 'date', fontSize: 7, align: 'left'}),
                E('field', 42, 34, 16.5, 4.5, {key: 'machine', fontSize: 7, align: 'right'})
            ]
        },
        {
            id: 'logo70x45',
            name: '70×45 带 Logo（图片+二维码）',
            width: 70,
            height: 45,
            border: true,
            elements: [
                // 图片位留空，套用后在「元素属性 → 图片」里上传 Logo
                E('image', 2, 2, 20, 12, {src: '', fit: 'contain'}),
                E('text', 24, 2, 44, 6, {text: '大焱电子有限公司', fontSize: 11, bold: true, align: 'center'}),
                E('qrcode', 52, 9, 16, 16, {key: 'qr_text', ec: 'M', qrMargin: 2}),
                E('field', 2, 15, 48, 7, {key: 'part_no', fontSize: 14, bold: true, align: 'left'}),
                E('field', 2, 23, 24, 5, {key: 'lot', fontSize: 8.5, align: 'left'}),
                E('field', 2, 28.5, 24, 5, {key: 'qty', fontSize: 8.5, align: 'left'}),
                E('field', 28, 23, 22, 5, {key: 'date', fontSize: 8.5, align: 'left'}),
                E('field', 28, 28.5, 22, 5, {key: 'machine', fontSize: 8.5, align: 'left'}),
                E('barcode', 2, 34.5, 48, 9, {key: 'part_no', fontSize: 6.5, align: 'center'})
            ]
        },
        {
            id: 'box100x80',
            name: '100×80 外箱标（大标签）',
            width: 100,
            height: 80,
            border: true,
            elements: [
                E('text', 3, 3, 60, 7, {text: '大焱电子有限公司', fontSize: 14, bold: true, align: 'left'}),
                E('text', 3, 10.5, 60, 5, {text: 'OUTER CARTON', fontSize: 9, align: 'left', color: '#555555'}),
                E('qrcode', 74, 3, 23, 23, {key: 'qr_text', ec: 'M', qrMargin: 2}),
                E('field', 3, 20, 68, 11, {key: 'part_no', fontSize: 20, bold: true, align: 'left'}),
                E('field', 3, 33, 34, 6, {key: 'lot', fontSize: 10, align: 'left'}),
                E('field', 3, 40, 34, 6, {key: 'qty', fontSize: 10, align: 'left'}),
                E('field', 40, 33, 32, 6, {key: 'date', fontSize: 10, align: 'left'}),
                E('field', 40, 40, 32, 6, {key: 'machine', fontSize: 10, align: 'left'}),
                E('barcode', 3, 50, 94, 20, {key: 'part_no', fontSize: 9, align: 'center'}),
                E('field', 3, 72, 94, 5, {key: 'qr_text', fontSize: 8, align: 'center', color: '#444444'})
            ]
        }
    ];
})(window);
