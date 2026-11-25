/* 离线二维码生成器（纯 ES5，无外部依赖）
 * 实现：byte 模式 + UTF-8、版本 1~40 自适应、Reed-Solomon 纠错分块交织、
 *       8 种掩码按罚分择优、格式信息与版本信息。
 * 对外接口：
 *   QRCodeGen.matrix(text, ec)            -> 二维布尔数组（true 为深色）或 null
 *   QRCodeGen.svg(text, opts)             -> SVG 字符串或 null
 *   opts = {ec:'L'|'M'|'Q'|'H', color:'#000', bg:'#fff', margin:4}
 */
(function (global) {
    'use strict';

    // 每块纠错码字数：行序 L/M/Q/H，列序版本 1~40（索引 0 占位）
    var ECC_PER_BLOCK = [
        [-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
        [-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28],
        [-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
        [-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30]
    ];
    // 纠错块数：行序 L/M/Q/H，列序版本 1~40
    var NUM_BLOCKS = [
        [-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25],
        [-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49],
        [-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68],
        [-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81]
    ];
    var EC_INDEX = {L: 0, M: 1, Q: 2, H: 3};
    // 格式信息中的纠错级别编码位
    var EC_FORMAT_BITS = {L: 1, M: 0, Q: 3, H: 2};

    // 某版本的原始数据模块数（不含功能图案）
    function numRawDataModules(ver) {
        var result = (16 * ver + 128) * ver + 64;
        if (ver >= 2) {
            var numAlign = Math.floor(ver / 7) + 2;
            result -= (25 * numAlign - 10) * numAlign - 55;
            if (ver >= 7) { result -= 36; }
        }
        return result;
    }

    // UTF-8 编码为字节数组
    function utf8Bytes(text) {
        var out = [];
        for (var i = 0; i < text.length; i++) {
            var code = text.charCodeAt(i);
            // 处理代理对（emoji 等增补平面字符）
            if (code >= 0xD800 && code <= 0xDBFF && i + 1 < text.length) {
                var low = text.charCodeAt(i + 1);
                if (low >= 0xDC00 && low <= 0xDFFF) {
                    code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00);
                    i++;
                }
            }
            if (code < 0x80) {
                out.push(code);
            } else if (code < 0x800) {
                out.push(0xC0 | (code >> 6), 0x80 | (code & 63));
            } else if (code < 0x10000) {
                out.push(0xE0 | (code >> 12), 0x80 | ((code >> 6) & 63), 0x80 | (code & 63));
            } else {
                out.push(0xF0 | (code >> 18), 0x80 | ((code >> 12) & 63),
                    0x80 | ((code >> 6) & 63), 0x80 | (code & 63));
            }
        }
        return out;
    }

    // 字节模式的数据位流（含模式指示符与字符计数）
    function buildDataBits(bytes, ver, ec) {
        var bits = [];
        function append(val, len) {
            for (var i = len - 1; i >= 0; i--) { bits.push((val >>> i) & 1); }
        }
        append(4, 4);                                     // 模式指示符：0100 = byte
        var ccBits = ver <= 9 ? 8 : 16;                   // 字符计数位宽
        append(bytes.length, ccBits);
        for (var i = 0; i < bytes.length; i++) { append(bytes[i], 8); }

        var capacityBits = numDataCodewords(ver, ec) * 8;
        var term = Math.min(4, capacityBits - bits.length);  // 终止符最多 4 位
        append(0, term);
        append(0, (8 - bits.length % 8) % 8);             // 补齐到字节边界
        // 交替填充 0xEC / 0x11 直到容量用尽
        for (var pad = 0xEC; bits.length < capacityBits; pad ^= 0xEC ^ 0x11) {
            append(pad, 8);
        }
        return bits;
    }

    function numDataCodewords(ver, ec) {
        return Math.floor(numRawDataModules(ver) / 8) -
            ECC_PER_BLOCK[EC_INDEX[ec]][ver] * NUM_BLOCKS[EC_INDEX[ec]][ver];
    }

    // GF(256) 指数/对数表
    var GF_EXP = new Array(512);
    var GF_LOG = new Array(256);
    (function initGf() {
        var x = 1;
        for (var i = 0; i < 255; i++) {
            GF_EXP[i] = x;
            GF_LOG[x] = i;
            x <<= 1;
            if (x & 0x100) { x ^= 0x11D; }
        }
        for (var j = 255; j < 512; j++) { GF_EXP[j] = GF_EXP[j - 255]; }
    })();

    function gfMul(a, b) {
        if (a === 0 || b === 0) { return 0; }
        return GF_EXP[GF_LOG[a] + GF_LOG[b]];
    }

    // Reed-Solomon 生成多项式（次数为 degree）
    function rsGenerator(degree) {
        var poly = [1];
        for (var i = 0; i < degree; i++) {
            var next = new Array(poly.length + 1);
            for (var k = 0; k < next.length; k++) { next[k] = 0; }
            for (var j = 0; j < poly.length; j++) {
                next[j] ^= poly[j];
                next[j + 1] ^= gfMul(poly[j], GF_EXP[i]);
            }
            poly = next;
        }
        return poly;
    }

    // 计算一块数据的纠错码字
    function rsRemainder(data, gen) {
        var result = new Array(gen.length - 1);
        for (var i = 0; i < result.length; i++) { result[i] = 0; }
        for (var d = 0; d < data.length; d++) {
            var factor = data[d] ^ result[0];
            result.shift();
            result.push(0);
            for (var j = 0; j < result.length; j++) {
                result[j] ^= gfMul(gen[j + 1], factor);
            }
        }
        return result;
    }

    // 按版本与纠错级别分块、交织，返回最终码字序列
    function addEccAndInterleave(dataCodewords, ver, ec) {
        var numBlocks = NUM_BLOCKS[EC_INDEX[ec]][ver];
        var eccLen = ECC_PER_BLOCK[EC_INDEX[ec]][ver];
        var rawCodewords = Math.floor(numRawDataModules(ver) / 8);
        var numShortBlocks = numBlocks - rawCodewords % numBlocks;
        var shortBlockLen = Math.floor(rawCodewords / numBlocks);
        var gen = rsGenerator(eccLen);

        var blocks = [];
        var offset = 0;
        for (var i = 0; i < numBlocks; i++) {
            var dataLen = shortBlockLen - eccLen + (i < numShortBlocks ? 0 : 1);
            var dat = dataCodewords.slice(offset, offset + dataLen);
            offset += dataLen;
            var ecc = rsRemainder(dat, gen);
            // 短块在数据段末尾补占位，便于对齐交织
            if (i < numShortBlocks) { dat.push(0); }
            blocks.push(dat.concat(ecc));
        }

        var result = [];
        for (var col = 0; col < shortBlockLen + 1; col++) {
            for (var b = 0; b < numBlocks; b++) {
                // 跳过短块的补位与末列的纠错码
                if (col !== shortBlockLen - eccLen && (b < numShortBlocks ? false : true) === false) { /* 占位判断下方统一处理 */ }
                var arr = blocks[b];
                var idx = b < numShortBlocks ? (col <= shortBlockLen - eccLen ? col : -1) : col;
                if (idx >= 0 && idx < arr.length) {
                    if (b < numShortBlocks && idx === shortBlockLen - eccLen) { continue; }
                    result.push(arr[idx]);
                }
            }
        }
        return result;
    }

    // 对齐图案中心坐标
    function alignmentPositions(ver) {
        if (ver === 1) { return []; }
        var numAlign = Math.floor(ver / 7) + 2;
        var step = ver === 32 ? 26 : Math.ceil((ver * 4 + 4) / (numAlign * 2 - 2)) * 2;
        var result = [];
        for (var i = 0, pos = ver * 4 + 10; i < numAlign - 1; i++, pos -= step) {
            result.unshift(pos);
        }
        result.unshift(6);
        return result;
    }

    // BCH 校验位计算
    function bchRemainder(data, poly, bits) {
        var val = data << bits;
        for (var i = 31; i >= bits; i--) {
            if ((val >>> i) & 1) { val ^= poly << (i - bits); }
        }
        return val;
    }

    var MASKS = [
        function (x, y) { return (x + y) % 2 === 0; },
        function (x, y) { return y % 2 === 0; },
        function (x, y) { return x % 3 === 0; },
        function (x, y) { return (x + y) % 3 === 0; },
        function (x, y) { return (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0; },
        function (x, y) { return x * y % 2 + x * y % 3 === 0; },
        function (x, y) { return (x * y % 2 + x * y % 3) % 2 === 0; },
        function (x, y) { return ((x + y) % 2 + x * y % 3) % 2 === 0; }
    ];

    // 构建某掩码下的最终矩阵
    function buildMatrix(ver, ec, codewords, mask) {
        var size = ver * 4 + 17;
        var modules = [];
        var isFunc = [];
        for (var r = 0; r < size; r++) {
            modules.push(new Array(size));
            isFunc.push(new Array(size));
            for (var c = 0; c < size; c++) { modules[r][c] = false; isFunc[r][c] = false; }
        }

        function setFunc(x, y, dark) {
            modules[y][x] = dark;
            isFunc[y][x] = true;
        }

        // 定位图案（左上/右上/左下）及其分隔带
        function drawFinder(cx, cy) {
            for (var dy = -4; dy <= 4; dy++) {
                for (var dx = -4; dx <= 4; dx++) {
                    var x = cx + dx, y = cy + dy;
                    if (x < 0 || x >= size || y < 0 || y >= size) { continue; }
                    var dist = Math.max(Math.abs(dx), Math.abs(dy));
                    setFunc(x, y, dist !== 2 && dist !== 4);
                }
            }
        }
        drawFinder(3, 3);
        drawFinder(size - 4, 3);
        drawFinder(3, size - 4);

        // 时序图案
        for (var i = 8; i < size - 8; i++) {
            setFunc(i, 6, i % 2 === 0);
            setFunc(6, i, i % 2 === 0);
        }

        // 对齐图案
        var pos = alignmentPositions(ver);
        var n = pos.length;
        for (var a = 0; a < n; a++) {
            for (var b = 0; b < n; b++) {
                // 跳过与定位图案重叠的三个角
                if ((a === 0 && b === 0) || (a === 0 && b === n - 1) || (a === n - 1 && b === 0)) { continue; }
                for (var dy2 = -2; dy2 <= 2; dy2++) {
                    for (var dx2 = -2; dx2 <= 2; dx2++) {
                        var dist2 = Math.max(Math.abs(dx2), Math.abs(dy2));
                        setFunc(pos[a] + dx2, pos[b] + dy2, dist2 !== 1);
                    }
                }
            }
        }

        // 格式信息占位（先占位，稍后写入）
        function reserveFormat() {
            for (var k = 0; k <= 8; k++) {
                if (k !== 6) {
                    setFunc(8, k, false);
                    setFunc(k, 8, false);
                }
            }
            for (var m = 0; m < 8; m++) {
                setFunc(8, size - 1 - m, false);
                setFunc(size - 1 - m, 8, false);
            }
            setFunc(8, size - 8, false);        // 固定深色模块
        }
        reserveFormat();

        // 版本信息（版本 7 起）
        if (ver >= 7) {
            var rem = bchRemainder(ver, 0x1F25, 12);
            var vbits = (ver << 12) | rem;
            for (var v = 0; v < 18; v++) {
                var bit = ((vbits >>> v) & 1) === 1;
                var aa = size - 11 + v % 3;
                var bb = Math.floor(v / 3);
                setFunc(aa, bb, bit);
                setFunc(bb, aa, bit);
            }
        }

        // 数据区：从右下角开始，两列一组蛇形填充
        var bitIndex = 0;
        var totalBits = codewords.length * 8;
        for (var right = size - 1; right >= 1; right -= 2) {
            if (right === 6) { right = 5; }             // 跳过竖向时序列
            for (var vert = 0; vert < size; vert++) {
                for (var j = 0; j < 2; j++) {
                    var x2 = right - j;
                    var upward = ((right + 1) & 2) === 0;
                    var y2 = upward ? size - 1 - vert : vert;
                    if (isFunc[y2][x2]) { continue; }
                    var dark = false;
                    if (bitIndex < totalBits) {
                        dark = ((codewords[bitIndex >>> 3] >>> (7 - (bitIndex & 7))) & 1) === 1;
                        bitIndex++;
                    }
                    // 剩余位（remainder bits）保持浅色
                    if (MASKS[mask](x2, y2)) { dark = !dark; }
                    modules[y2][x2] = dark;
                }
            }
        }

        // 写入格式信息
        var data = (EC_FORMAT_BITS[ec] << 3) | mask;
        var fmt = bchRemainder(data, 0x537, 10) ^ 0x5412;
        for (var f = 0; f <= 5; f++) { modules[f][8] = ((fmt >>> f) & 1) === 1; }
        modules[7][8] = ((fmt >>> 6) & 1) === 1;
        modules[8][8] = ((fmt >>> 7) & 1) === 1;
        modules[8][7] = ((fmt >>> 8) & 1) === 1;
        for (var g = 9; g < 15; g++) { modules[8][14 - g] = ((fmt >>> g) & 1) === 1; }
        for (var h = 0; h < 8; h++) { modules[8][size - 1 - h] = ((fmt >>> h) & 1) === 1; }
        for (var k2 = 8; k2 < 15; k2++) { modules[size - 15 + k2][8] = ((fmt >>> k2) & 1) === 1; }
        modules[size - 8][8] = true;                    // 固定深色模块
        return modules;
    }

    // 掩码罚分（按标准 N1~N4）
    function penalty(modules) {
        var size = modules.length;
        var score = 0;
        var dark = 0;

        function lineScore(get) {
            var total = 0;
            for (var i = 0; i < size; i++) {
                var run = 1;
                var prev = get(i, 0);
                var bits = [prev ? 1 : 0];
                for (var j = 1; j < size; j++) {
                    var cur = get(i, j);
                    bits.push(cur ? 1 : 0);
                    if (cur === prev) {
                        run++;
                        if (run === 5) { total += 3; } else if (run > 5) { total += 1; }
                    } else {
                        run = 1;
                        prev = cur;
                    }
                }
                // 规则 3：出现 10111010000 或 00001011101 模式
                var str = bits.join('');
                var pattern1 = '10111010000';
                var pattern2 = '00001011101';
                var p = -1;
                while ((p = str.indexOf(pattern1, p + 1)) >= 0) { total += 40; }
                p = -1;
                while ((p = str.indexOf(pattern2, p + 1)) >= 0) { total += 40; }
            }
            return total;
        }

        score += lineScore(function (row, col) { return modules[row][col]; });
        score += lineScore(function (col, row) { return modules[row][col]; });

        // 规则 2：2×2 同色块
        for (var r = 0; r < size - 1; r++) {
            for (var c = 0; c < size - 1; c++) {
                var v = modules[r][c];
                if (v === modules[r][c + 1] && v === modules[r + 1][c] && v === modules[r + 1][c + 1]) {
                    score += 3;
                }
            }
        }

        // 规则 4：深色比例偏离 50%
        for (var i = 0; i < size; i++) {
            for (var j = 0; j < size; j++) { if (modules[i][j]) { dark++; } }
        }
        var ratio = dark * 100 / (size * size);
        score += Math.floor(Math.abs(ratio - 50) / 5) * 10;
        return score;
    }

    // 主入口：文本 -> 矩阵
    function matrix(text, ec) {
        ec = EC_INDEX[ec] != null ? ec : 'M';
        var str = String(text == null ? '' : text);
        if (!str) { return null; }
        var bytes = utf8Bytes(str);

        // 选择能容纳数据的最小版本
        var ver = 0;
        for (var v = 1; v <= 40; v++) {
            var ccBits = v <= 9 ? 8 : 16;
            if (bytes.length >= (1 << ccBits)) { continue; }
            var needBits = 4 + ccBits + bytes.length * 8;
            if (needBits <= numDataCodewords(v, ec) * 8) { ver = v; break; }
        }
        if (!ver) { return null; }

        var bits = buildDataBits(bytes, ver, ec);
        var dataCodewords = [];
        for (var i = 0; i < bits.length; i += 8) {
            var byteVal = 0;
            for (var j = 0; j < 8; j++) { byteVal = (byteVal << 1) | bits[i + j]; }
            dataCodewords.push(byteVal);
        }
        var codewords = addEccAndInterleave(dataCodewords, ver, ec);

        // 8 种掩码择优
        var best = null;
        var bestScore = Infinity;
        for (var mask = 0; mask < 8; mask++) {
            var m = buildMatrix(ver, ec, codewords, mask);
            var sc = penalty(m);
            if (sc < bestScore) { bestScore = sc; best = m; }
        }
        return best;
    }

    // 生成 SVG 字符串
    function svg(text, opts) {
        opts = opts || {};
        var m = matrix(text, opts.ec || 'M');
        if (!m) { return null; }
        var margin = opts.margin == null ? 4 : opts.margin;
        var size = m.length;
        var total = size + margin * 2;
        var color = opts.color || '#000000';
        var bg = opts.bg === 'none' ? 'none' : (opts.bg || '#ffffff');
        var path = '';
        for (var y = 0; y < size; y++) {
            for (var x = 0; x < size; x++) {
                if (m[y][x]) {
                    // 合并水平连续段，减少 SVG 节点数量
                    var start = x;
                    while (x + 1 < size && m[y][x + 1]) { x++; }
                    path += 'M' + (start + margin) + ' ' + (y + margin) +
                        'h' + (x - start + 1) + 'v1h-' + (x - start + 1) + 'z';
                }
            }
        }
        return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + total + ' ' + total +
            '" shape-rendering="crispEdges" preserveAspectRatio="xMidYMid meet">' +
            (bg === 'none' ? '' : '<rect width="' + total + '" height="' + total + '" fill="' + bg + '"/>') +
            '<path d="' + path + '" fill="' + color + '"/></svg>';
    }

    global.QRCodeGen = {matrix: matrix, svg: svg};
})(window);
