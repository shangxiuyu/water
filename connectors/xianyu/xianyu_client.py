#!/usr/bin/env python3
"""
Xianyu (闲鱼 / goofish.com) AI Agent Client.

Architecture aligned with dingdong-ai / jd-ai / hema-ai:
1. Fast HTTP MTOP Client (appKey=34839810, accountSite=xianyu) for lightweight public/authed calls
   such as hot search shade words and session status verification.
2. Persistent `ego-browser` Automation Engine (strictly runs in isolated background TaskSpaces,
   never launches external Google Chrome) for:
   - Interactive QR / SMS login with automatic cookie capture & persistence (`data/xianyu_state.json` + `.env`)
   - Homepage recommendation feed (`mtop.taobao.idlehome.home.webpc.feed`)
   - Keyword search (`mtop.taobao.idlemtopsearch.pc.search` + DOM fallback)
   - Item detail & seller credit inspection (`mtop.taobao.idle.pc.detail` + DOM fallback)
   - Personal bought orders & published listings
"""

from __future__ import annotations

import hashlib
import http.cookiejar
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional


class XianyuClient:
    """Client for interacting with Xianyu (goofish.com) via MTOP HTTP & ego-browser."""

    GATEWAY = "https://h5api.m.goofish.com"
    APPKEY = "34839810"
    ACCOUNT_SITE = "xianyu"
    BASE_URL = "https://www.goofish.com"

    DESKTOP_UA = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    )

    DATA_DIR = Path(__file__).parent / "data"
    STATE_FILE = DATA_DIR / "xianyu_state.json"
    USER_INFO_FILE = DATA_DIR / "user_info.json"
    ENV_FILE = Path(__file__).parent / ".env"

    JSON_MARKER = "__XIANYU_JSON__"

    # Indicators in cookie dictionary proving a logged-in Alibaba/Xianyu user session
    AUTH_COOKIE_KEYS = ("unb", "cookie2", "sgcookie", "_nk_", "tracknick", "lgc")

    def __init__(self, cookie: Optional[str] = None):
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.cookie_dict: Dict[str, str] = {}
        self.cookie: str = ""
        self._load_cookies(explicit_cookie=cookie)

    # -------------------------------------------------------------------------
    # Cookie & Session Persistence Helpers
    # -------------------------------------------------------------------------

    def _load_cookies(self, explicit_cookie: Optional[str] = None) -> None:
        raw = (explicit_cookie or os.getenv("XIANYU_COOKIE", "")).strip()

        if not raw and self.ENV_FILE.is_file():
            try:
                for line in self.ENV_FILE.read_text(encoding="utf-8").splitlines():
                    if line.startswith("XIANYU_COOKIE="):
                        raw = line.split("=", 1)[1].strip().strip("\"'")
                        break
            except Exception:
                pass

        if raw:
            self._set_cookie_string(raw)
            return

        if self.STATE_FILE.is_file():
            try:
                state = json.loads(self.STATE_FILE.read_text(encoding="utf-8"))
                cookies = state.get("cookies", [])
                for c in cookies:
                    name = c.get("name", "").strip()
                    val = c.get("value", "").strip()
                    if name:
                        self.cookie_dict[name] = val
                self.cookie = "; ".join(f"{k}={v}" for k, v in self.cookie_dict.items())
            except Exception:
                pass

    def _set_cookie_string(self, raw: str) -> None:
        self.cookie_dict = {}
        for part in raw.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                self.cookie_dict[k.strip()] = v.strip()
        self.cookie = "; ".join(f"{k}={v}" for k, v in self.cookie_dict.items())

    def _persist_cookies(self, cookies: List[Dict[str, Any]], user_info: Optional[Dict[str, Any]] = None) -> None:
        state_payload = {
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "cookies": cookies,
        }
        self.STATE_FILE.write_text(json.dumps(state_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        cookie_pairs: Dict[str, str] = {}
        for c in cookies:
            name = str(c.get("name", "")).strip()
            val = str(c.get("value", "")).strip()
            if name:
                cookie_pairs[name] = val

        if cookie_pairs:
            self.cookie_dict = cookie_pairs
            self.cookie = "; ".join(f"{k}={v}" for k, v in cookie_pairs.items())
            self.ENV_FILE.write_text(f"XIANYU_COOKIE={self.cookie}\n", encoding="utf-8")

        if user_info is not None:
            self.USER_INFO_FILE.write_text(json.dumps(user_info, ensure_ascii=False, indent=2), encoding="utf-8")

    def _has_auth_cookie(self) -> bool:
        return "cookie2" in self.cookie_dict and any(
            k in self.cookie_dict for k in ("unb", "_nk_", "tracknick", "sgcookie", "lgc")
        )

    @staticmethod
    def extract_item_id(item_id_or_url: str) -> str:
        text = str(item_id_or_url).strip()
        if text.isdigit():
            return text
        m = re.search(r"[?&]id=(\d+)", text)
        if m:
            return m.group(1)
        m = re.search(r"(\d{8,})", text)
        return m.group(1) if m else text

    # -------------------------------------------------------------------------
    # Fast HTTP MTOP Client
    # -------------------------------------------------------------------------

    def _mtop_request(self, api: str, data: Dict[str, Any], ver: str = "1.0", timeout: int = 10) -> Dict[str, Any]:
        """Execute a lightweight MTOP POST request with _m_h5_tk MD5 signing."""
        cj = http.cookiejar.CookieJar()
        for k, v in self.cookie_dict.items():
            cj.set_cookie(
                http.cookiejar.Cookie(
                    version=0, name=k, value=v, port=None, port_specified=False,
                    domain=".goofish.com", domain_specified=True, domain_initial_dot=True,
                    path="/", path_specified=True, secure=True, expires=None,
                    discard=True, comment=None, comment_url=None, rest={},
                )
            )
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        last_resp: Dict[str, Any] = {}

        for _ in range(2):
            token = ""
            for c in cj:
                if c.name == "_m_h5_tk":
                    token = c.value.split("_", 1)[0]
                    break
            t = str(int(time.time() * 1000))
            sign = hashlib.md5(f"{token}&{t}&{self.APPKEY}&{body}".encode("utf-8")).hexdigest()
            qs = urllib.parse.urlencode({
                "jsv": "2.7.2",
                "appKey": self.APPKEY,
                "t": t,
                "sign": sign,
                "api": api,
                "v": ver,
                "type": "originaljson",
                "accountSite": self.ACCOUNT_SITE,
                "dataType": "json",
            })
            url = f"{self.GATEWAY}/h5/{api}/{ver}/?{qs}"
            req = urllib.request.Request(
                url,
                data=urllib.parse.urlencode({"data": body}).encode("utf-8"),
                headers={
                    "User-Agent": self.DESKTOP_UA,
                    "Referer": f"{self.BASE_URL}/",
                    "Origin": self.BASE_URL,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
            )
            try:
                with opener.open(req, timeout=timeout) as resp:
                    last_resp = json.loads(resp.read().decode("utf-8"))
            except Exception as exc:
                last_resp = {"ret": [f"HTTP_EXCEPTION::{exc}"]}
                return last_resp

            for c in cj:
                self.cookie_dict[c.name] = c.value
            self.cookie = "; ".join(f"{k}={v}" for k, v in self.cookie_dict.items())

            ret_str = " ".join(last_resp.get("ret") or [])
            if "TOKEN" in ret_str.upper():
                continue
            return last_resp

        return last_resp

    # -------------------------------------------------------------------------
    # ego-browser Subprocess Execution Engine
    # -------------------------------------------------------------------------

    def _run_ego_script(self, js_body_template: str, timeout: int = 45) -> Dict[str, Any]:
        """
        Execute an ESM script via `ego-browser nodejs`.
        Uses a temporary output file (`__OUT_FILE__`) to avoid stdout line-wrapping/truncation on large MTOP JSON payloads.
        """
        fd, out_path_str = tempfile.mkstemp(prefix="xianyu_ego_", suffix=".json")
        os.close(fd)
        out_path = Path(out_path_str)
        js_body = js_body_template.replace("__OUT_FILE__", json.dumps(str(out_path)))

        try:
            proc = subprocess.run(
                ["ego-browser", "nodejs"],
                input=js_body,
                text=True,
                capture_output=True,
                timeout=timeout,
            )
            if out_path.is_file() and out_path.stat().st_size > 0:
                raw_payload = out_path.read_text(encoding="utf-8")
                try:
                    return json.loads(raw_payload)
                except Exception as exc:
                    return {"success": False, "error": f"解析 ego-browser 输出文件失败: {exc}"}

            err_msg = (proc.stderr or proc.stdout or "").strip()
            return {
                "success": False,
                "error": err_msg[-600:] if err_msg else f"ego-browser 异常退出 (code={proc.returncode})",
            }
        except FileNotFoundError:
            return {"success": False, "error": "未找到 ego-browser CLI，请确认已安装 ego-browser。"}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"ego-browser 执行超时（>{timeout}s）。"}
        except Exception as exc:
            return {"success": False, "error": f"调用 ego-browser 失败: {exc}"}
        finally:
            try:
                out_path.unlink(missing_ok=True)
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # Login, Status Check & Keepalive
    # -------------------------------------------------------------------------

    def interactive_login(self, timeout_seconds: int = 180) -> Dict[str, Any]:
        """
        Open goofish.com in an ego-browser TaskSpace for user QR-code or SMS login.
        Polls cookies until authenticated, persists state & cookies, and closes TaskSpace.
        """
        print("\n🐟 正在通过 ego-browser 打开闲鱼登录页面...")
        print("💡 请在弹出的 ego-browser 窗口中使用【闲鱼 APP 扫码】或【短信验证码】完成登录。")
        print("   登录成功后将自动提取并持久化保存会话 Cookie 与用户信息，随后关闭任务窗口。\n")

        deadline_ms = int(timeout_seconds * 1000)
        script = f"""
const fs = await import("node:fs/promises");
const task = await taskSpace("xianyu-login");
const page = task.page("p1");
try {{
  await page.cdp("Network.enable");
  await page.goto("https://www.goofish.com/", {{ waitUntil: "domcontentloaded" }});
  await page.waitForTimeout(2500);

  // If not logged in yet, click the login button if login dialog isn't already open
  await page.evaluate(() => {{
    const hasDialog = document.querySelector('iframe[src*="mini_login"], iframe[src*="passport.goofish.com"]');
    if (!hasDialog) {{
      const els = [...document.querySelectorAll('a, button, div, span')];
      const loginBtn = els.find(el => el.textContent && el.textContent.trim() === '登录');
      if (loginBtn) loginBtn.click();
    }}
  }});

  const start = Date.now();
  let authedCookies = null;
  let userNav = null;

  while (Date.now() - start < {deadline_ms}) {{
    const res = await page.cdp("Network.getCookies", {{
      urls: ["https://www.goofish.com", "https://h5api.m.goofish.com", "https://passport.goofish.com"]
    }});
    const cookies = res.cookies || [];
    const cmap = Object.fromEntries(cookies.map(c => [c.name, c.value]));
    const hasIdentity = cmap.cookie2 && (cmap.unb || cmap._nk_ || cmap.tracknick || cmap.sgcookie || cmap.lgc);
    if (hasIdentity) {{
      authedCookies = cookies;
      break;
    }}
    await page.waitForTimeout(1500);
  }}

  if (!authedCookies) {{
    await fs.writeFile(__OUT_FILE__, JSON.stringify({{ success: false, message: "登录超时，未检测到有效的闲鱼认证 Cookie。" }}));
  }} else {{
    await page.goto("https://www.goofish.com/", {{ waitUntil: "domcontentloaded" }});
    await page.waitForTimeout(2500);
    const evts = await page.events();
    for (const e of evts) {{
      if (e.method === "Network.responseReceived") {{
        const u = e.params?.response?.url || "";
        if (u.includes("mtop.idle.web.user.page.nav/") || u.includes("mtop.taobao.idlemessage.pc.loginuser.get/")) {{
          try {{
            const b = await page.cdp("Network.getResponseBody", {{ requestId: e.params.requestId }});
            const j = JSON.parse(b.body);
            if ((j.ret || []).some(r => r.startsWith("SUCCESS"))) {{
              userNav = Object.assign(userNav || {{}}, j.data || {{}});
            }}
          }} catch {{}}
        }}
      }}
    }}
    const finalCookies = (await page.cdp("Network.getCookies", {{
      urls: ["https://www.goofish.com", "https://h5api.m.goofish.com", "https://passport.goofish.com"]
    }})).cookies || authedCookies;

    await fs.writeFile(__OUT_FILE__, JSON.stringify({{
      success: true,
      cookies: finalCookies,
      userNav: userNav || {{}}
    }}));
  }}
}} finally {{
  await task.finish({{ keep: [] }});
}}
"""
        res = self._run_ego_script(script, timeout=timeout_seconds + 30)
        if not res.get("success"):
            return {"success": False, "message": res.get("message") or res.get("error") or "登录失败"}

        cookies = res.get("cookies", [])
        user_nav = res.get("userNav", {})
        user_info = self._extract_user_profile(user_nav, cookies)
        self._persist_cookies(cookies, user_info=user_info)
        return {
            "success": True,
            "message": f"🎉 闲鱼登录成功！当前账号: {user_info.get('nickname', '闲鱼用户')}",
            "user": user_info,
        }

    def _extract_user_profile(self, nav_data: Dict[str, Any], cookies: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        cmap = dict(self.cookie_dict)
        if cookies:
            for c in cookies:
                if c.get("name"):
                    cmap[c["name"]] = c.get("value", "")

        base = nav_data.get("baseInfo") if isinstance(nav_data.get("baseInfo"), dict) else nav_data
        nick = (
            (base.get("displayName") if isinstance(base, dict) else None)
            or (base.get("kcUserNick") if isinstance(base, dict) else None)
            or (base.get("userNick") if isinstance(base, dict) else None)
            or (base.get("nick") if isinstance(base, dict) else None)
            or urllib.parse.unquote(cmap.get("_nk_", "") or cmap.get("tracknick", "") or cmap.get("lgc", ""))
            or "闲鱼用户"
        )
        user_id = (
            str(base.get("userId") or base.get("encryptedUserId") or "")
            if isinstance(base, dict) else ""
        ) or cmap.get("unb", "")

        return {
            "logged_in": True,
            "user_id": user_id,
            "nickname": nick,
            "avatar": base.get("avatar", "") if isinstance(base, dict) else "",
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def check_login_status(self, sync_from_browser: bool = True) -> Dict[str, Any]:
        """
        Check whether saved cookies or current ego-browser profile has a valid logged-in Xianyu session.
        """
        # 1. Try fast HTTP MTOP verification if we already have auth cookies
        if self._has_auth_cookie():
            resp = self._mtop_request("mtop.idle.web.user.page.nav", {})
            ret_str = " ".join(resp.get("ret") or [])
            if "SUCCESS" in ret_str:
                profile = self._extract_user_profile(resp.get("data", {}))
                self.USER_INFO_FILE.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
                return {
                    "logged_in": True,
                    "user_id": profile["user_id"],
                    "nickname": profile["nickname"],
                    "avatar": profile.get("avatar", ""),
                    "source": "http_mtop",
                }

        if not sync_from_browser:
            return {
                "logged_in": False,
                "message": "未检测到有效闲鱼登录态，请先执行 `python3 cli.py login` 完成扫码或短信登录。",
            }

        # 2. Check & sync from ego-browser persistent profile
        script = """
const fs = await import("node:fs/promises");
const task = await taskSpace("xianyu-status");
const page = task.page("p1");
try {
  await page.cdp("Network.enable");
  await page.goto("https://www.goofish.com/", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2500);
  const cookies = (await page.cdp("Network.getCookies", {
    urls: ["https://www.goofish.com", "https://h5api.m.goofish.com", "https://passport.goofish.com"]
  })).cookies || [];
  let navData = null;
  let loggedIn = false;
  const evts = await page.events();
  for (const e of evts) {
    if (e.method === "Network.responseReceived") {
      const u = e.params?.response?.url || "";
      if (u.includes("mtop.idle.web.user.page.nav/") || u.includes("mtop.taobao.idlemessage.pc.loginuser.get/")) {
        try {
          const b = await page.cdp("Network.getResponseBody", { requestId: e.params.requestId });
          const j = JSON.parse(b.body);
          if ((j.ret || []).some(r => r.startsWith("SUCCESS"))) {
            loggedIn = true;
            navData = Object.assign(navData || {}, j.data || {});
          }
        } catch {}
      }
    }
  }
  await fs.writeFile(__OUT_FILE__, JSON.stringify({ success: true, loggedIn, cookies, navData: navData || {} }));
} finally {
  await task.finish({ keep: [] });
}
"""
        res = self._run_ego_script(script, timeout=35)
        if res.get("success") and res.get("loggedIn"):
            cookies = res.get("cookies", [])
            profile = self._extract_user_profile(res.get("navData", {}), cookies)
            self._persist_cookies(cookies, user_info=profile)
            return {
                "logged_in": True,
                "user_id": profile["user_id"],
                "nickname": profile["nickname"],
                "avatar": profile.get("avatar", ""),
                "source": "ego_browser",
            }

        return {
            "logged_in": False,
            "message": "当前闲鱼未登录或会话已过期，请执行 `python3 cli.py login` 扫码/短信登录。",
        }

    def keepalive(self) -> Dict[str, Any]:
        """Refresh cookies and session TTL via ego-browser."""
        res = self.check_login_status(sync_from_browser=True)
        if res.get("logged_in"):
            return {
                "success": True,
                "message": f"闲鱼会话刷新成功（账号: {res.get('nickname')}）",
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "user": res,
            }
        return {
            "success": False,
            "message": res.get("message", "当前未登录，无法刷新会话。"),
        }

    # -------------------------------------------------------------------------
    # Public Data: Hot Search Keywords & Homepage Feed
    # -------------------------------------------------------------------------

    def get_hot_keywords(self) -> Dict[str, Any]:
        """Fetch real-time Xianyu hot search / shade keywords (no login required)."""
        resp = self._mtop_request("mtop.taobao.idlemtopsearch.pc.search.shade", {"searchReqFromPage": "xyPchome"})
        ret_str = " ".join(resp.get("ret") or [])
        if "SUCCESS" in ret_str:
            words = [
                w.get("showText") or w.get("realShade")
                for w in (resp.get("data", {}).get("singleShadeWords") or [])
                if w.get("showText") or w.get("realShade")
            ]
            return {
                "success": True,
                "source": "http_mtop",
                "server_time": resp.get("data", {}).get("serverTime", ""),
                "keywords": words,
            }

        # Fallback via ego-browser
        script = """
const fs = await import("node:fs/promises");
const task = await taskSpace("xianyu-hot");
const page = task.page("p1");
try {
  await page.cdp("Network.enable");
  await page.goto("https://www.goofish.com/", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(3000);
  let words = [];
  let serverTime = "";
  for (const e of await page.events()) {
    if (e.method === "Network.responseReceived" && (e.params?.response?.url || "").includes("mtop.taobao.idlemtopsearch.pc.search.shade/")) {
      try {
        const b = await page.cdp("Network.getResponseBody", { requestId: e.params.requestId });
        const j = JSON.parse(b.body);
        serverTime = j.data?.serverTime || "";
        words = (j.data?.singleShadeWords || []).map(w => w.showText || w.realShade).filter(Boolean);
      } catch {}
    }
  }
  await fs.writeFile(__OUT_FILE__, JSON.stringify({ success: words.length > 0, keywords: words, server_time: serverTime }));
} finally {
  await task.finish({ keep: [] });
}
"""
        res = self._run_ego_script(script, timeout=35)
        if res.get("success"):
            res["source"] = "ego_browser"
            return res
        return {"success": False, "error": res.get("error") or f"获取热搜词失败: {ret_str}"}

    @staticmethod
    def _parse_feed_cards(cards: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for card in cards:
            cd = card.get("cardData") or {}
            if not isinstance(cd, dict):
                continue
            dp = cd.get("detailParams") or {}
            item_id = str(cd.get("itemId") or dp.get("itemId") or "")
            title = (cd.get("titleSummary") or {}).get("text") or cd.get("title") or dp.get("title") or ""
            if not item_id or not title:
                continue

            price_info = cd.get("priceInfo") or {}
            price_raw = price_info.get("price") or cd.get("price") or dp.get("soldPrice") or ""
            ori_price = price_info.get("oriPrice") or ""

            tags: List[str] = []
            want_text = (cd.get("hotPoint") or {}).get("text") or ""
            fish_tags = cd.get("fishTags") or {}
            for group_key in ("r2", "r3", "r4"):
                for t_obj in (fish_tags.get(group_key) or {}).get("tagList") or []:
                    content = (t_obj.get("data") or {}).get("content")
                    if content:
                        if "想要" in content:
                            if not want_text:
                                want_text = content
                        elif content not in tags:
                            tags.append(content)

            user_obj = cd.get("user") or {}
            img_url = (cd.get("mainPicInfo") or {}).get("url") or (cd.get("image") or {}).get("url") or dp.get("picUrl") or ""
            if img_url.startswith("http://"):
                img_url = "https://" + img_url[7:]

            items.append({
                "item_id": item_id,
                "title": str(title).strip(),
                "price": str(price_raw).strip(),
                "ori_price": str(ori_price).strip() if ori_price and ori_price != "0.00" else "",
                "want_count": want_text,
                "city": cd.get("city") or cd.get("area") or "",
                "seller_nick": user_obj.get("userNick") or dp.get("userNick") or "",
                "tags": tags,
                "image_url": img_url,
                "url": f"https://www.goofish.com/item?id={item_id}" if item_id else cd.get("targetUrl", ""),
            })
            if len(items) >= limit:
                break
        return items

    def get_home_feed(self, limit: int = 15) -> Dict[str, Any]:
        """Fetch Xianyu homepage '猜你喜欢' feed items via ego-browser (no login required)."""
        script = f"""
const fs = await import("node:fs/promises");
const task = await taskSpace("xianyu-feed");
const page = task.page("p1");
try {{
  await page.cdp("Network.enable");
  await page.goto("https://www.goofish.com/", {{ waitUntil: "domcontentloaded" }});
  await page.waitForTimeout(3500);
  if ({limit} > 18) {{
    await page.mouse.wheel(0, 1400, {{ label: "scroll feed items" }});
    await page.waitForTimeout(2000);
  }}

  const cards = [];
  for (const e of await page.events()) {{
    if (e.method === "Network.responseReceived" && (e.params?.response?.url || "").includes("mtop.taobao.idlehome.home.webpc.feed/")) {{
      try {{
        const b = await page.cdp("Network.getResponseBody", {{ requestId: e.params.requestId }});
        const j = JSON.parse(b.body);
        if (Array.isArray(j.data?.cardList)) cards.push(...j.data.cardList);
      }} catch {{}}
    }}
  }}

  const domItems = await page.evaluate((maxN) => {{
    const out = [];
    const seen = new Set();
    for (const a of document.querySelectorAll('a[href*="/item?id="]')) {{
      const m = a.href.match(/id=(\\d+)/);
      const id = m ? m[1] : "";
      if (!id || seen.has(id)) continue;
      seen.add(id);
      const lines = (a.innerText || "").split("\\n").map(s => s.trim()).filter(Boolean);
      const img = a.querySelector("img")?.src || "";
      out.push({{ item_id: id, lines, img, url: a.href }});
      if (out.length >= maxN) break;
    }}
    return out;
  }}, {limit});

  await fs.writeFile(__OUT_FILE__, JSON.stringify({{ success: true, cards, domItems }}));
}} finally {{
  await task.finish({{ keep: [] }});
}}
"""
        res = self._run_ego_script(script, timeout=45)
        if not res.get("success"):
            return {"success": False, "error": res.get("error", "获取首页推荐流失败"), "items": []}

        items = self._parse_feed_cards(res.get("cards", []), limit=limit)
        if not items and res.get("domItems"):
            items = self._parse_dom_cards(res["domItems"], limit=limit)

        return {
            "success": True,
            "total_found": len(items),
            "items": items[:limit],
        }

    @staticmethod
    def _parse_dom_cards(dom_items: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
        parsed: List[Dict[str, Any]] = []
        for d in dom_items[:limit]:
            lines = d.get("lines") or []
            price = ""
            want = ""
            title = ""
            seller = ""
            for i, line in enumerate(lines):
                if line == "¥" and i + 1 < len(lines) and not price:
                    price = lines[i + 1]
                    if i + 2 < len(lines) and lines[i + 2].startswith("."):
                        price += lines[i + 2]
                elif line.startswith("¥") and not price:
                    price = line.lstrip("¥").strip()
                elif "人想要" in line and not want:
                    want = line
                elif not title and len(line) >= 4 and line not in ("全新", "包邮"):
                    title = line
            if lines and not seller:
                last = lines[-1]
                if "信用" not in last and "想要" not in last and not last.startswith("¥"):
                    seller = last
            parsed.append({
                "item_id": d.get("item_id", ""),
                "title": title or "闲鱼商品",
                "price": price,
                "ori_price": "",
                "want_count": want,
                "city": "",
                "seller_nick": seller,
                "tags": [],
                "image_url": d.get("img", ""),
                "url": d.get("url", ""),
            })
        return parsed

    # -------------------------------------------------------------------------
    # Keyword Search (Requires Logged-in Session)
    # -------------------------------------------------------------------------

    @staticmethod
    def _parse_search_cards(result_list: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for entry in result_list:
            data = entry.get("data") or entry
            item = data.get("item") or data
            main = item.get("main") or {}
            ex = main.get("exContent") or item.get("exContent") or main
            click_args = ((main.get("clickParam") or {}).get("args")) or {}

            detail_params = ex.get("detailParams") or {}
            item_id = str(
                ex.get("itemId")
                or detail_params.get("itemId")
                or click_args.get("item_id")
                or click_args.get("id")
                or ""
            )
            title = ex.get("title") or detail_params.get("title") or ""
            if not item_id and not title:
                continue

            # Price can be nested under price array or string field
            price_val = ""
            price_field = ex.get("price")
            if isinstance(price_field, list):
                price_val = "".join(str(p.get("text", "")) for p in price_field if isinstance(p, dict)).lstrip("¥￥")
            elif price_field is not None:
                price_val = str(price_field).lstrip("¥￥")
            if not price_val:
                price_val = str(detail_params.get("soldPrice") or click_args.get("price") or "")

            want_num = str(click_args.get("wantNum") or "").strip()
            want_text = f"{want_num}人想要" if want_num and want_num != "0" else ""
            tag_alias = {"freeShippingIcon": "包邮", "nfrIcon": "", "level7": "鱼小铺Lv7", "level6": "鱼小铺Lv6"}
            tags: List[str] = []
            for tag_obj in (ex.get("fishTags") or {}).values():
                if isinstance(tag_obj, dict):
                    for t_item in tag_obj.get("tagList") or []:
                        c = (t_item.get("data") or {}).get("content")
                        if not c:
                            continue
                        if "想要" in c:
                            if not want_text:
                                want_text = c
                            continue
                        mapped = tag_alias.get(c, c)
                        if mapped and mapped not in tags:
                            tags.append(mapped)

            img_url = ex.get("picUrl") or detail_params.get("picUrl") or ""
            if img_url.startswith("http://"):
                img_url = "https://" + img_url[7:]

            items.append({
                "item_id": item_id,
                "title": str(title).strip(),
                "price": price_val.strip(),
                "want_count": want_text,
                "city": ex.get("area") or click_args.get("area") or "",
                "seller_nick": ex.get("userNickName") or detail_params.get("userNick") or "",
                "tags": tags,
                "image_url": img_url,
                "url": f"https://www.goofish.com/item?id={item_id}" if item_id else "",
            })
            if len(items) >= limit:
                break
        return items

    def search_goods(self, keyword: str, limit: int = 15, sort: str = "") -> Dict[str, Any]:
        """
        Search Xianyu items by keyword via ego-browser.
        Note: Xianyu enforces login + anti-bot (Baxia) on search; returns `requires_login=True` if unauthenticated.
        :param sort: '', 'price_asc', 'price_desc', or 'new'
        """
        kw = keyword.strip()
        if not kw:
            return {"success": False, "error": "搜索关键词不能为空。", "items": []}

        encoded_kw = urllib.parse.quote(kw)
        search_url = f"https://www.goofish.com/search?q={encoded_kw}"
        sort_click_label = {
            "new": "新发布",
            "price_asc": "价格",
            "price_desc": "价格",
        }.get(sort, "")

        script = f"""
const fs = await import("node:fs/promises");
const task = await taskSpace("xianyu-search");
const page = task.page("p1");
try {{
  await page.cdp("Network.enable");
  await page.goto({json.dumps(search_url)}, {{ waitUntil: "domcontentloaded" }});
  await page.waitForTimeout(4000);

  // Optional sort tab click when authenticated
  const sortLabel = {json.dumps(sort_click_label)};
  if (sortLabel) {{
    await page.evaluate((lbl) => {{
      const els = [...document.querySelectorAll('div, span, a, button')];
      const target = els.find(el => el.children.length === 0 && el.textContent?.trim() === lbl);
      if (target) target.click();
    }}, sortLabel);
    await page.waitForTimeout(2500);
  }}

  let requiresLogin = false;
  let baxiaMsg = "";
  let searchResultList = [];

  for (const e of await page.events()) {{
    if (e.method === "Network.responseReceived") {{
      const u = e.params?.response?.url || "";
      if (u.includes("mtop.taobao.idlemtopsearch.pc.search/")) {{
        try {{
          const b = await page.cdp("Network.getResponseBody", {{ requestId: e.params.requestId }});
          const j = JSON.parse(b.body);
          const retStr = (j.ret || []).join(" ");
          if (retStr.includes("RGV587_ERROR") || retStr.includes("FAIL_SYS_SESSION_EXPIRED") || j.data?.url?.includes("login")) {{
            requiresLogin = true;
            baxiaMsg = retStr || "需要登录验证";
          }} else if (Array.isArray(j.data?.resultList)) {{
            searchResultList = j.data.resultList;
          }}
        }} catch {{}}
      }}
    }}
  }}

  const domItems = await page.evaluate((maxN) => {{
    const out = [];
    const seen = new Set();
    for (const a of document.querySelectorAll('a[href*="/item?id="]')) {{
      const m = a.href.match(/id=(\\d+)/);
      const id = m ? m[1] : "";
      if (!id || seen.has(id)) continue;
      seen.add(id);
      const lines = (a.innerText || "").split("\\n").map(s => s.trim()).filter(Boolean);
      const img = a.querySelector("img")?.src || "";
      out.push({{ item_id: id, lines, img, url: a.href }});
      if (out.length >= maxN) break;
    }}
    return out;
  }}, {limit});

  await fs.writeFile(__OUT_FILE__, JSON.stringify({{
    success: true,
    requiresLogin,
    baxiaMsg,
    searchResultList,
    domItems
  }}));
}} finally {{
  await task.finish({{ keep: [] }});
}}
"""
        res = self._run_ego_script(script, timeout=45)
        if not res.get("success"):
            return {"success": False, "error": res.get("error", "搜索执行失败"), "items": []}

        items = self._parse_search_cards(res.get("searchResultList", []), limit=limit)
        if not items and res.get("domItems"):
            items = self._parse_dom_cards(res["domItems"], limit=limit)

        if res.get("requiresLogin") and not items:
            return {
                "success": False,
                "requires_login": True,
                "message": "闲鱼关键词搜索需要登录后才可使用（触发风控验证）。请先执行 `python3 cli.py login` 扫码或短信登录。",
                "detail": res.get("baxiaMsg", ""),
                "items": [],
            }

        return {
            "success": True,
            "keyword": kw,
            "total_found": len(items),
            "items": items[:limit],
        }

    # -------------------------------------------------------------------------
    # Item Detail & Seller Profile
    # -------------------------------------------------------------------------

    def get_item_detail(self, item_id_or_url: str) -> Dict[str, Any]:
        """
        Fetch full Xianyu item details, seller info, and related recommendations.
        Combines MTOP detail API interception with resilient DOM extraction.
        """
        item_id = self.extract_item_id(item_id_or_url)
        if not item_id:
            return {"success": False, "error": "无法识别有效的闲鱼商品 ID。"}

        item_url = f"https://www.goofish.com/item?id={item_id}"
        script = f"""
const fs = await import("node:fs/promises");
const task = await taskSpace("xianyu-detail");
const page = task.page("p1");
try {{
  await page.cdp("Network.enable");
  await page.goto({json.dumps(item_url)}, {{ waitUntil: "domcontentloaded" }});
  await page.waitForTimeout(3500);

  let detailData = null;
  let recCards = [];
  let requiresVerify = false;

  for (const e of await page.events()) {{
    if (e.method === "Network.responseReceived") {{
      const u = e.params?.response?.url || "";
      if (u.includes("mtop.taobao.idle.pc.detail/")) {{
        try {{
          const b = await page.cdp("Network.getResponseBody", {{ requestId: e.params.requestId }});
          const j = JSON.parse(b.body);
          if (j.data?.itemDO) {{
            detailData = j.data;
          }} else if (j.data?.url || (j.ret || []).some(r => r.includes("RGV587"))) {{
            requiresVerify = true;
          }}
        }} catch {{}}
      }}
      if (u.includes("mtop.taobao.idle.item.web.recommend.list/")) {{
        try {{
          const b = await page.cdp("Network.getResponseBody", {{ requestId: e.params.requestId }});
          const j = JSON.parse(b.body);
          if (Array.isArray(j.data?.cardList)) recCards = j.data.cardList;
        }} catch {{}}
      }}
    }}
  }}

  const domSnap = await page.evaluate(() => {{
    const text = document.body?.innerText || "";
    const imgs = [...document.querySelectorAll('img')]
      .map(img => img.src)
      .filter(s => s && (s.includes('alicdn.com') || s.includes('fleamarket')) && !s.includes('tps-'));
    return {{
      title: document.title || "",
      text: text.slice(0, 4000),
      images: [...new Set(imgs)].slice(0, 12),
    }};
  }});

  await fs.writeFile(__OUT_FILE__, JSON.stringify({{
    success: true,
    itemId: {json.dumps(item_id)},
    itemUrl: {json.dumps(item_url)},
    detailData,
    recCards,
    requiresVerify,
    domSnap
  }}));
}} finally {{
  await task.finish({{ keep: [] }});
}}
"""
        res = self._run_ego_script(script, timeout=45)
        if not res.get("success"):
            return {"success": False, "error": res.get("error", "获取商品详情失败")}

        detail_data = res.get("detailData")
        if isinstance(detail_data, dict) and detail_data.get("itemDO"):
            parsed = self._format_mtop_detail(item_id, item_url, detail_data)
        else:
            parsed = self._format_dom_detail(item_id, item_url, res.get("domSnap", {}), res.get("requiresVerify", False))

        rec_items = self._parse_feed_cards(res.get("recCards", []), limit=6)
        parsed["recommendations"] = rec_items
        return parsed

    @staticmethod
    def _format_mtop_detail(item_id: str, item_url: str, data: Dict[str, Any]) -> Dict[str, Any]:
        item_do = data.get("itemDO") or {}
        seller_do = data.get("sellerDO") or {}
        images = [
            (img.get("url") or "").replace("http://", "https://")
            for img in (item_do.get("imageInfos") or [])
            if isinstance(img, dict) and img.get("url")
        ]
        labels = [
            t.get("text")
            for t in (item_do.get("commonTags") or [])
            if isinstance(t, dict) and t.get("text")
        ]
        ori_price = str(item_do.get("originalPrice") or "").strip()
        if ori_price in ("0", "0.0", "0.00"):
            ori_price = ""
        return {
            "success": True,
            "source": "mtop_detail",
            "item_id": str(item_do.get("itemId") or item_id),
            "title": item_do.get("title") or "",
            "price": str(item_do.get("soldPrice") or ""),
            "original_price": ori_price,
            "description": item_do.get("desc") or "",
            "want_count": item_do.get("wantCnt") or 0,
            "browse_count": item_do.get("browseCnt") or 0,
            "condition": item_do.get("condition") or "",
            "tags": labels,
            "images": images,
            "seller": {
                "nick": seller_do.get("nick") or "",
                "city": seller_do.get("city") or item_do.get("city") or "",
                "credit": (seller_do.get("zhimaLevelInfo") or {}).get("levelName") or "",
                "sold_count": seller_do.get("hasSoldNumInteger") or seller_do.get("itemCount") or "",
                "good_rate": seller_do.get("goodRate") or "",
                "last_visit": seller_do.get("lastVisitTime") or "",
            },
            "url": item_url,
        }

    @staticmethod
    def _format_dom_detail(item_id: str, item_url: str, dom_snap: Dict[str, Any], requires_verify: bool) -> Dict[str, Any]:
        raw_text = dom_snap.get("text") or ""
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]

        # If page showed a pure login wall with no item content
        if requires_verify and "立即购买" not in raw_text and "聊一聊" not in raw_text:
            return {
                "success": False,
                "requires_login": True,
                "item_id": item_id,
                "url": item_url,
                "message": "该商品详情触发了闲鱼防刷验证，请先执行 `python3 cli.py login` 登录后再查看。",
            }

        price = ""
        want_count = ""
        browse_count = ""
        seller_nick = ""
        city = ""
        title_desc_lines: List[str] = []

        for idx, line in enumerate(lines):
            if line == "¥" and idx + 1 < len(lines) and not price:
                price = lines[idx + 1]
            elif line.startswith("¥") and not price:
                price = line.lstrip("¥").strip()
            elif line.endswith("人想要") and not want_count:
                want_count = line
            elif line.endswith("浏览") and not browse_count:
                browse_count = line
            elif line == "举报" and idx >= 2 and not seller_nick:
                # Seller metadata block sits just before "举报"
                for back in range(max(0, idx - 6), idx):
                    cand = lines[back]
                    if cand in ("网页版发闲置功能又升级啦！", "搜索", "闲鱼号", "担保交易"):
                        continue
                    if not seller_nick and len(cand) <= 30 and "卖出" not in cand and "好评" not in cand and "来闲鱼" not in cand and "来过" not in cand:
                        seller_nick = cand
                    elif not city and len(cand) <= 10 and "卖出" not in cand and "好评" not in cand and "来" not in cand:
                        city = cand

        # Extract main description between browse/tags and "聊一聊" / "品牌"
        start_idx = 0
        end_idx = len(lines)
        for idx, line in enumerate(lines):
            if line.endswith("浏览") or line in ("可自提", "包邮"):
                start_idx = idx + 1
            if line in ("聊一聊", "立即购买", "为你推荐"):
                end_idx = idx
                break
        if start_idx < end_idx:
            title_desc_lines = [l for l in lines[start_idx:end_idx] if l not in ("：", "品", "牌", "成", "色")]

        description = "\n".join(title_desc_lines).strip()
        title = title_desc_lines[0] if title_desc_lines else dom_snap.get("title", "").replace(" - 闲鱼", "").strip()

        return {
            "success": True,
            "source": "dom_fallback",
            "item_id": item_id,
            "title": title,
            "price": price,
            "original_price": "",
            "description": description,
            "want_count": want_count,
            "browse_count": browse_count,
            "condition": "",
            "tags": [t for t in ("包邮", "可自提", "担保交易") if t in lines],
            "images": dom_snap.get("images", []),
            "seller": {
                "nick": seller_nick,
                "city": city,
                "credit": "",
                "sold_count": next((l for l in lines if "卖出" in l), ""),
                "good_rate": next((l for l in lines if "好评率" in l), ""),
                "last_visit": next((l for l in lines if "前来过" in l), ""),
            },
            "url": item_url,
        }

    # -------------------------------------------------------------------------
    # Personal Account Data: Bought Orders & Published Listings (Requires Login)
    # -------------------------------------------------------------------------

    def _fetch_personal_page(self, target_url: str, page_kind: str, limit: int = 10) -> Dict[str, Any]:
        script = f"""
const fs = await import("node:fs/promises");
const task = await taskSpace("xianyu-personal");
const page = task.page("p1");
try {{
  await page.cdp("Network.enable");
  await page.goto({json.dumps(target_url)}, {{ waitUntil: "domcontentloaded" }});
  await page.waitForTimeout(3500);

  let sessionExpired = false;
  const apiPayloads = [];
  for (const e of await page.events()) {{
    if (e.method === "Network.responseReceived") {{
      const u = e.params?.response?.url || "";
      if (u.includes("h5api.m.goofish.com/h5/")) {{
        try {{
          const b = await page.cdp("Network.getResponseBody", {{ requestId: e.params.requestId }});
          const j = JSON.parse(b.body);
          const retStr = (j.ret || []).join(" ");
          if (retStr.includes("SESSION_EXPIRED")) sessionExpired = true;
          if (retStr.includes("SUCCESS") && (u.includes("order") || u.includes("bought") || u.includes("publish") || u.includes("item.list"))) {{
            apiPayloads.push({{ api: u.split("/h5/")[1]?.split("/")[0], data: j.data }});
          }}
        }} catch {{}}
      }}
    }}
  }}

  const domInfo = await page.evaluate(() => ({{
    url: location.href,
    hasLoginIframe: !!document.querySelector('iframe[src*="mini_login"], iframe[src*="passport.goofish.com"]'),
    text: (document.body?.innerText || "").slice(0, 5000),
    links: [...document.querySelectorAll('a[href*="/item?id="], a[href*="order"]')].slice(0, 30).map(a => ({{
      href: a.href,
      text: (a.innerText || "").trim()
    }}))
  }}));

  await fs.writeFile(__OUT_FILE__, JSON.stringify({{
    success: true,
    sessionExpired,
    apiPayloads,
    domInfo
  }}));
}} finally {{
  await task.finish({{ keep: [] }});
}}
"""
        res = self._run_ego_script(script, timeout=45)
        if not res.get("success"):
            return {"success": False, "error": res.get("error", f"获取{page_kind}失败")}

        dom_info = res.get("domInfo") or {}
        if res.get("sessionExpired") or dom_info.get("hasLoginIframe") or "login" in (dom_info.get("url") or "").lower():
            return {
                "success": False,
                "requires_login": True,
                "message": f"查看{page_kind}需要先登录闲鱼账号，请执行 `python3 cli.py login`。",
            }

        return {
            "success": True,
            "page_kind": page_kind,
            "url": dom_info.get("url", target_url),
            "api_payloads": res.get("apiPayloads", []),
            "items": (dom_info.get("links") or [])[:limit],
            "raw_text_preview": (dom_info.get("text") or "")[:1500],
        }

    @staticmethod
    def _parse_bought_text(raw_text: str, limit: int) -> List[Dict[str, Any]]:
        lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
        status_set = {"交易成功", "待付款", "待发货", "待收货", "待评价", "退款中", "交易关闭", "已取消"}
        action_set = {"更多", "联系卖家", "去评价", "再次购买", "查看评价", "物流记录", "删除订单", "确认收货", "提醒发货", "申请售后"}

        start_idx = 0
        for i, line in enumerate(lines):
            if line == "退款中":
                start_idx = i + 1
        end_idx = len(lines)
        for i in range(start_idx, len(lines)):
            if lines[i] in ("发闲置", "阿里巴巴集团"):
                end_idx = i
                break

        orders: List[Dict[str, Any]] = []
        cur: Optional[Dict[str, Any]] = None
        i = start_idx
        while i < end_idx:
            line = lines[i]
            if line in action_set:
                i += 1
                continue
            if i + 1 < end_idx and lines[i + 1] in status_set:
                if cur:
                    orders.append(cur)
                cur = {
                    "seller": line,
                    "status": lines[i + 1],
                    "title": "",
                    "spec": "",
                    "price": "",
                }
                i += 2
                continue
            if cur is not None:
                if line.startswith("¥") or line.startswith("￥"):
                    cur["price"] = line.lstrip("¥￥").strip()
                elif not cur["title"]:
                    cur["title"] = line
                elif not cur["spec"] and not line.startswith("¥"):
                    cur["spec"] = line
            i += 1
        if cur:
            orders.append(cur)
        return orders[:limit]

    @staticmethod
    def _parse_published_text(raw_text: str, links: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
        lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
        start_idx = 0
        for i, line in enumerate(lines):
            if line == "信用及评价":
                start_idx = i + 2 if i + 1 < len(lines) and lines[i + 1].isdigit() else i + 1
        end_idx = len(lines)
        for i in range(start_idx, len(lines)):
            if lines[i] in ("发闲置", "阿里巴巴集团"):
                end_idx = i
                break

        link_by_title: Dict[str, str] = {}
        for lk in links:
            href = lk.get("href", "")
            txt = lk.get("text", "")
            if "/item?id=" in href and txt:
                link_by_title[txt.splitlines()[0].strip()] = href

        items: List[Dict[str, Any]] = []
        i = start_idx
        while i < end_idx:
            if lines[i] == "¥" and i >= 1 and i + 1 < end_idx:
                title = lines[i - 1]
                price = lines[i + 1]
                seller = lines[i + 2] if i + 2 < end_idx and lines[i + 2] != "发闲置" else ""
                url = link_by_title.get(title, "")
                item_id = XianyuClient.extract_item_id(url) if url else ""
                items.append({
                    "item_id": item_id,
                    "title": title,
                    "price": price,
                    "seller_nick": seller,
                    "url": url,
                })
                i += 3
                continue
            i += 1
        return items[:limit]

    def get_orders(self, limit: int = 10) -> Dict[str, Any]:
        """Fetch user's bought orders from https://www.goofish.com/bought."""
        res = self._fetch_personal_page("https://www.goofish.com/bought", "已买到的宝贝订单", limit=limit)
        if res.get("success"):
            res["orders"] = self._parse_bought_text(res.get("raw_text_preview", ""), limit=limit)
        return res

    def get_my_published(self, limit: int = 10) -> Dict[str, Any]:
        """Fetch user's published items from https://www.goofish.com/personal."""
        res = self._fetch_personal_page("https://www.goofish.com/personal", "个人主页/在售宝贝", limit=limit)
        if res.get("success"):
            res["published_items"] = self._parse_published_text(
                res.get("raw_text_preview", ""), res.get("items", []), limit=limit
            )
        return res

    # -------------------------------------------------------------------------
    # Publish Idle Item (Safe Preview / HandOff / Explicit Submit)
    # -------------------------------------------------------------------------

    SHIPPING_LABELS = {
        "free": "包邮",
        "distance": "按距离计费",
        "fixed": "一口价",
        "no_ship": "无需邮寄",
    }

    def publish_item(
        self,
        description: str,
        price: float,
        image_paths: List[str],
        original_price: Optional[float] = None,
        shipping_mode: str = "free",
        shipping_fee: Optional[float] = None,
        self_pickup: bool = False,
        submit: bool = False,
        hand_off: bool = False,
    ) -> Dict[str, Any]:
        """
        Fill and optionally publish a Xianyu idle listing on https://www.goofish.com/publish.
        Safety defaults:
        - `submit=False, hand_off=False`: Fills the form, verifies upload/location/attributes, returns preview, closes space.
        - `submit=False, hand_off=True`: Fills the form and hands the ego-browser window to the user for final click.
        - `submit=True`: Explicitly clicks the '发布' button after filling all required fields.
        """
        desc = (description or "").strip()
        if not desc:
            return {"success": False, "error": "宝贝描述不能为空。"}
        if len(desc) > 1500:
            return {"success": False, "error": f"宝贝描述超过 1500 字上限（当前 {len(desc)} 字）。"}
        if price <= 0:
            return {"success": False, "error": "售价必须大于 0。"}
        if shipping_mode not in self.SHIPPING_LABELS:
            return {"success": False, "error": f"不支持的发货方式: {shipping_mode}，可选 {list(self.SHIPPING_LABELS.keys())}"}

        abs_images: List[str] = []
        for p_str in image_paths or []:
            p = Path(p_str).expanduser().resolve()
            if not p.is_file():
                return {"success": False, "error": f"图片文件不存在: {p_str}"}
            abs_images.append(str(p))
        if not abs_images:
            return {"success": False, "error": "发布闲置至少需要提供 1 张本地商品图片。"}

        shipping_label = self.SHIPPING_LABELS[shipping_mode]
        price_str = f"{float(price):.2f}"
        orig_str = f"{float(original_price):.2f}" if original_price and original_price > 0 else ""
        ship_fee_str = f"{float(shipping_fee):.2f}" if shipping_mode == "fixed" and shipping_fee is not None else ""

        space_name = f"xianyu-publish-{int(time.time())}"
        script = f"""
const fs = await import("node:fs/promises");
const task = await taskSpace({json.dumps(space_name)});
const page = task.page("p1");
let keepForUser = false;
try {{
  await page.cdp("Network.enable");
  await page.goto("https://www.goofish.com/publish", {{ waitUntil: "domcontentloaded" }});
  await page.waitForTimeout(3000);

  const loginGate = await page.evaluate(() => !!document.querySelector('iframe[src*="mini_login"], iframe[src*="passport.goofish.com"]'));
  if (loginGate) {{
    await fs.writeFile(__OUT_FILE__, JSON.stringify({{
      success: false,
      requiresLogin: true,
      message: "发布闲置需要先登录闲鱼账号，请先执行 `python3 cli.py login`。"
    }}));
  }} else {{
    await page.waitForSelector('input[type="file"]', {{ state: "attached", timeout: 10000 }});
    await page.setInputFiles('input[type="file"]', {json.dumps(abs_images)});
    await page.waitForTimeout(3000);

    await page.click('[contenteditable="true"]', {{ label: "focus item description editor" }});
    await page.keyboard.insertText({json.dumps(desc)});
    await page.waitForTimeout(1000);

    await page.fill('input[placeholder="0.00"] >> nth=0', {json.dumps(price_str)});
    if ({json.dumps(orig_str)}) {{
      await page.fill('input[placeholder="0.00"] >> nth=1', {json.dumps(orig_str)});
    }}

    await page.click({json.dumps(f'text="{shipping_label}"')}, {{ label: "select shipping mode" }});
    if ({json.dumps(ship_fee_str)}) {{
      await page.waitForTimeout(500);
      await page.fill('input[placeholder="0.00"] >> nth=2', {json.dumps(ship_fee_str)});
    }}
    if ({json.dumps(self_pickup)}) {{
      await page.click('.ant-switch', {{ label: "toggle self pickup" }});
    }}

    await page.waitForTimeout(2500);

    const previewState = await page.evaluate(() => ({{
      url: location.href,
      text: (document.body?.innerText || "").slice(0, 2000),
      uploadedImgs: document.querySelectorAll('img[src*="alicdn"], img[src^="blob:"], img[src^="data:"]').length,
    }}));

    if ({json.dumps(submit)}) {{
      await page.click('button:has-text("发布")', {{ label: "submit publish item" }});
      await page.waitForTimeout(4000);
      let publishResp = null;
      for (const e of await page.events()) {{
        if (e.method === "Network.responseReceived") {{
          const u = e.params?.response?.url || "";
          if (u.includes("publish") || u.includes("idle.item")) {{
            try {{
              const b = await page.cdp("Network.getResponseBody", {{ requestId: e.params.requestId }});
              publishResp = {{ api: u.split("/h5/")[1]?.split("/")[0] || u, body: b.body.slice(0, 800) }};
            }} catch {{}}
          }}
        }}
      }}
      const afterSubmit = await page.evaluate(() => ({{
        url: location.href,
        text: (document.body?.innerText || "").slice(0, 1000),
      }}));
      await fs.writeFile(__OUT_FILE__, JSON.stringify({{
        success: true,
        submitted: true,
        previewState,
        afterSubmit,
        publishResp
      }}));
    }} else if ({json.dumps(hand_off)}) {{
      keepForUser = true;
      await task.handOff();
      await fs.writeFile(__OUT_FILE__, JSON.stringify({{
        success: true,
        submitted: false,
        handedOff: true,
        spaceId: task.spaceId,
        previewState
      }}));
    }} else {{
      await fs.writeFile(__OUT_FILE__, JSON.stringify({{
        success: true,
        submitted: false,
        handedOff: false,
        previewState
      }}));
    }}
  }}
}} finally {{
  if (!keepForUser) {{
    await task.finish({{ keep: [] }});
  }}
}}
"""
        res = self._run_ego_script(script, timeout=60)
        if not res.get("success"):
            if res.get("requiresLogin"):
                return {"success": False, "requires_login": True, "message": res.get("message")}
            return {"success": False, "error": res.get("message") or res.get("error") or "发布流程执行失败"}

        return {
            "success": True,
            "submitted": bool(res.get("submitted")),
            "handed_off": bool(res.get("handedOff")),
            "space_id": res.get("spaceId"),
            "description": desc,
            "price": price_str,
            "original_price": orig_str,
            "shipping_mode": shipping_label,
            "self_pickup": self_pickup,
            "image_count": len(abs_images),
            "preview_text": (res.get("previewState") or {}).get("text", ""),
            "after_submit": res.get("afterSubmit"),
            "publish_resp": res.get("publishResp"),
        }
