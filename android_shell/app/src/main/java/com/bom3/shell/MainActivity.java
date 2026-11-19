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
import android.text.InputType;
import android.util.Log;
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
import android.widget.Toast;

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
        webView = new WebView(this);
        root.addView(webView, new FrameLayout.LayoutParams(
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
                String js = "window.__bomPdaScan?window.__bomPdaScan("
                        + JSONObject.quote(value) + "):'missing';";
                webView.evaluateJavascript(js, new ValueCallback<String>() {
                    @Override
                    public void onReceiveValue(String s) {
                        Log.i(TAG, "js inject " + value + " => " + s);
                    }
                });
            }
        });
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
