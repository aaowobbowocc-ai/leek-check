package tw.leekcheck.app;

import android.os.Bundle;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {

    private static final String HIDE_STREAMLIT_BRANDING_JS =
        "javascript:(function(){" +
        "var s = document.createElement('style');" +
        "s.innerHTML = " +
            "'#MainMenu,footer,[data-testid=\"stToolbar\"],[data-testid=\"stDecoration\"]," +
            "[data-testid=\"stStatusWidget\"],[data-testid=\"stAppDeployButton\"]," +
            "[data-testid=\"stToolbarActions\"],[data-testid=\"manage-app-button\"]," +
            "div[class*=\"viewerBadge\"],a[href*=\"streamlit.io\"]," +
            "iframe[src*=\"streamlit.io\"]{display:none!important;visibility:hidden!important;}" +
            "header[data-testid=\"stHeader\"]{height:0!important;background:transparent!important;}'" +
        ";" +
        "document.head.appendChild(s);" +
        "})()";

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // 注入 CSS 隱藏 Streamlit Cloud 品牌(每次頁面載入都跑一次,蓋掉 server 注入的)
        WebView webView = this.getBridge().getWebView();
        webView.setWebViewClient(new com.getcapacitor.BridgeWebViewClient(this.getBridge()) {
            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                view.evaluateJavascript(HIDE_STREAMLIT_BRANDING_JS, null);
            }
        });
    }
}
