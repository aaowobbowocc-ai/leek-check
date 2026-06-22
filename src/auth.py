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


def get_current_user_id() -> str:
    """主入口:回傳 user_id(local mode 回 'local-user')。
    雲端模式未登入會先試 cookie restore,失敗再 render 登入頁面後 st.stop()。"""
    if not db.USE_SUPABASE:
        return "local-user"

    # 已登入(session_state 有)
    if st.session_state.get("user_id"):
        if st.session_state.get("access_token"):
            db.set_session_token(
                st.session_state["access_token"],
                st.session_state.get("refresh_token"),
            )
        return st.session_state["user_id"]

    # 試從 cookie 恢復(保持登入)
    if _try_restore_from_cookie():
        return st.session_state["user_id"]

    # 未登入 → 渲染登入頁面
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
            cols = st.columns([1, 1])
            login_btn = cols[0].form_submit_button("🔑 登入", type="primary",
                                                       use_container_width=True)
            magic_btn = cols[1].form_submit_button("📧 寄登入連結",
                                                        use_container_width=True)
            if login_btn:
                if not email or not password:
                    st.error("⚠️ 請填 email 跟密碼")
                else:
                    _do_login(email, password)
            if magic_btn:
                if not email:
                    st.error("⚠️ 請填 email")
                else:
                    _do_magic_link(email)

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
        elif "not confirmed" in msg.lower():
            st.error("⚠️ Email 尚未驗證,請去信箱點確認連結")
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
            if res.session:
                # 自動登入(若 Supabase 設定不需 email 驗證)
                st.session_state["user_id"] = res.user.id
                st.session_state["user_email"] = res.user.email
                st.session_state["access_token"] = res.session.access_token
                st.session_state["refresh_token"] = res.session.refresh_token
                # 寫 cookie 保持登入
                cookies = _get_cookies()
                if cookies:
                    try:
                        cookies.set(COOKIE_NAME, res.session.refresh_token,
                                      max_age=COOKIE_MAX_AGE)
                    except Exception:
                        pass
                st.success("🎉 註冊成功,已自動登入!")
                st.rerun()
            else:
                st.success(f"🎉 註冊成功!請去 {email} 收信點確認連結。")
        else:
            st.error("⚠️ 註冊失敗,可能 email 已被使用")
    except Exception as e:
        msg = str(e)
        if "already registered" in msg.lower() or "exists" in msg.lower():
            st.error("⚠️ 這個 email 已註冊過,請去登入")
        else:
            st.error(f"⚠️ 註冊失敗:{msg}")


def _do_magic_link(email: str):
    """Email magic link(不用密碼)"""
    client = db.get_client()
    if not client:
        st.error("⚠️ 資料庫連線失敗")
        return
    try:
        client.auth.sign_in_with_otp({"email": email})
        st.success(f"📧 已寄登入連結到 {email},去收信點連結即可登入")
    except Exception as e:
        st.error(f"⚠️ 寄送失敗:{e}")


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
