package tw.leekcheck.app;

import android.os.Bundle;
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
        // 關掉 cache 避免認證後快取舊頁面
        webView.getSettings().setCacheMode(android.webkit.WebSettings.LOAD_NO_CACHE);
        webView.setWebViewClient(new com.getcapacitor.BridgeWebViewClient(this.getBridge()) {
            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                view.evaluateJavascript(HIDE_STREAMLIT_BRANDING_JS, null);
            }
        });
    }
}
