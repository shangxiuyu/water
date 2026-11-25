#!/usr/bin/env python3
"""
JD (京东) AI Agent Client - Enhanced Long-Lived Session Edition.

Provides capabilities for:
1. Dual-channel interactive login:
   - 'h5' / 'sms': Mobile H5 SMS verification code login (obtains 30-day long-lived pt_key & pt_pin).
   - 'qr': Desktop real-browser QR-code scan login (backed by persistent Chromium profile + automated keep-alive).
2. Ultra-fast HTTP session checking & automated keepalive engine to prevent server-side idle timeout.
3. Persistent browser context with device fingerprint alignment & anti-detection stealth.
4. Real-time shopping cart perception (products, quantities, specifications, prices).
5. Searching goods on JD with price, rating, shop, and self-run (自营) identification.
6. Adding products to shopping cart directly with regional stock safety verification.
7. Retrieving historical purchase orders and order status with SSO bounce resilience.
8. Tracking order logistics in real-time.
"""

from __future__ import annotations

import os
import sys
import json
import time
import re
import urllib.parse
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Any, List, Optional, Tuple

# Ensure Playwright always finds the user's shared browser cache when available
if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
    _candidate_paths = [
        Path.home() / "Library/Caches/ms-playwright",
        Path.home() / ".cache/ms-playwright",
    ]
    if os.environ.get("LOCALAPPDATA"):
        _candidate_paths.append(Path(os.environ["LOCALAPPDATA"]) / "ms-playwright")
    for _cp in _candidate_paths:
        if _cp.exists():
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(_cp)
            break

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, Page


def sync_playwright():
    try:
        from playwright.sync_api import sync_playwright as _sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "未安装 playwright 运行时，请先使用客户端内置浏览器窗口完成京东登录，或安装 playwright"
        ) from exc
    return _sync_playwright()


