package com.bom3.shell;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.os.VibratorManager;
import android.graphics.Color;
import android.text.InputType;
import android.util.Log;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.DownloadListener;
import android.webkit.PermissionRequest;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;

import org.json.JSONObject;

/**
 * BOM3 资材入库 PDA 壳。
 * 作用：给网页补上「优博讯 Intent 输出」的接收能力——收到扫描广播/Intent 后转成
 * window.__bomPdaScan(条码)，网页侧已有的解析、去重、查询逻辑全部复用。
 */
public class MainActivity extends Activity {

    private static final String PREFS = "bom3_shell";
    private static final String KEY_URL = "server_url";
    /** 首次启动的默认地址，可在「菜单键」里修改 */
    private static final String DEFAULT_URL = "http://218.59.244.81:55004/";
    private static final int REQ_FILE = 1001;
    private static final String TAG = "BOM3";          // adb logcat -s BOM3 过滤

    /**
     * 各固件扫描广播 Action。
     * 优博讯固件主要是 ACTION_DECODE_DATA；
     * 新大陆/NLS 是 com.newland.mtscanner.api.action.ACTION_DECODE；
     * 商米/链想也都在列表里，尽量覆盖。
     */
    private static final String[] SCAN_ACTIONS = {
            "android.intent.ACTION_DECODE_DATA",
            "android.intent.action.DECODE_DATA",
            "com.newland.mtscanner.api.action.ACTION_DECODE",
            "com.urovo.rcv.scanneresult",
            "android.intent.action.SCAN_RESULT",
            "com.android.server.scannerservice.broadcast",
            "com.sunmi.SCAN_RESULT",
            "com.linkscan.ACTION_SCANNER",
            "ACTION_SCAN"
    };
    /** 各固件条码字段名 */
    private static final String[] SCAN_KEYS = {
            "barcode_string", "barcode", "SCAN_BARCODE1", "scanCode",
            "data", "scan_result", "value", "ScanValue", "EXTRA_DATA"
    };

    private WebView webView;
    private View splashView;                    // 启动页覆盖层
    private long splashShownAt;
    private ValueCallback<Uri[]> filePathCallback;
    private boolean pageReady = false;          // 页面 onPageFinished 后才允许注入 JS
    private String pendingCode;                 // 页面未就绪时暂存扫码结果
    private boolean receiverRegistered = false;
    private String lastCode = "";                // 原生侧去重，避免广播+Activity 双通道重复
    private long lastCodeAt = 0;
    private String bridgeJs;                     // 打包进 assets 的通用扫码桥脚本

