"""
韭菜健檢 — Auth 層

行為:
- 沒 SUPABASE_URL → 直接回 "local-user"(本機單機 dev 用)
- 有 SUPABASE_URL 但沒登入 → render 登入/註冊 UI + st.stop()
- 已登入 → 回 user_id 字串
"""
from __future__ import annotations

import streamlit as st

from . import db


LOGIN_TITLE = "🩺 韭菜健檢"
LOGIN_SUBTITLE = "買進前,先做一次韭菜健檢"
COOKIE_NAME = "leek_check_session"
COOKIE_MAX_AGE = 30 * 86400  # 30 天

# Streamlit Cloud 公開 URL,Google OAuth 完成後 redirect 回這裡
APP_URL = "https://leek-check.streamlit.app"
# Google OAuth callback 落在這個靜態頁(JS 抓 #fragment 寫 cookie 再回主畫面)
OAUTH_CALLBACK_URL = f"{APP_URL}/app/static/oauth_callback.html"


def _get_cookies():
    """Lazy import cookies controller(避免本地沒裝時 import 爆炸)。"""
    try:
        from streamlit_cookies_controller import CookieController
        return CookieController(key="leek_check_cookies")
    except Exception:
        return None


def _try_restore_from_cookie() -> bool:
    """從 cookie 撈 refresh_token,refresh 一次 session。成功回 True。"""
    cookies = _get_cookies()
    if not cookies:
        return False
    refresh_token = cookies.get(COOKIE_NAME)
    if not refresh_token:
        return False
    client = db.get_client()
    if not client:
        return False
    try:
        res = client.auth.refresh_session(refresh_token)
        if res and res.user and res.session:
            st.session_state["user_id"] = res.user.id
            st.session_state["user_email"] = res.user.email
            st.session_state["access_token"] = res.session.access_token
            st.session_state["refresh_token"] = res.session.refresh_token
            # 把新的 refresh token 寫回 cookie
            cookies.set(COOKIE_NAME, res.session.refresh_token,
                          max_age=COOKIE_MAX_AGE)
            db.set_session_token(res.session.access_token, res.session.refresh_token)
            return True
    except Exception as e:
        print(f"[auth] cookie restore failed: {e}")
        # cookie 失效 → 清掉
        try:
            cookies.remove(COOKIE_NAME)
        except Exception:
            pass
    return False


def _try_handle_oauth_callback() -> bool:
    """檢查 OAuth callback 的兩種 channel:
       1. cookie(leek_oauth_at + leek_oauth_rt) — implicit flow JS bridge 寫進來的
       2. URL query param ?code= — PKCE flow"""
    cookies = _get_cookies()

    # Channel 1: JS bridge 寫進來的 access_token + refresh_token
    if cookies:
        try:
            at_raw = cookies.get("leek_oauth_at")
            rt_raw = cookies.get("leek_oauth_rt")
        except Exception:
            at_raw = rt_raw = None
        if at_raw and rt_raw:
            from urllib.parse import unquote
            at = unquote(at_raw)
            rt = unquote(rt_raw)
            client = db.get_client()
            if client:
                try:
                    res = client.auth.set_session(at, rt)
                    # 拿 user
                    user_res = client.auth.get_user(at)
                    user = getattr(user_res, "user", None) or user_res
                    if user and getattr(user, "id", None):
                        st.session_state["user_id"] = user.id
                        st.session_state["user_email"] = getattr(user, "email", "")
                        st.session_state["access_token"] = at
                        st.session_state["refresh_token"] = rt
                        # 寫長效 cookie 保持登入 30 天
                        try:
                            cookies.set(COOKIE_NAME, rt, max_age=COOKIE_MAX_AGE)
                        except Exception:
                            pass
                        # 清掉 oauth handoff cookies(一次性使用)
                        try:
                            cookies.remove("leek_oauth_at")
                            cookies.remove("leek_oauth_rt")
                        except Exception:
                            pass
                        db.set_session_token(at, rt)
                        return True
                except Exception as e:
                    print(f"[auth] cookie OAuth handoff failed: {e}")

    # Channel 2: PKCE ?code=
    try:
        params = st.query_params
        code = params.get("code")
    except Exception:
        return False
    if not code:
        return False
    client = db.get_client()
    if not client:
        return False
    try:
        res = client.auth.exchange_code_for_session({"auth_code": code})
        if res and res.user and res.session:
            st.session_state["user_id"] = res.user.id
            st.session_state["user_email"] = res.user.email
            st.session_state["access_token"] = res.session.access_token
            st.session_state["refresh_token"] = res.session.refresh_token
            if cookies:
                try:
                    cookies.set(COOKIE_NAME, res.session.refresh_token,
                                  max_age=COOKIE_MAX_AGE)
                except Exception:
                    pass
            db.set_session_token(res.session.access_token, res.session.refresh_token)
            try:
                st.query_params.clear()
            except Exception:
                pass
            return True
    except Exception as e:
        print(f"[auth] OAuth code exchange failed: {e}")
    return False