class JDClient:
    """Enhanced Client for interacting with JD.com with persistent sessions & keepalive."""

    DATA_DIR = Path(__file__).parent / "data"
    PROFILE_DIR = DATA_DIR / "browser_profile"
    STATE_FILE = DATA_DIR / "jd_state.json"
    USER_INFO_FILE = DATA_DIR / "user_info.json"

    # Aligned with installed Playwright Chromium version (v136) to prevent Client Hints mismatch
    DESKTOP_UA = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    )
    MOBILE_UA = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.4 Mobile/15E148 Safari/604.1"
    )

    CHROME_ARGS = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-infobars",
        "--start-maximized",
        "--disable-features=IsolateOrigins,site-per-process",
    ]

    DEFAULT_AREA_ID = "2-2830-51810-0"
    DEFAULT_AREA_NAME = "上海浦东新区张江镇"

    STEALTH_SCRIPT = """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        window.chrome = {
            runtime: { PlatformOs: { MAC: 'mac' } },
            loadTimes: function() {},
            csi: function() {},
            app: {}
        };
        Object.defineProperty(navigator, 'plugins', {
            get: () => [
                { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' }
            ]
        });
        Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
    """

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        self._last_keepalive_time: float = 0.0

    @classmethod
    def get_user_agent(cls, mobile: bool = False) -> str:
        return cls.MOBILE_UA if mobile else cls.DESKTOP_UA

    def get_user_area(self) -> Tuple[str, str]:
        """Extract user's default delivery area ID and name from saved session cookies."""
        area_id = self.DEFAULT_AREA_ID
        area_name = self.DEFAULT_AREA_NAME
        if self.STATE_FILE.exists():
            try:
                d = json.loads(self.STATE_FILE.read_text(encoding="utf-8"))
                cookie_map = {c["name"]: c["value"] for c in d.get("cookies", [])}
                if "detailedAdd_areaid" in cookie_map:
                    area_id = cookie_map["detailedAdd_areaid"]
                elif "ipLoc-djd" in cookie_map:
                    area_id = cookie_map["ipLoc-djd"].split(".")[0]

                if "detailedAdd" in cookie_map:
                    area_name = urllib.parse.unquote(cookie_map["detailedAdd"])
            except Exception:
                pass
        return area_id, area_name

    def _create_browser_and_context(
        self, p, headless: Optional[bool] = None, is_mobile: bool = False, use_profile: bool = True
    ) -> Tuple[Optional[Browser], BrowserContext]:
        """
        Create browser and context.
        Attempts launch_persistent_context for full native profile persistence;
        gracefully falls back to browser.new_context with storage_state on profile contention.
        """
        use_headless = self.headless if headless is None else headless
        ua = self.get_user_agent(mobile=is_mobile)
        viewport = {"width": 420, "height": 840} if is_mobile else {"width": 1440, "height": 900}

        if use_profile:
            try:
                ctx = p.chromium.launch_persistent_context(
                    user_data_dir=str(self.PROFILE_DIR),
                    headless=use_headless,
                    args=self.CHROME_ARGS,
                    ignore_default_args=["--enable-automation"],
                    viewport=viewport,
                    user_agent=ua,
                    locale="zh-CN",
                )
                ctx.add_init_script(self.STEALTH_SCRIPT)
                if self.STATE_FILE.exists():
                    try:
                        d = json.loads(self.STATE_FILE.read_text(encoding="utf-8"))
                        if d.get("cookies"):
                            ctx.add_cookies(d["cookies"])
                    except Exception:
                        pass
                return None, ctx
            except Exception:
                pass

        # Fallback to standard launch + storage_state
        browser = p.chromium.launch(
            headless=use_headless,
            args=self.CHROME_ARGS,
            ignore_default_args=["--enable-automation"],
        )
        context_kwargs = {
            "viewport": viewport,
            "user_agent": ua,
            "locale": "zh-CN",
        }
        if self.STATE_FILE.exists():
            try:
                context_kwargs["storage_state"] = str(self.STATE_FILE)
            except Exception:
                pass

        ctx = browser.new_context(**context_kwargs)
        ctx.add_init_script(self.STEALTH_SCRIPT)
        return browser, ctx

    def _close_browser(self, browser: Optional[Browser], ctx: BrowserContext) -> None:
        """Safely close context and browser regardless of persistent or standalone mode."""
        try:
            if browser:
                browser.close()
            elif ctx:
                ctx.close()
        except Exception:
            pass

    def _sync_state(self, ctx: BrowserContext) -> None:
        """Persist current cookies and storage state to local STATE_FILE."""
        try:
            ctx.storage_state(path=str(self.STATE_FILE))
        except Exception:
            pass

    # =========================================================================
    # 会话心跳与自动保活 (Keep-Alive Engine)
    # =========================================================================

    def touch_keepalive(self, force: bool = False) -> Dict[str, Any]:
        """
        Lightweight heartbeat keep-alive to refresh JD session on the server side.
        Sends rapid HTTP pings to passport.jd.com & cart.jd.com to slide the session expiration window.
        Syncs any newly returned cookies to STATE_FILE without launching a heavy browser.
        """
        if not self.STATE_FILE.exists():
            return {"success": False, "message": "未找到本地登录凭证，请先登录"}

        now = time.time()
        # Rate-limit to once per 30 minutes unless force=True
        if not force and (now - self._last_keepalive_time) < 1800:
            return {"success": True, "cached": True, "message": "近期已执行心跳保活"}

        try:
            state_data = json.loads(self.STATE_FILE.read_text(encoding="utf-8"))
            cookies = state_data.get("cookies", [])
            cookie_dict = {c["name"]: c["value"] for c in cookies}

            has_pt_key = "pt_key" in cookie_dict
            has_thor = "thor" in cookie_dict
            if not has_pt_key and not has_thor:
                return {"success": False, "message": "凭据文件中缺失核心鉴权 Token (thor 或 pt_key)"}

            import requests
            session = requests.Session()
            session.headers.update({
                "User-Agent": self.get_user_agent(),
                "Referer": "https://www.jd.com/",
            })
            for c in cookies:
                session.cookies.set(c["name"], c["value"], domain=c["domain"], path=c.get("path", "/"))

            # 1. Ping endpoint according to credential type
            if has_pt_key:
                # Mobile long-lived session
                cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies if "jd.com" in c.get("domain", "")])
                headers = {
                    "Cookie": cookie_str,
                    "Referer": "https://m.jd.com/",
                    "User-Agent": self.get_user_agent(mobile=True),
                }
                r_m = session.get("https://my.m.jd.com/index.html?sceneval=2&sourceType=home_m", headers=headers, timeout=8)
                is_valid = (r_m.status_code == 200) and ("我的京东" in r_m.text or "jd_" in r_m.text)
            else:
                # Desktop thor session
                now_ms = int(now * 1000)
                url_passport = f"https://passport.jd.com/user/petName/getUserInfoForMiniJd.action?callback=jsonpUserinfo&_={now_ms}"
                r1 = session.get(url_passport, timeout=8, allow_redirects=False)
                is_valid = (r1.status_code == 200) and ("NickName" in r1.text)

            # Sync any new cookies returned by server
            new_cookie_count = 0
            for sc in session.cookies:
                found = False
                for ec in cookies:
                    if ec["name"] == sc.name and (sc.domain in ec["domain"] or ec["domain"] in sc.domain):
                        if ec["value"] != sc.value:
                            ec["value"] = sc.value
                            new_cookie_count += 1
                        found = True
                        break
                if not found:
                    cookies.append({
                        "name": sc.name,
                        "value": sc.value,
                        "domain": sc.domain or ".jd.com",
                        "path": sc.path or "/",
                        "expires": sc.expires or -1,
                        "httpOnly": False,
                        "secure": True,
                        "sameSite": "Lax",
                    })
                    new_cookie_count += 1

            if new_cookie_count > 0:
                state_data["cookies"] = cookies
                self.STATE_FILE.write_text(json.dumps(state_data, ensure_ascii=False, indent=2), encoding="utf-8")

            self._last_keepalive_time = now

            # Update user info timestamp
            if self.USER_INFO_FILE.exists():
                try:
                    u_info = json.loads(self.USER_INFO_FILE.read_text(encoding="utf-8"))
                    u_info["last_keepalive"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    self.USER_INFO_FILE.write_text(json.dumps(u_info, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass

            auth_type = "pt_key (移动端长效 30 天)" if has_pt_key else "thor (网页端自动滑动续期)"
            if is_valid:
                return {
                    "success": True,
                    "auth_type": auth_type,
                    "message": "会话滑动保活成功，服务端已顺延",
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            else:
                return {
                    "success": False,
                    "auth_type": auth_type,
                    "message": "服务端会话已自然过期，需要重新登录",
                }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # =========================================================================
    # 登录与状态校验 (Dual-Mode Login & Fast Status Check)
    # =========================================================================

    def interactive_login(self, mode: str = "qr", timeout_seconds: int = 180) -> Dict[str, Any]:
        """
        Interactive login with dual-channel support:
        1. mode='h5' / 'sms': Pop up mobile view for SMS code login -> Obtains 30-day long-lived pt_key.
        2. mode='qr': Pop up Chromium window for mobile JD App QR code scan -> Obtains desktop thor session.
        """
        is_h5_mode = mode.lower() in ["h5", "sms", "phone", "mobile"]

        if is_h5_mode:
            print("\n📱 正在启动京东手机端登录窗口【长效 30 天免密模式】...")
            print("💡 请在弹出的手机窗口中输入【手机号】并获取【短信验证码】完成登录。")
            print("   👉 该模式将直接获取京东移动端专属长效凭据 (pt_key)，有效期待遇长达 30 天！\n")
            target_url = "https://plogin.m.jd.com/login/login"
        else:
            print("\n🌐 正在启动京东 PC 扫码登录窗口【极速扫码模式】...")
            print("💡 请使用【手机京东 App】扫描窗口中的二维码进行登录授权。")
            print("   👉 登录成功后会自动接入后台心跳保活引擎，定期自动滑动续期。\n")
            target_url = "https://passport.jd.com/new/login.aspx"

        with sync_playwright() as p:
            browser, ctx = self._create_browser_and_context(
                p, headless=False, is_mobile=is_h5_mode, use_profile=True
            )
            page = ctx.new_page()
            try:
                page.goto(target_url, timeout=30000)
            except Exception as e:
                print(f"⚠️ 打开登录页出错: {e}")

            start_t = time.time()
            logged_in = False
            user_nickname = ""
            has_pt_key = False

            while time.time() - start_t < timeout_seconds:
                try:
                    cur_url = page.url
                    cookies = ctx.cookies()
                    cookie_dict = {c["name"]: c["value"] for c in cookies}

                    has_pt_key = "pt_key" in cookie_dict
                    has_thor = "thor" in cookie_dict

                    if is_h5_mode:
                        # H5: completed when pt_key present or redirected away from login page
                        if (has_pt_key or has_thor) and ("plogin.m.jd.com/login" not in cur_url):
                            logged_in = True
                            user_nickname = cookie_dict.get("unick") or cookie_dict.get("pt_pin") or cookie_dict.get("pin") or ""
                            break
                    else:
                        # QR: completed when thor or pt_key present and not on passport.jd.com login page
                        if (has_thor or has_pt_key) and ("passport.jd.com" not in cur_url or "login.aspx" not in cur_url):
                            logged_in = True
                            user_nickname = cookie_dict.get("unick") or cookie_dict.get("pt_pin") or cookie_dict.get("pin") or ""
                            break
                except Exception:
                    break
                time.sleep(1.5)

            if logged_in:
                # Allow cross-domain cookies to settle
                page.wait_for_timeout(2000)

                # Touch home page to ensure full cookie synchronization
                try:
                    if is_h5_mode:
                        page.goto("https://m.jd.com", timeout=8000)
                    else:
                        page.goto("https://www.jd.com", timeout=8000)
                    page.wait_for_timeout(1000)
                except Exception:
                    pass

                self._sync_state(ctx)

                auth_desc = "pt_key (移动端长效 30 天)" if has_pt_key else "thor (网页端自动滑动续期)"
                info = {
                    "logged_in": True,
                    "auth_type": auth_desc,
                    "user_pin": user_nickname or "京东用户",
                    "login_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "last_keepalive": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
                self.USER_INFO_FILE.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
                self._close_browser(browser, ctx)

                # Immediate heartbeat to lock server-side sliding window
                self.touch_keepalive(force=True)

                return {
                    "success": True,
                    "message": f"🎉 登录成功！已成功持久化保存凭证【{auth_desc}】。",
                    "user": info,
                }
            else:
                self._close_browser(browser, ctx)
                return {"success": False, "message": "登录超时或窗口被关闭，未完成登录。"}

    def check_login_status(self) -> Dict[str, Any]:
        """
        Check whether the current saved session is still valid.
        Uses fast ~100ms HTTP verification first, with graceful fallback to browser check.
        """
        if not self.STATE_FILE.exists():
            return {
                "logged_in": False,
                "message": "未找到本地登录凭证，请执行 `python cli.py login` (扫码) 或 `python cli.py login --h5` (短信长效) 登录",
            }

        # 1. Fast HTTP verification
        try:
            state_data = json.loads(self.STATE_FILE.read_text(encoding="utf-8"))
            cookies = state_data.get("cookies", [])
            cookie_dict = {c["name"]: c["value"] for c in cookies}

            has_pt_key = "pt_key" in cookie_dict and len(cookie_dict.get("pt_key", "")) > 5
            has_thor = "thor" in cookie_dict and len(cookie_dict.get("thor", "")) > 5
            if not has_pt_key and not has_thor:
                return {"logged_in": False, "message": "凭据文件中缺失核心鉴权 Token (thor 或 pt_key)，请重新登录"}

            auth_desc = "pt_key (移动端长效 30 天)" if has_pt_key else "thor (网页端自动滑动续期)"

            # If pt_key is present: verify against mobile personal center (home.m.jd.com / my.m.jd.com)
            if has_pt_key:
                import requests
                session = requests.Session()
                cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies if "jd.com" in c.get("domain", "")])
                headers = {
                    "Cookie": cookie_str,
                    "Referer": "https://m.jd.com/",
                    "User-Agent": self.get_user_agent(mobile=True),
                }
                r_m = session.get("https://my.m.jd.com/index.html?sceneval=2&sourceType=home_m", headers=headers, timeout=6)
                if r_m.status_code == 200 and ("我的京东" in r_m.text or "jd_" in r_m.text):
                    pin = cookie_dict.get("pt_pin") or cookie_dict.get("pin") or ""
                    unick = cookie_dict.get("unick") or pin
                    m_nick = re.search(r'(jd_[a-zA-Z0-9_-]+)', r_m.text)
                    nickname = m_nick.group(1) if m_nick else unick
                    return {
                        "logged_in": True,
                        "auth_type": auth_desc,
                        "user_pin": pin or nickname or "京东用户",
                        "nickname": nickname or pin,
                        "fast_check": True,
                    }

            # If desktop thor is present: verify against passport endpoint
            if has_thor:
                import requests
                session = requests.Session()
                session.headers.update({
                    "User-Agent": self.get_user_agent(),
                    "Referer": "https://www.jd.com/",
                })
                for c in cookies:
                    session.cookies.set(c["name"], c["value"], domain=c["domain"], path=c.get("path", "/"))

                now_ms = int(time.time() * 1000)
                url = f"https://passport.jd.com/user/petName/getUserInfoForMiniJd.action?callback=jsonpUserinfo&_={now_ms}"
                r = session.get(url, timeout=5)
                if "NickName" in r.text:
                    pin = cookie_dict.get("pin") or cookie_dict.get("pt_pin") or ""
                    unick = cookie_dict.get("unick") or ""
                    m_nick = re.search(r'"NickName":"([^"]+)"', r.text)
                    nickname = m_nick.group(1) if m_nick else unick
                    return {
                        "logged_in": True,
                        "auth_type": auth_desc,
                        "user_pin": pin or nickname or "京东用户",
                        "nickname": nickname or pin,
                        "fast_check": True,
                    }
        except Exception:
            pass

        # 2. Browser-level verification
        is_mobile_check = has_pt_key
        check_url = "https://my.m.jd.com/index.html?sceneval=2&sourceType=home_m" if is_mobile_check else "https://order.jd.com/center/list.action"

        with sync_playwright() as p:
            browser, ctx = self._create_browser_and_context(p, headless=True, is_mobile=is_mobile_check)
            page = ctx.new_page()
            try:
                page.goto(check_url, timeout=20000)

                for _ in range(6):
                    page.wait_for_timeout(500)
                    if ("my.m.jd.com" in page.url) or ("order.jd.com" in page.url):
                        break

                cur_url = page.url
                if ("login" in cur_url) or ("passport.jd.com" in cur_url):
                    self._close_browser(browser, ctx)
                    return {"logged_in": False, "message": "登录态已过期，请执行 `python cli.py login` 重新登录"}

                cookies = ctx.cookies()
                cookie_dict = {c["name"]: c["value"] for c in cookies}
                pin = cookie_dict.get("pin") or cookie_dict.get("pt_pin") or ""
                unick = cookie_dict.get("unick") or ""
                has_pt_key = "pt_key" in cookie_dict

                nickname = unick
                try:
                    name_el = page.locator(".nickname, .user-name, #myjd-user-name, a:has-text('jd_')")
                    if name_el.count() > 0:
                        nickname = name_el.first.inner_text().strip()
                except Exception:
                    pass

                self._sync_state(ctx)
                self._close_browser(browser, ctx)

                return {
                    "logged_in": True,
                    "auth_type": "pt_key (移动端长效 30 天)" if has_pt_key else "thor (网页端自动滑动续期)",
                    "user_pin": pin or nickname or "已登录用户",
                    "nickname": nickname or pin,
                    "url": cur_url,
                }
            except Exception as e:
                self._close_browser(browser, ctx)
                return {"logged_in": False, "error": str(e)}

    # =========================================================================
    # 业务功能 (Cart, Search, Stock, Add, Orders, Track)
    # =========================================================================

    def get_cart(self) -> Dict[str, Any]:
        """
        Retrieve all items currently in the user's shopping cart.
        Includes product name, SKU, price, quantity, specs, shop, and validity status.
        """
        # Maintain session freshness before opening cart
        self.touch_keepalive(force=False)

        with sync_playwright() as p:
            browser, ctx = self._create_browser_and_context(p, headless=True)
            page = ctx.new_page()
            try:
                page.goto("https://cart.jd.com/cart_index", timeout=25000)
                page.wait_for_timeout(3000)

                if "passport.jd.com" in page.url:
                    self._close_browser(browser, ctx)
                    return {"success": False, "message": "登录态已过期，请先扫码登录: python cli.py login"}

                raw_items = page.evaluate("""
                    () => {
                        const items = [];
                        const itemNodes = document.querySelectorAll('.item-item, .cart-item-list, [class*="item-list"] .item-single, [id^="product_"]');
                        
                        itemNodes.forEach(node => {
                            try {
                                const id = node.id || '';
                                const skuMatch = id.match(/\\d+/) || (node.getAttribute('goods-id') ? [node.getAttribute('goods-id')] : null);
                                const skuId = skuMatch ? skuMatch[0] : '';
                                
                                const nameNode = node.querySelector('.p-name a, [class*="name"] a, a[title]');
                                const title = nameNode ? (nameNode.getAttribute('title') || nameNode.innerText.trim()) : '';
                                const link = nameNode ? nameNode.href : '';
                                
                                const priceNode = node.querySelector('.p-price strong, .price-line, [class*="price"] strong, .p-price');
                                let price = priceNode ? priceNode.innerText.replace(/[^0-9.]/g, '').trim() : '';
                                
                                const qtyInput = node.querySelector('.itxt, input[class*="quantity"], input[type="text"]');
                                const quantity = qtyInput ? (qtyInput.value || qtyInput.getAttribute('value') || '1') : '1';
                                
                                const propNode = node.querySelector('.p-props, .props-txt, [class*="props"]');
                                const specs = propNode ? propNode.innerText.replace(/\\s+/g, ' ').trim() : '';
                                
                                let shop = '';
                                const shopNode = node.closest('.cart-item-list, .cart-tbody, .shop')?.querySelector('.shop-name, .shop-txt, [class*="shop"] a');
                                if (shopNode) {
                                    shop = shopNode.innerText.trim();
                                }
                                
                                const chk = node.querySelector('input[type="checkbox"], [class*="checkbox"]');
                                const isSelected = chk ? (chk.checked || chk.classList.contains('jdcheckbox-selected')) : false;

                                if (title || skuId) {
                                    items.push({
                                        sku_id: skuId,
                                        title: title,
                                        price: price ? parseFloat(price) : 0.0,
                                        quantity: parseInt(quantity) || 1,
                                        specs: specs,
                                        shop: shop,
                                        is_selected: isSelected,
                                        link: link
                                    });
                                }
                            } catch (e) {}
                        });

                        const totalCountNode = document.querySelector('#cart-floatbar .amount-sum em, .amount-sum em');
                        const totalPriceNode = document.querySelector('#cart-floatbar .sumPrice em, .sumPrice em');
                        
                        return {
                            items: items,
                            total_selected_count: totalCountNode ? totalCountNode.innerText.trim() : '0',
                            total_selected_price: totalPriceNode ? totalPriceNode.innerText.replace(/[^0-9.]/g, '').trim() : '0.00'
                        };
                    }
                """)

                seen_skus = set()
                clean_items = []
                for it in raw_items.get("items", []):
                    sku = it.get("sku_id") or it.get("title")
                    if sku not in seen_skus:
                        seen_skus.add(sku)
                        clean_items.append(it)

                self._sync_state(ctx)
                self._close_browser(browser, ctx)
                return {
                    "success": True,
                    "total_items": len(clean_items),
                    "selected_count": raw_items.get("total_selected_count"),
                    "selected_price": raw_items.get("total_selected_price"),
                    "items": clean_items,
                }
            except Exception as e:
                self._close_browser(browser, ctx)
                return {"success": False, "error": str(e)}

    def search_goods(self, keyword: str, limit: int = 10) -> Dict[str, Any]:
        """
        Search products on JD by keyword.
        Returns item list with SKU, title, price, shop, comments, and self-run (自营) badge.
        """
        with sync_playwright() as p:
            browser, ctx = self._create_browser_and_context(p, headless=True)
            page = ctx.new_page()
            try:
                encoded_kw = keyword.strip()
                search_url = f"https://search.jd.com/Search?keyword={encoded_kw}&enc=utf-8"
                page.goto(search_url, timeout=25000)
                page.wait_for_timeout(3000)

                page.evaluate("window.scrollBy(0, 1200)")
                page.wait_for_timeout(1500)

                results = page.evaluate("""
                    () => {
                        const list = [];
                        const nodes = document.querySelectorAll('#J_goodsList ul.gl-warp li.gl-item, .gl-item');
                        nodes.forEach(node => {
                            try {
                                const skuId = node.getAttribute('data-sku') || '';
                                const titleEl = node.querySelector('.p-name em, .p-name a');
                                const title = titleEl ? titleEl.innerText.replace(/\\s+/g, ' ').trim() : '';
                                
                                const priceEl = node.querySelector('.p-price i, .p-price strong');
                                const price = priceEl ? parseFloat(priceEl.innerText.replace(/[^0-9.]/g, '')) : 0.0;
                                
                                const shopEl = node.querySelector('.p-shop a, .curr-shop, [class*="shop"]');
                                const shop = shopEl ? shopEl.innerText.trim() : '';
                                
                                const commitEl = node.querySelector('.p-commit a, [class*="commit"]');
                                const comments = commitEl ? commitEl.innerText.trim() : '';
                                
                                const iconText = node.querySelector('.p-icons, [class*="icons"]')?.innerText || '';
                                const isSelfRun = iconText.includes('自营') || shop.includes('自营');
                                
                                const imgEl = node.querySelector('.p-img img');
                                const image = imgEl ? (imgEl.src || imgEl.getAttribute('data-lazy-img') || '') : '';

                                if (title && skuId) {
                                    list.push({
                                        sku_id: skuId,
                                        title: title,
                                        price: price,
                                        shop: shop,
                                        comments: comments,
                                        is_self_run: isSelfRun,
                                        link: `https://item.jd.com/${skuId}.html`,
                                        image: image.startsWith('//') ? 'https:' + image : image
                                    });
                                }
                            } catch(e) {}
                        });
                        return list;
                    }
                """)

                self._close_browser(browser, ctx)
                return {
                    "success": True,
                    "keyword": keyword,
                    "total_found": len(results),
                    "results": results[:limit],
                }
            except Exception as e:
                self._close_browser(browser, ctx)
                return {"success": False, "error": str(e)}

    def check_stock(self, sku_id_or_url: str, area_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Check real-time regional stock/inventory status for a given SKU.
        Automatically detects user default delivery area (e.g. 上海浦东张江) or accepts custom areaId.
        """
        sku_match = re.search(r"(\d{6,16})", str(sku_id_or_url))
        if not sku_match:
            return {"success": False, "message": "无效的商品 SKU 或链接，未能提取到纯数字商品 ID"}

        sku_id = sku_match.group(1)
        detected_area, detected_name = self.get_user_area()
        target_area = area_id or detected_area or self.DEFAULT_AREA_ID
        province_id = target_area.split("-")[0] if "-" in target_area else "2"

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=self.CHROME_ARGS)
            ctx = browser.new_context(
                user_agent=self.MOBILE_UA,
                viewport={"width": 390, "height": 844},
                is_mobile=True,
                has_touch=True,
            )
            ctx.add_cookies([
                {"name": "ipLoc-djd", "value": target_area, "domain": ".jd.com", "path": "/"},
                {"name": "detailedAdd_areaid", "value": target_area, "domain": ".jd.com", "path": "/"},
                {"name": "areaId", "value": province_id, "domain": ".jd.com", "path": "/"},
            ])
            page = ctx.new_page()
            try:
                page.goto(f"https://item.m.jd.com/product/{sku_id}.html", wait_until="domcontentloaded", timeout=12000)
                page.wait_for_timeout(2500)

                title = page.title().split("【")[0].strip()
                body = page.locator("body").inner_text()
                lines = [l.strip() for l in body.splitlines() if l.strip()]

                has_stock = False
                stock_status = "未知"

                if "无货" in body:
                    has_stock = False
                    stock_status = "无货"
                elif "到货通知" in body:
                    has_stock = False
                    stock_status = "到货通知 (无货)"
                elif "不支持销售" in body or "所选区域不支持销售" in body:
                    has_stock = False
                    stock_status = "该区域不支持销售"
                elif "商品已下架" in body or "暂无报价" in body:
                    has_stock = False
                    stock_status = "商品已下架"
                elif any(kw in body for kw in ["现货", "最快明天送达", "最快今天送达", "次日达", "立即购买", "加入购物车"]):
                    has_stock = True
                    for kw in ["现货", "最快明天送达", "次日达", "有货"]:
                        if kw in body:
                            stock_status = kw
                            break
                    if stock_status == "未知":
                        stock_status = "现货有货"
                elif "采购中" in body:
                    has_stock = False
                    stock_status = "采购中 (暂无现货)"
                else:
                    has_stock = True
                    stock_status = "正常在售"

                price = "暂无标价"
                for l in lines[:25]:
                    if "¥" in l and any(c.isdigit() for c in l):
                        price = l.replace("¥", "").replace("起", "").strip()
                        break

                browser.close()
                return {
                    "success": True,
                    "sku_id": sku_id,
                    "title": title,
                    "has_stock": has_stock,
                    "stock_status": stock_status,
                    "price": price,
                    "area_id": target_area,
                    "area_name": detected_name or "当前默认收货区域",
                    "product_url": f"https://item.jd.com/{sku_id}.html",
                }
            except Exception as e:
                browser.close()
                return {"success": False, "error": str(e), "sku_id": sku_id}

    def add_to_cart(self, sku_id_or_url: str, count: int = 1, verify_stock: bool = True) -> Dict[str, Any]:
        """
        Add a product to the user's shopping cart with regional stock verification.
        """
        sku_match = re.search(r"(\d{6,16})", str(sku_id_or_url))
        if not sku_match:
            return {"success": False, "message": "无效的商品 SKU 或链接，未能提取到纯数字商品 ID"}

        sku_id = sku_match.group(1)

        stock_info = None
        if verify_stock:
            stock_info = self.check_stock(sku_id)
            if stock_info.get("success") and not stock_info.get("has_stock"):
                return {
                    "success": False,
                    "message": (
                        f"⚠️ 加购拦截：商品【{stock_info.get('title', sku_id)}】在您的收货区域 "
                        f"({stock_info.get('area_name', '当前收货地址')}) 处于【{stock_info.get('stock_status')}】状态，暂无现货！已为您阻止盲目加购。"
                    ),
                    "stock_info": stock_info,
                    "sku_id": sku_id,
                }

        self.touch_keepalive(force=False)

        with sync_playwright() as p:
            browser, ctx = self._create_browser_and_context(p, headless=True)
            page = ctx.new_page()
            try:
                gate_url = f"https://cart.jd.com/gate.action?pid={sku_id}&pcount={count}&ptype=1"
                page.goto(gate_url, timeout=25000)
                page.wait_for_timeout(3000)

                result_url = page.url
                result_text = page.locator("body").inner_text()

                is_success = "成功加入购物车" in result_text or "addToCart" in result_url

                if is_success:
                    self._sync_state(ctx)
                    self._close_browser(browser, ctx)
                    res_msg = f"🎉 商品 (SKU: {sku_id}) 已成功加入购物车！数量: {count}"
                    if stock_info and stock_info.get("title"):
                        res_msg = f"🎉【{stock_info.get('title')}】(SKU: {sku_id}) 已成功加入购物车！数量: {count}，当前区域库存: {stock_info.get('stock_status')}"
                    return {
                        "success": True,
                        "message": res_msg,
                        "sku_id": sku_id,
                        "title": stock_info.get("title") if stock_info else "",
                        "stock_status": stock_info.get("stock_status") if stock_info else "已验证",
                        "product_url": f"https://item.jd.com/{sku_id}.html",
                        "cart_url": "https://cart.jd.com/cart_index",
                    }
                else:
                    self._close_browser(browser, ctx)
                    return {
                        "success": False,
                        "message": f"未能确认加购结果，当前页面提示: {result_text[:100].strip()}",
                    }
            except Exception as e:
                self._close_browser(browser, ctx)
                return {"success": False, "error": str(e)}

    def get_orders(self, page_num: int = 1) -> Dict[str, Any]:
        """
        Retrieve recent historical purchase orders.
        Extracts from both modern React (#orderLite) and standard tables with SSO bounce cushion.
        """
        self.touch_keepalive(force=False)

        with sync_playwright() as p:
            browser, ctx = self._create_browser_and_context(p, headless=True)
            page = ctx.new_page()
            try:
                order_url = f"https://order.jd.com/center/list.action?d={page_num}&s=4096"
                page.goto(order_url, timeout=25000)

                # Allow up to 4 seconds for SSO bounce
                for _ in range(8):
                    page.wait_for_timeout(500)
                    if "order.jd.com" in page.url:
                        break

                if "passport.jd.com" in page.url or "login.aspx" in page.url:
                    self._close_browser(browser, ctx)
                    return {"success": False, "message": "登录态已过期，请先重新扫码登录: python cli.py login"}

                orders = page.evaluate("""
                    () => {
                        const list = [];
                        const modernCards = document.querySelectorAll('[class*="orderCard-"]');
                        if (modernCards.length > 0) {
                            modernCards.forEach(card => {
                                try {
                                    const text = card.innerText.replace(/\\s+/g, ' ');
                                    const idMatch = text.match(/订单号\\s*(\\d+)/);
                                    const orderId = idMatch ? idMatch[1] : '';
                                    
                                    const dateMatch = text.match(/(\\d{4}-\\d{2}-\\d{2}\\s+\\d{2}:\\d{2}:\\d{2})/);
                                    const dealTime = dateMatch ? dateMatch[1] : '';
                                    
                                    const priceMatch = text.match(/¥\\s*([0-9.]+)/);
                                    const amount = priceMatch ? parseFloat(priceMatch[1]) : 0.0;
                                    
                                    let status = '已完成';
                                    if (text.includes('待付款')) status = '待付款';
                                    else if (text.includes('待收货')) status = '待收货';
                                    else if (text.includes('已取消')) status = '已取消';
                                    else if (text.includes('已完成')) status = '已完成';
                                    else if (text.includes('运输中') || text.includes('配送中')) status = '配送中';
                                    
                                    let consignee = '';
                                    const addrMatch = text.match(/(尚尚[^\\s]+|[\\u4e00-\\u9fa5]{2,4}[\\u4e00-\\u9fa50-9-号楼室]+)/);
                                    if (addrMatch) consignee = addrMatch[1];
                                    
                                    const products = [];
                                    const prodLinks = card.querySelectorAll('a[title], .p-name a, [class*="goods"] a, a[href*="item.jd.com"]');
                                    prodLinks.forEach(a => {
                                        const pTitle = (a.getAttribute('title') || a.innerText).trim();
                                        if (pTitle && !pTitle.includes('订单详情') && !pTitle.includes('发票') && !pTitle.includes('评价')) {
                                            products.push({
                                                title: pTitle,
                                                link: a.href
                                            });
                                        }
                                    });
                                    
                                    if (products.length === 0) {
                                        const itemMatches = [...text.matchAll(/([^\\s¥]+(?:人份|盒|袋|瓶|包|件|台|支)?)\\s+共\\s*(\\d+)\\s*件/g)];
                                        itemMatches.forEach(m => {
                                            products.push({
                                                title: m[1].replace(/^(在线支付|已完成|先享后付)/, '').trim(),
                                                count: m[2]
                                            });
                                        });
                                    }

                                    const shopMatch = text.match(/订单号\\s*\\d+\\s+([\\u4e00-\\u9fa5a-zA-Z0-9]+)/);
                                    const shop = shopMatch ? shopMatch[1].trim() : '';

                                    let deliveryMsg = '';
                                    const delivMatch = text.match(/(订单已送达[^\\s]*|已签收[^\\s]*|派送中[^\\s]*)/);
                                    if (delivMatch) deliveryMsg = delivMatch[1];

                                    if (orderId) {
                                        list.push({
                                            order_id: orderId,
                                            deal_time: dealTime,
                                            shop: shop,
                                            status: status,
                                            delivery_msg: deliveryMsg,
                                            amount: amount,
                                            consignee: consignee,
                                            products: products
                                        });
                                    }
                                } catch (e) {}
                            });
                        }
                        
                        if (list.length === 0) {
                            const tbNodes = document.querySelectorAll('.order-tb, tbody[id^="tb-"]');
                            tbNodes.forEach(node => {
                                try {
                                    const timeEl = node.querySelector('.dealtime');
                                    const dealTime = timeEl ? timeEl.innerText.trim() : '';
                                    
                                    const idEl = node.querySelector('.number a, [class*="number"] a');
                                    const orderId = idEl ? idEl.innerText.trim() : '';
                                    
                                    const amountEl = node.querySelector('.amount strong, .amount');
                                    const amount = amountEl ? amountEl.innerText.replace(/[^0-9.]/g, '').trim() : '';
                                    
                                    const statusEl = node.querySelector('.order-status');
                                    const status = statusEl ? statusEl.innerText.trim() : '';
                                    
                                    const consigneeEl = node.querySelector('.consignee .txt, .consignee');
                                    const consignee = consigneeEl ? consigneeEl.innerText.trim() : '';

                                    const products = [];
                                    const prodNodes = node.querySelectorAll('.goods-item, .p-name');
                                    prodNodes.forEach(pn => {
                                        const a = pn.querySelector('a') || pn;
                                        const title = a ? a.innerText.replace(/\\s+/g, ' ').trim() : '';
                                        if (title) products.push({ title: title, link: a.href });
                                    });

                                    if (orderId) {
                                        list.push({
                                            order_id: orderId,
                                            deal_time: dealTime,
                                            status: status,
                                            amount: amount ? parseFloat(amount) : 0.0,
                                            consignee: consignee,
                                            products: products
                                        });
                                    }
                                } catch (e) {}
                            });
                        }

                        return list;
                    }
                """)

                self._sync_state(ctx)
                self._close_browser(browser, ctx)
                return {
                    "success": True,
                    "page": page_num,
                    "total_orders": len(orders),
                    "orders": orders,
                }
            except Exception as e:
                self._close_browser(browser, ctx)
                return {"success": False, "error": str(e)}

    def get_order_track(self, order_id: str) -> Dict[str, Any]:
        """
        Retrieve real-time delivery and logistics tracking for a specific order.
        """
        self.touch_keepalive(force=False)

        with sync_playwright() as p:
            browser, ctx = self._create_browser_and_context(p, headless=True)
            page = ctx.new_page()
            try:
                track_url = f"https://details.jd.com/normal/item.action?orderid={order_id}"
                page.goto(track_url, timeout=25000)
                page.wait_for_timeout(3000)

                if "passport.jd.com" in page.url:
                    self._close_browser(browser, ctx)
                    return {"success": False, "message": "登录态已过期，请重新登录"}

                track_info = page.evaluate("""
                    () => {
                        const tracks = [];
                        const nodes = document.querySelectorAll('.track-list li, .logistics-box li, .delivery-step, [class*="track"] li');
                        nodes.forEach(n => {
                            const text = n.innerText.replace(/\\s+/g, ' ').trim();
                            if (text) tracks.push(text);
                        });
                        
                        const statusNode = document.querySelector('.state-top, .order-state, [class*="state-title"]');
                        const status = statusNode ? statusNode.innerText.trim() : '';
                        
                        return {
                            status: status,
                            trajectory: tracks
                        };
                    }
                """)

                self._close_browser(browser, ctx)
                return {
                    "success": True,
                    "order_id": order_id,
                    "status": track_info.get("status"),
                    "trajectory": track_info.get("trajectory", []),
                }
            except Exception as e:
                self._close_browser(browser, ctx)
                return {"success": False, "error": str(e)}