    private final BroadcastReceiver scanReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            String action = intent == null ? "null" : intent.getAction();
            Bundle ex = intent == null ? null : intent.getExtras();
            Log.i(TAG, "broadcast received action=" + action
                    + " extras=" + (ex == null ? "null" : ex.keySet()));
            String code = extractCode(intent);
            Log.i(TAG, "broadcast code=" + code);
            deliver(code);
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);   // 仓库作业时别熄屏

        FrameLayout root = new FrameLayout(this);

        // 垂直容器：上方 WebView（占满剩余空间）+ 下方 4 个自定义快捷入口 + 刷新
        LinearLayout container = new LinearLayout(this);
        container.setOrientation(LinearLayout.VERTICAL);
        webView = new WebView(this);
        container.addView(webView, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        container.addView(buildShortcutBar(), new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(56)));
        root.addView(container, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(root);

        // 启动页：盖在 WebView 之上，页面加载完成后淡出
        splashView = getLayoutInflater().inflate(R.layout.splash, root, false);
        splashShownAt = System.currentTimeMillis();
        android.widget.TextView footer = (android.widget.TextView) splashView.findViewById(R.id.splash_footer);
        footer.setText("v" + versionName());
        root.addView(splashView, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        // 内网 http 需要放开明文与混合内容
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(true);

        CookieManager cm = CookieManager.getInstance();
        cm.setAcceptCookie(true);
        cm.setAcceptThirdPartyCookies(webView, true);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
                pageReady = false;   // 新页面开始加载，禁止注入
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                pageReady = true;
                hideSplash();
                injectBridge(view);                 // 每页注入通用扫码桥，服务端未更新也能用
                if (pendingCode != null) {          // 补发加载期间扫到的条码
                    String code = pendingCode;
                    pendingCode = null;
                    deliver(code);
                }
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request,
                                        android.webkit.WebResourceError error) {
                if (request.isForMainFrame()) {
                    hideSplash();
                    Toast.makeText(MainActivity.this,
                            "无法连接服务器，请检查地址与网络，按菜单键可修改地址",
                            Toast.LENGTH_LONG).show();
                }
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                return handleExternalUrl(url);
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return handleExternalUrl(request.getUrl().toString());
            }
        });

        webView.setWebChromeClient(new WebChromeClient() {
            /** 页面里的 <input type=file capture> 拍照识别：交给系统图片选择/相机 */
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                                             FileChooserParams params) {
                if (filePathCallback != null) {
                    filePathCallback.onReceiveValue(null);
                }
                filePathCallback = callback;
                try {
                    Intent intent = new Intent(Intent.ACTION_GET_CONTENT);
                    intent.addCategory(Intent.CATEGORY_OPENABLE);
                    intent.setType("image/*");
                    startActivityForResult(Intent.createChooser(intent, "选择条码照片"), REQ_FILE);
                    return true;
                } catch (Exception e) {
                    filePathCallback = null;
                    return false;
                }
            }

            @Override
            public void onPermissionRequest(final PermissionRequest request) {
                request.deny();   // http 内网无安全上下文，摄像头走拍照通道
            }
        });

        webView.setDownloadListener(new DownloadListener() {
            @Override
            public void onDownloadStart(String url, String userAgent, String contentDisposition,
                                        String mimeType, long contentLength) {
                startSystemDownload(url, userAgent, contentDisposition, mimeType);
            }
        });

        String saved = getSharedPreferences(PREFS, MODE_PRIVATE).getString(KEY_URL, null);
        if (saved == null || saved.trim().isEmpty()) {
            askServerUrl(true);
        } else {
            loadUrl(saved);
        }

        // 扫描广播在 Activity 创建时就注册，保持常驻直到销毁。
        // 某些 PDA 固件的扫描广播只投递到已注册的 Activity receiver，
        // 若放在 onResume 注册、onPause 注销，Activity 被切后台回来时可能漏收。
        IntentFilter filter = new IntentFilter();
        for (String action : SCAN_ACTIONS) {
            filter.addAction(action);
        }
        try {
            if (Build.VERSION.SDK_INT >= 33) {
                registerReceiver(scanReceiver, filter, Context.RECEIVER_EXPORTED);
            } else {
                registerReceiver(scanReceiver, filter);
            }
            receiverRegistered = true;
            Log.i(TAG, "scanReceiver registered, actions=" + SCAN_ACTIONS.length);
        } catch (Exception e) {
            Log.e(TAG, "scanReceiver register failed: " + e.getMessage());
            Toast.makeText(this, "扫描广播注册失败：" + e.getMessage(), Toast.LENGTH_LONG).show();
        }

        deliver(extractCode(getIntent()));   // 冷启动由「启动 Activity」模式带进来的条码
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        deliver(extractCode(intent));
    }

    @Override
    protected void onResume() {
        super.onResume();
        // receiver 已在 onCreate 常驻注册，这里不再重注册
    }

    @Override
    protected void onPause() {
        CookieManager.getInstance().flush();   // 持久化登录会话
        super.onPause();
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode != REQ_FILE) {
            super.onActivityResult(requestCode, resultCode, data);
            return;
        }
        Uri[] result = null;
        if (resultCode == RESULT_OK && data != null && data.getData() != null) {
            result = new Uri[]{data.getData()};
        }
        if (filePathCallback != null) {
            filePathCallback.onReceiveValue(result);
            filePathCallback = null;
        }
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_MENU) {   // 实体菜单键：修改服务器地址
            askServerUrl(false);
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    @Override
    protected void onDestroy() {
        if (receiverRegistered) {
            try {
                unregisterReceiver(scanReceiver);
            } catch (Exception ignored) {
            }
            receiverRegistered = false;
        }
        if (webView != null) {
            webView.destroy();
            webView = null;
        }
        super.onDestroy();
    }

    // ---------------- 启动页 ----------------

    /** 页面就绪后淡出启动页；最少停留 700ms，避免一闪而过 */
    private void hideSplash() {
        if (splashView == null) {
            return;
        }
        final View target = splashView;
        splashView = null;
        long delay = Math.max(0, 700 - (System.currentTimeMillis() - splashShownAt));
        target.postDelayed(new Runnable() {
            @Override
            public void run() {
                target.animate().alpha(0f).setDuration(220).withEndAction(new Runnable() {
                    @Override
                    public void run() {
                        ViewGroup parent = (ViewGroup) target.getParent();
                        if (parent != null) {
                            parent.removeView(target);
                        }
                    }
                }).start();
            }
        }, delay);
    }

    private String versionName() {
        try {
            return getPackageManager().getPackageInfo(getPackageName(), 0).versionName;
        } catch (Exception e) {
            return "";
        }
    }

    // ---------------- 扫码注入 ----------------

    /** 每个页面加载完成后注入通用扫码桥（assets/pda_bridge.js） */
    private void injectBridge(WebView view) {
        final String js = loadBridgeJs();
        if (js.isEmpty()) {
            return;
        }
        view.post(new Runnable() {
            @Override
            public void run() {
                if (webView == null) {
                    return;
                }
                webView.evaluateJavascript(js + "\n;'pda-bridge'", new ValueCallback<String>() {
                    @Override
                    public void onReceiveValue(String s) {
                        Log.i(TAG, "bridge injected => " + s);
                    }
                });
            }
        });
    }

    /** 读取打包进 assets 的扫码桥脚本，只读一次并缓存 */
    private String loadBridgeJs() {
        if (bridgeJs != null) {
            return bridgeJs;
        }
        InputStream in = null;
        try {
            in = getAssets().open("pda_bridge.js");
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) > 0) {
                out.write(buf, 0, n);
            }
            bridgeJs = out.toString("UTF-8");
        } catch (Exception e) {
            Log.w(TAG, "asset pda_bridge.js missing: " + e.getMessage());
            bridgeJs = "";
        } finally {
            if (in != null) {
                try {
                    in.close();
                } catch (Exception ignored) {
                }
            }
        }
        return bridgeJs;
    }

    /** 把条码注入网页；页面未就绪时先缓存 */
    private void deliver(String code) {
        if (code == null) {
            return;
        }
        final String value = code.trim();
        if (value.isEmpty()) {
            return;
        }
        long now = System.currentTimeMillis();
        if (value.equals(lastCode) && now - lastCodeAt < 800) {
            Log.d(TAG, "dup scan ignored: " + value);
            return;   // 广播与 Activity 双通道去重
        }
        lastCode = value;
        lastCodeAt = now;

        // 现场反馈：Toast + 震动，让操作员立刻知道壳收到了码
        Log.i(TAG, "deliver code=" + value + " pageReady=" + pageReady);
        Toast.makeText(this, "扫码收到：" + value, Toast.LENGTH_SHORT).show();
        vibrateShort();

        if (webView == null) {
            Log.w(TAG, "webView null, drop scan");
            return;
        }
        if (!pageReady) {
            pendingCode = value;
            Log.i(TAG, "page not ready, queued: " + value);
            return;
        }
        webView.post(new Runnable() {
            @Override
            public void run() {
                webView.evaluateJavascript(buildInjectJs(value), new ValueCallback<String>() {
                    @Override
                    public void onReceiveValue(String s) {
                        // s 形如 "bridge" / "dom" / "no-input"，用于现场快速判断注入落点
                        Log.i(TAG, "js inject " + value + " => " + s);
                        if (s != null && s.contains("no-input")) {
                            Toast.makeText(MainActivity.this,
                                    "当前页面没有扫码输入框，请先打开「资材入库」页",
                                    Toast.LENGTH_LONG).show();
                        }
                    }
                });
            }
        });
    }

    /**
     * 生成注入脚本：优先调用网页桥 window.__bomPdaScan；
     * 老版本页面没有该桥时，直接写 DOM 并派发 input / Enter 事件触发现有查询逻辑；
     * 输入框还没渲染出来则每 250ms 重试，最多 5 秒。
     */
    private String buildInjectJs(String value) {
        return "(function(){"
                + "var v=" + JSONObject.quote(value) + ";"
                + "function fire(el,t){try{el.dispatchEvent(new Event(t,{bubbles:true}));}"
                + "catch(e){try{var ev=document.createEvent('Event');ev.initEvent(t,true,true);el.dispatchEvent(ev);}catch(e2){}}}"
                + "function fill(){"
                + "if(window.__bomPdaScan){try{window.__bomPdaScan(v);}catch(e){}return 'bridge';}"
                + "var el=document.getElementById('scanInput');"
                + "if(!el){return 'no-input';}"
                + "el.value=v;"
                + "fire(el,'input');"
                + "fire(el,'change');"
                + "try{el.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',keyCode:13,which:13,bubbles:true}));}catch(e){}"
                + "var b=document.getElementById('btnQuery');if(b){try{b.click();}catch(e){}}"
                + "return 'dom';}"
                + "var r=fill();"
                + "if(r==='no-input'){var n=0,t=setInterval(function(){n++;var x=fill();"
                + "if(x!=='no-input'||n>=20){clearInterval(t);}},250);}"
                + "return r;})()";
    }

    /** 短震动 50ms，现场提示壳收到扫码 */
    private void vibrateShort() {
        try {
            if (Build.VERSION.SDK_INT >= 31) {
                VibratorManager vm = (VibratorManager) getSystemService(VIBRATOR_MANAGER_SERVICE);
                if (vm != null) vm.getDefaultVibrator().vibrate(
                        VibrationEffect.createOneShot(50, VibrationEffect.DEFAULT_AMPLITUDE));
            } else {
                @SuppressWarnings("deprecation")
                Vibrator v = (Vibrator) getSystemService(VIBRATOR_SERVICE);
                if (v != null) {
                    if (Build.VERSION.SDK_INT >= 26) {
                        v.vibrate(VibrationEffect.createOneShot(50, VibrationEffect.DEFAULT_AMPLITUDE));
                    } else {
                        v.vibrate(50);
                    }
                }
            }
        } catch (Exception ignored) {
        }
    }

    /** 从扫描 Intent 中取出条码：先按常见键名找，再兜底遍历所有字符串 extra */
    private String extractCode(Intent intent) {
        if (intent == null) {
            return null;
        }
        String code = null;
        for (String key : SCAN_KEYS) {
            String v = intent.getStringExtra(key);
            if (v != null && !v.trim().isEmpty()) {
                code = v;
                break;
            }
        }
        if (code == null) {
            Bundle extras = intent.getExtras();
            if (extras != null) {
                for (String key : extras.keySet()) {
                    Object v = extras.get(key);
                    if (v instanceof String && !((String) v).trim().isEmpty()) {
                        code = (String) v;
                        break;
                    }
                }
            }
        }
        return code;
    }

    // ---------------- 地址与页面 ----------------

    private void askServerUrl(final boolean firstRun) {
        SharedPreferences sp = getSharedPreferences(PREFS, MODE_PRIVATE);
        final EditText input = new EditText(this);
        input.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        input.setText(sp.getString(KEY_URL, DEFAULT_URL));
        input.setSelection(input.getText().length());

        AlertDialog.Builder builder = new AlertDialog.Builder(this)
                .setTitle(firstRun ? "请输入服务器地址" : "修改服务器地址")
                .setView(input)
                .setPositiveButton("打开", new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        loadUrl(input.getText().toString());
                    }
                });
        if (firstRun) {
            builder.setCancelable(false).setNegativeButton("退出", new DialogInterface.OnClickListener() {
                @Override
                public void onClick(DialogInterface dialog, int which) {
                    finish();
                }
            });
        } else {
            builder.setNegativeButton("取消", null);
        }
        builder.show();
    }

    /** 规范化并加载地址：补协议、去掉尾部多余斜杠 */
    private void loadUrl(String raw) {
        String url = raw == null ? "" : raw.trim();
        if (url.isEmpty()) {
            url = DEFAULT_URL;
        }
        if (!url.startsWith("http://") && !url.startsWith("https://")) {
            url = "http://" + url;
        }
        while (url.length() > 8 && url.endsWith("/")) {
            url = url.substring(0, url.length() - 1);
        }
        getSharedPreferences(PREFS, MODE_PRIVATE).edit().putString(KEY_URL, url).apply();
        pageReady = false;
        webView.loadUrl(url);
    }

    // ---------------- 底部快捷入口（4 个自定义 + 刷新） ----------------

    /** 4 个快捷入口的默认名称与路径，长按按钮即可自行修改 */
    private static final String[][] DEFAULT_SLOTS = {
            {"首页", "/"},
            {"资材入库", "/material/inbound"},
            {"线束扫描", "/wire/scan_entry"},
            {"数据下载", "/wire/data_download"}
    };

    /** 底部快捷按钮的文本控件（用于改完配置后刷新显示） */
    private final TextView[] slotViews = new TextView[4];

    private int dp(int value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }

    /** 构建底部按钮栏：4 个自定义入口 + 刷新 */
    private LinearLayout buildShortcutBar() {
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setBackgroundColor(Color.parseColor("#1565C0"));

        for (int i = 0; i < 4; i++) {
            final int idx = i;
            TextView tv = new TextView(this);
            tv.setGravity(Gravity.CENTER);
            tv.setTextColor(Color.WHITE);
            tv.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
            tv.setSingleLine(true);
            tv.setEllipsize(android.text.TextUtils.TruncateAt.END);
            tv.setPadding(dp(2), 0, dp(2), 0);
            tv.setOnClickListener(new View.OnClickListener() {
                @Override
                public void onClick(View v) {
                    openSlot(idx);          // 单击：跳转
                }
            });
            tv.setOnLongClickListener(new View.OnLongClickListener() {
                @Override
                public boolean onLongClick(View v) {
                    editSlot(idx);          // 长按：修改名称与路径
                    return true;
                }
            });
            slotViews[i] = tv;
            bar.addView(tv, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 1f));
            bar.addView(buildDivider());
        }

        // 刷新按钮（长按可修改服务器地址）
        TextView refresh = new TextView(this);
        refresh.setText("刷新");
        refresh.setGravity(Gravity.CENTER);
        refresh.setTextColor(Color.WHITE);
        refresh.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
        refresh.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                if (webView != null) {
                    pageReady = false;
                    webView.reload();
                }
            }
        });
        refresh.setOnLongClickListener(new View.OnLongClickListener() {
            @Override
            public boolean onLongClick(View v) {
                askServerUrl(false);        // 长按：改服务器地址
                return true;
            }
        });
        bar.addView(refresh, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 1f));

        refreshSlotLabels();
        return bar;
    }

    private View buildDivider() {
        View div = new View(this);
        div.setBackgroundColor(Color.parseColor("#42A5F5"));
        div.setLayoutParams(new LinearLayout.LayoutParams(dp(1), ViewGroup.LayoutParams.MATCH_PARENT));
        return div;
    }

    private void refreshSlotLabels() {
        for (int i = 0; i < slotViews.length; i++) {
            if (slotViews[i] != null) {
                slotViews[i].setText(slotValue(i, "_name", DEFAULT_SLOTS[i][0]));
            }
        }
    }

    /** 读取快捷入口配置；suffix 为 _name 或 _path */
    private String slotValue(int index, String suffix, String fallback) {
        String v = getSharedPreferences(PREFS, MODE_PRIVATE)
                .getString("slot" + index + suffix, null);
        return (v == null || v.trim().isEmpty()) ? fallback : v;
    }

    /** 单击快捷入口：站内路径拼到服务器地址上加载 */
    private void openSlot(int index) {
        String path = slotValue(index, "_path", DEFAULT_SLOTS[index][1]);
        String target = resolveTarget(path);
        if (target.isEmpty()) {
            Toast.makeText(this, "该快捷入口未配置路径，长按可编辑", Toast.LENGTH_SHORT).show();
            return;
        }
        if (webView != null) {
            pageReady = false;
            webView.loadUrl(target);
        }
    }

    /** 相对路径补成完整地址；已是 http(s) 开头则原样使用 */
    private String resolveTarget(String path) {
        String p = path == null ? "" : path.trim();
        if (p.isEmpty()) {
            return "";
        }
        if (p.startsWith("http://") || p.startsWith("https://")) {
            return p;
        }
        String base = getSharedPreferences(PREFS, MODE_PRIVATE).getString(KEY_URL, DEFAULT_URL);
        while (base.endsWith("/")) {
            base = base.substring(0, base.length() - 1);
        }
        if (!p.startsWith("/")) {
            p = "/" + p;
        }
        return base + p;
    }

    /** 长按快捷入口：编辑名称与路径 */
    private void editSlot(final int index) {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(16), dp(8), dp(16), 0);

        final EditText nameInput = new EditText(this);
        nameInput.setHint("按钮名称，如：线束扫描");
        nameInput.setText(slotValue(index, "_name", DEFAULT_SLOTS[index][0]));

        final EditText pathInput = new EditText(this);
        pathInput.setHint("页面路径，如：/wire/scan_entry");
        pathInput.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        pathInput.setText(slotValue(index, "_path", DEFAULT_SLOTS[index][1]));

        box.addView(nameInput);
        box.addView(pathInput);

        new AlertDialog.Builder(this)
                .setTitle("编辑快捷入口 " + (index + 1))
                .setView(box)
                .setPositiveButton("保存", new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        String name = nameInput.getText().toString().trim();
                        String path = pathInput.getText().toString().trim();
                        if (name.isEmpty()) {
                            name = DEFAULT_SLOTS[index][0];
                        }
                        getSharedPreferences(PREFS, MODE_PRIVATE).edit()
                                .putString("slot" + index + "_name", name)
                                .putString("slot" + index + "_path", path)
                                .apply();
                        refreshSlotLabels();
                        Toast.makeText(MainActivity.this, "已保存，单击即可跳转", Toast.LENGTH_SHORT).show();
                    }
                })
                .setNegativeButton("取消", null)
                .show();
    }

    /** 站内链接留在 WebView，其它协议（tel/mailto 等）交给系统 */
    private boolean handleExternalUrl(String url) {
        if (url == null) {
            return false;
        }
        if (url.startsWith("http://") || url.startsWith("https://")) {
            return false;
        }
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
        } catch (Exception ignored) {
        }
        return true;
    }

    private void startSystemDownload(String url, String userAgent, String contentDisposition, String mimeType) {
        try {
            DownloadManager.Request req = new DownloadManager.Request(Uri.parse(url));
            req.addRequestHeader("User-Agent", userAgent);
            String cookie = CookieManager.getInstance().getCookie(url);
            if (cookie != null) {
                req.addRequestHeader("Cookie", cookie);   // 沿用登录会话
            }
            String name = URLUtil.guessFileName(url, contentDisposition, mimeType);
            req.setTitle(name);
            req.setMimeType(mimeType);
            req.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            req.setDestinationInExternalFilesDir(this, Environment.DIRECTORY_DOWNLOADS, name);
            DownloadManager dm = (DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE);
            dm.enqueue(req);
            Toast.makeText(this, "开始下载：" + name, Toast.LENGTH_SHORT).show();
        } catch (Exception e) {
            Toast.makeText(this, "下载失败：" + e.getMessage(), Toast.LENGTH_LONG).show();
        }
    }
}