def get_current_user_id() -> str:
    """主入口:回傳 user_id(local mode 回 'local-user')。
    流程:OAuth callback → session_state → cookie restore → 強迫登入。"""
    if not db.USE_SUPABASE:
        return "local-user"

    # 1. Google OAuth 回流(優先,因為帶 ?code= 等於剛登入完)
    if _try_handle_oauth_callback():
        return st.session_state["user_id"]

    # 2. session_state 已有
    if st.session_state.get("user_id"):
        if st.session_state.get("access_token"):
            db.set_session_token(
                st.session_state["access_token"],
                st.session_state.get("refresh_token"),
            )
        return st.session_state["user_id"]

    # 3. 從 cookie 恢復(保持登入)
    if _try_restore_from_cookie():
        return st.session_state["user_id"]

    # 4. 未登入 → 渲染登入頁面
    _render_login_page()
    st.stop()


def _render_login_page():
    """簡潔的登入/註冊頁面 — Brand 一致風格。"""
    # Hero
    st.markdown(
        f"""
        <div style='background:linear-gradient(135deg, #0f766e 0%, #0a1a1f 35%, #16181d 100%);
                    padding:32px 36px; border-radius:16px; margin-bottom:24px;
                    border:1px solid #2f343d;
                    box-shadow: inset 0 1px 0 rgba(94,234,212,0.1)'>
          <div style='font-size:0.85rem; color:#5eead4; letter-spacing:2px; margin-bottom:4px;
                      font-weight:600'>
            LEEK CHECK · v0.1
          </div>
          <div style='font-size:2.5rem; color:#fff; font-weight:800; line-height:1.1'>
            {LOGIN_TITLE}
          </div>
          <div style='font-size:1.05rem; color:#5eead4; margin-top:8px'>
            {LOGIN_SUBTITLE}
          </div>
          <div style='font-size:0.85rem; color:#94a3b8; margin-top:6px; font-style:italic'>
            韭菜不是命,是健檢不夠勤
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    tab_login, tab_signup = st.tabs(["🔑 登入", "✨ 註冊"])

    with tab_login:
        with st.form("login_form", clear_on_submit=False, border=True):
            email = st.text_input("Email", key="login_email",
                                    placeholder="you@example.com")
            password = st.text_input("密碼", type="password", key="login_password")
            if st.form_submit_button("🔑 登入", type="primary",
                                       use_container_width=True):
                if not email or not password:
                    st.error("⚠️ 請填 email 跟密碼")
                else:
                    _do_login(email, password)

    with tab_signup:
        with st.form("signup_form", clear_on_submit=False, border=True):
            su_email = st.text_input("Email", key="signup_email")
            su_password = st.text_input("密碼(至少 6 字)", type="password",
                                          key="signup_password")
            su_password2 = st.text_input("確認密碼", type="password",
                                           key="signup_password2")
            if st.form_submit_button("✨ 註冊", type="primary",
                                       use_container_width=True):
                if not su_email or not su_password:
                    st.error("⚠️ 請填 email 跟密碼")
                elif len(su_password) < 6:
                    st.error("⚠️ 密碼至少 6 字")
                elif su_password != su_password2:
                    st.error("⚠️ 兩次密碼不一致")
                else:
                    _do_signup(su_email, su_password)

    # Footer
    st.markdown("---")
    st.caption(
        "🔒 全機 RLS 加密 · 你的資料只有你看得到 · "
        "💡 不報明牌、純客觀分析"
    )


def _render_google_button():
    """⚠️ Streamlit web 跑不通(URL fragment 限制 + static .html 被擋)。
    保留實作,等 Capacitor 包成 App 時改用 native Google Sign-In plugin 取代。
    現在不 call 這個函式。"""
    if not db.SUPABASE_URL:
        return
    from urllib.parse import urlencode
    oauth_url = (
        f"{db.SUPABASE_URL.rstrip('/')}/auth/v1/authorize?"
        + urlencode({
            "provider": "google",
            "redirect_to": OAUTH_CALLBACK_URL,
        })
    )
    try:
        st.markdown(
            f"""
            <a href='{oauth_url}' style='text-decoration:none; display:block; margin-bottom:14px'>
              <div style='background:linear-gradient(135deg, #fff 0%, #f1f5f9 100%);
                          color:#1f2937; padding:12px 18px; border-radius:10px;
                          font-weight:600; text-align:center; font-size:0.95rem;
                          border:1px solid #d1d5db;
                          box-shadow: 0 2px 8px rgba(0,0,0,0.2);
                          display:flex; align-items:center; justify-content:center; gap:10px'>
                <svg width='18' height='18' viewBox='0 0 48 48'>
                  <path fill='#4285F4' d='M45.12 24.5c0-1.56-.14-3.06-.4-4.5H24v8.51h11.84c-.51 2.75-2.06 5.08-4.39 6.64v5.52h7.11c4.16-3.83 6.56-9.47 6.56-16.17z'/>
                  <path fill='#34A853' d='M24 46c5.94 0 10.92-1.97 14.56-5.33l-7.11-5.52c-1.97 1.32-4.49 2.1-7.45 2.1-5.73 0-10.58-3.87-12.31-9.07H4.34v5.7C7.96 41.07 15.4 46 24 46z'/>
                  <path fill='#FBBC05' d='M11.69 28.18C11.25 26.86 11 25.45 11 24s.25-2.86.69-4.18v-5.7H4.34C2.85 17.09 2 20.45 2 24c0 3.55.85 6.91 2.34 9.88l7.35-5.7z'/>
                  <path fill='#EA4335' d='M24 10.75c3.23 0 6.13 1.11 8.41 3.29l6.31-6.31C34.91 4.18 29.93 2 24 2 15.4 2 7.96 6.93 4.34 14.12l7.35 5.7c1.73-5.2 6.58-9.07 12.31-9.07z'/>
                </svg>
                用 Google 一鍵登入
              </div>
            </a>
            """,
            unsafe_allow_html=True,
        )
        st.caption("使用 Google 登入後,自動建立帳號,無需 email 驗證。")
    except Exception as e:
        print(f"[auth] google oauth url gen failed: {e}")


def _do_login(email: str, password: str):
    client = db.get_client()
    if not client:
        st.error("⚠️ 資料庫連線失敗,稍後再試")
        return
    try:
        res = client.auth.sign_in_with_password({"email": email, "password": password})
        if res.user:
            st.session_state["user_id"] = res.user.id
            st.session_state["user_email"] = res.user.email
            if res.session:
                st.session_state["access_token"] = res.session.access_token
                st.session_state["refresh_token"] = res.session.refresh_token
                # 寫 cookie 保持登入(30 天)
                cookies = _get_cookies()
                if cookies:
                    try:
                        cookies.set(COOKIE_NAME, res.session.refresh_token,
                                      max_age=COOKIE_MAX_AGE)
                    except Exception as ce:
                        print(f"[auth] cookie set failed: {ce}")
            st.success("✅ 登入成功!")
            st.rerun()
        else:
            st.error("⚠️ 登入失敗 — email 或密碼錯誤")
    except Exception as e:
        msg = str(e)
        if "Invalid login credentials" in msg:
            st.error("⚠️ Email 或密碼錯誤")
        else:
            st.error(f"⚠️ 登入失敗:{msg}")


def _do_signup(email: str, password: str):
    client = db.get_client()
    if not client:
        st.error("⚠️ 資料庫連線失敗")
        return
    try:
        res = client.auth.sign_up({"email": email, "password": password})
        if res.user:
            session = res.session
            # 沒拿到 session(Supabase 預設可能還是要 email confirm)→ 立刻用同組密碼登入
            if not session:
                try:
                    login_res = client.auth.sign_in_with_password(
                        {"email": email, "password": password}
                    )
                    if login_res and login_res.session:
                        session = login_res.session
                        res = login_res
                except Exception:
                    pass
            if session and res.user:
                st.session_state["user_id"] = res.user.id
                st.session_state["user_email"] = res.user.email
                st.session_state["access_token"] = session.access_token
                st.session_state["refresh_token"] = session.refresh_token
                cookies = _get_cookies()
                if cookies:
                    try:
                        cookies.set(COOKIE_NAME, session.refresh_token,
                                      max_age=COOKIE_MAX_AGE)
                    except Exception:
                        pass
                st.success("🎉 註冊成功,已自動登入!")
                st.rerun()
            else:
                st.success("✅ 註冊成功!請到「🔑 登入」tab 用同一組 email + 密碼登入。")
        else:
            st.error("⚠️ 註冊失敗,可能 email 已被使用")
    except Exception as e:
        msg = str(e)
        if "already registered" in msg.lower() or "exists" in msg.lower():
            st.error("⚠️ 這個 email 已註冊過,請去「🔑 登入」tab")
        else:
            st.error(f"⚠️ 註冊失敗:{msg}")




def logout():
    """登出 — 清除 session_state + cookie。"""
    if db.USE_SUPABASE:
        client = db.get_client()
        if client:
            try:
                client.auth.sign_out()
            except Exception:
                pass
    # 清 cookie
    cookies = _get_cookies()
    if cookies:
        try:
            cookies.remove(COOKIE_NAME)
        except Exception:
            pass
    for k in ["user_id", "user_email", "access_token", "refresh_token"]:
        st.session_state.pop(k, None)
    st.rerun()


def render_user_menu():
    """側邊欄渲染目前 user + 登出按鈕。"""
    if not db.USE_SUPABASE:
        st.sidebar.caption("📁 本機模式")
        return
    email = st.session_state.get("user_email", "—")
    st.sidebar.markdown(
        f"<div style='background:linear-gradient(135deg, #1e293b 0%, #1a1f27 100%);"
        f"padding:10px 12px; border-radius:8px; border-left:3px solid #14b8a6;"
        f"margin-bottom:10px'>"
        f"<div style='color:#94a3b8; font-size:0.7rem'>已登入</div>"
        f"<div style='color:#fff; font-size:0.85rem; font-weight:600; "
        f"overflow-wrap:anywhere'>{email}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )
    if st.sidebar.button("🚪 登出", use_container_width=True):
        logout()
