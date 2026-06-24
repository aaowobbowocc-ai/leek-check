package tw.leekcheck.app;

import android.content.Context;
import android.os.Bundle;
import android.view.KeyEvent;
import android.view.inputmethod.InputMethodManager;
import android.webkit.CookieManager;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {

    // CSS + MutationObserver:initial hide + 持續監視 Streamlit Cloud 後注入的品牌
    private static final String HIDE_STREAMLIT_BRANDING_JS =
        "javascript:(function(){" +
        "var SELECTORS = [" +
            "'#MainMenu','footer','header[data-testid=\"stHeader\"]'," +
            "'[data-testid=\"stToolbar\"]','[data-testid=\"stDecoration\"]'," +
            "'[data-testid=\"stStatusWidget\"]','[data-testid=\"stAppDeployButton\"]'," +
            "'[data-testid=\"stToolbarActions\"]','[data-testid=\"manage-app-button\"]'," +
            "'div[class*=\"viewerBadge\"]','div[class*=\"_terminalButton\"]'," +
            "'a[href*=\"streamlit.io\"]','iframe[src*=\"streamlit.io\"]'," +
            "'a[href*=\"github.com\"]','[data-testid=\"stIcon\"][title*=\"GitHub\"]'," +
            "'[data-testid=\"stIcon\"][title*=\"Fork\"]','[aria-label*=\"GitHub\"]'," +
            "'[aria-label*=\"View source\"]','[aria-label*=\"Open menu\"]'," +
            "'svg[data-icon=\"github\"]','[class*=\"ToolbarActions\"]'" +
        "];" +
        "function hideAll(){" +
            "SELECTORS.forEach(function(sel){" +
                "try{document.querySelectorAll(sel).forEach(function(el){" +
                    "el.style.setProperty('display','none','important');" +
                    "el.style.setProperty('visibility','hidden','important');" +
                "});}catch(e){}" +
            "});" +
            "try{var h=document.querySelector('header[data-testid=\"stHeader\"]');" +
            "if(h){h.style.setProperty('height','0','important');h.style.setProperty('background','transparent','important');}}catch(e){}" +
        "}" +
        "var s=document.createElement('style');" +
        "s.innerHTML=SELECTORS.join(',')+'{display:none!important;visibility:hidden!important;}';" +
        "document.head.appendChild(s);" +
        "hideAll();" +
        "if(window.MutationObserver){" +
            "var obs=new MutationObserver(function(){hideAll();});" +
            "obs.observe(document.body,{childList:true,subtree:true,attributes:true});" +
        "}" +
        "var counter=0;var interval=setInterval(function(){" +
            "hideAll();counter++;if(counter>60){clearInterval(interval);}" +
        "},500);" +
        "})()";

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        WebView webView = this.getBridge().getWebView();

        // 開啟 Cookie 持久化(預設 Capacitor WebView cookie 不寫硬碟,關 App 就消失)
        // setAcceptCookie + setAcceptThirdPartyCookies → 讓 Supabase refresh_token cookie
        // 跨重啟保留,user 30 天內不用重新登入
        CookieManager cookieManager = CookieManager.getInstance();
        cookieManager.setAcceptCookie(true);
        cookieManager.setAcceptThirdPartyCookies(webView, true);

        // cache 改 default(允許 cookie 跟 HTTP cache 一起用)
        // 避免關 cache 連 cookie 都被連帶清除
        webView.getSettings().setCacheMode(android.webkit.WebSettings.LOAD_DEFAULT);
        webView.getSettings().setDomStorageEnabled(true);
        webView.getSettings().setDatabaseEnabled(true);

        webView.setWebViewClient(new com.getcapacitor.BridgeWebViewClient(this.getBridge()) {
            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                view.evaluateJavascript(HIDE_STREAMLIT_BRANDING_JS, null);
                // 每次頁面載入完成立刻 flush cookie 到 disk
                CookieManager.getInstance().flush();
            }
        });
    }

    /**
     * 攔截實體 / 軟體 BACK 鍵:
     * 1. 如果軟鍵盤開著 → 收起鍵盤(不退 App)
     * 2. 如果 WebView 還有歷史 → 後退一頁
     * 3. 否則 → 退出 App
     *
     * 解掉「在輸入框按倒退鍵直接閃退」的問題。
     */
    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK) {
            WebView webView = this.getBridge().getWebView();
            // 1. 鍵盤開著 → 收鍵盤
            InputMethodManager imm = (InputMethodManager)
                getSystemService(Context.INPUT_METHOD_SERVICE);
            if (imm != null && imm.isAcceptingText()) {
                imm.hideSoftInputFromWindow(webView.getWindowToken(), 0);
                return true;
            }
            // 2. WebView 有歷史可退
            if (webView != null && webView.canGoBack()) {
                webView.goBack();
                return true;
            }
            // 3. fallback → 預設行為(退 App)
        }
        return super.onKeyDown(keyCode, event);
    }
}
