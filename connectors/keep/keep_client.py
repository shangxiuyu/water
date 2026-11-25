#!/usr/bin/env python3
"""
Keep Client SDK for AI / LLM Integration.
Provides programmatic access to Keep (自由运动场 - gotokeep.com) APIs:
1. User Authentication (Mobile + Password or Bearer Token)
2. Personal Fitness Dashboard & Cumulative Statistics (Total days, minutes, calories, distance)
3. Workout Records & Activity Logs (Running, cycling, hiking, walking, fitness)
4. Deep Workout Detail: Average pace, splits, cadence, heart rate curve & zones
5. AES-128-CBC + Zlib GPS Trajectory Decryption & WGS-84 coordinate transformation
6. GPX Trajectory Export
7. Official Badges & Virtual/Physical Medals
8. Course & Workout Global Search
9. LLM-friendly Markdown formatting
"""

from __future__ import annotations

import base64
import json
import math
import os
import time
import xml.etree.ElementTree as ET
import zlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from xml.dom import minidom

import requests

# AES decryption is only imported lazily when GPS track decryption is explicitly called
AES = None


# Constants
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
SESSION_FILE = DATA_DIR / "session.json"
GPX_DIR = DATA_DIR / "gpx"

# Keep AES Secret Keys for GPS point decryption
AES_KEY_B64 = "NTZmZTU5OzgyZzpkODczYw=="
AES_IV_B64 = "MjM0Njg5MjQzMjkyMDMwMA=="

# Keep API Endpoints
API_BASE = "https://api.gotokeep.com"
LOGIN_API = f"{API_BASE}/v1.1/users/login"
DASHBOARD_USER_API = f"{API_BASE}/v1.1/home/dashboard/user"
DASHBOARD_STATS_API = f"{API_BASE}/v1.1/home/dashboard/statistics"
DASHBOARD_PWDATA_API = f"{API_BASE}/v2/home/dashboard/pwData"
USER_PEOPLE_API = f"{API_BASE}/v2/people/{{user_id}}"
ACHIEVEMENTS_API = f"{API_BASE}/v1.1/home/achievements"
WORKOUT_LIST_API = f"{API_BASE}/pd/v3/stats/detail"
WORKOUT_DETAIL_API = f"{API_BASE}/pd/v3/{{sport_type}}log/{{run_id}}"
SEARCH_GLOBAL_API = f"{API_BASE}/search/v3/global"
COURSE_DISCOVER_API = f"{API_BASE}/training/v2/course/discover"
RANKING_LIST_API = f"{API_BASE}/social/v2/rankinglist/brief"
BODY_DATA_API = f"{API_BASE}/v1.1/bodyData"


QRCODE_CREATE_API = f"{API_BASE}/account/v2/qrcode"
QRCODE_CHECK_API = f"{API_BASE}/account/v2/qrcode/login_check"


# GCJ-02 to WGS-84 Coordinate Conversion Helpers
def _transform_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lon(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 20.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def gcj02_to_wgs84(lat: float, lon: float) -> Tuple[float, float]:
    """Convert GCJ-02 (China Mars coordinate) to standard WGS-84 GPS coordinate."""
    a = 6378245.0
    ee = 0.00669342162296594323
    dlat = _transform_lat(lon - 105.0, lat - 35.0)
    dlon = _transform_lon(lon - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - ee * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sqrtmagic) * math.pi)
    dlon = (dlon * 180.0) / (a / sqrtmagic * math.cos(radlat) * math.pi)
    mglat = lat + dlat
    mglon = lon + dlon
    return lat * 2 - mglat, lon * 2 - mglon


def decode_keep_stream_data(encoded_text: str, is_geo: bool = False) -> Any:
    """
    Decrypt and decompress Keep serialized stream data (GPS points or heart rate stream).
    If is_geo=True, AES-128-CBC decrypts before zlib decompress.
    """
    raw_bytes = base64.b64decode(encoded_text)
    if is_geo:
        key = base64.b64decode(AES_KEY_B64)
        iv = base64.b64decode(AES_IV_B64)
        try:
            from Crypto.Cipher import AES as _PyCryptoAES  # type: ignore

            cipher = _PyCryptoAES.new(key, _PyCryptoAES.MODE_CBC, iv)
            raw_bytes = cipher.decrypt(raw_bytes)
        except Exception:
            # Fallback to cryptography package
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

            cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
            decryptor = cipher.decryptor()
            raw_bytes = decryptor.update(raw_bytes) + decryptor.finalize()

    decompressed = zlib.decompress(raw_bytes, 16 + zlib.MAX_WBITS)
    return json.loads(decompressed)


@dataclass
class KeepUser:
    """User profile data model."""
    user_id: str
    username: str
    avatar: str = ""
    gender: str = ""
    bio: str = ""
    city: str = ""
    member: bool = False
    duration_level: int = 0
    running_level: int = 0
    masked_mobile: str = ""
    height: Optional[float] = None
    weight: Optional[float] = None
    birthday: str = ""
    registration_date: str = ""
    preferences: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class KeepSportsSummary:
    """Cumulative personal fitness summary."""
    total_days: int = 0
    total_minutes: int = 0
    total_calories: int = 0
    total_workout_count: int = 0
    total_entries: int = 0
    running_distance_km: float = 0.0
    cycling_distance_km: float = 0.0
    walking_distance_km: float = 0.0
    levels: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class KeepWorkoutItem:
    """Single workout record summary."""
    workout_id: str
    sport_type: str
    start_time: str
    duration_seconds: int
    distance_meters: float
    calories: int
    avg_pace: Optional[str] = None
    avg_heart_rate: Optional[int] = None
    title: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class KeepClient:
    """Official Keep API Client for Python & AI integration."""

    def __init__(self, token: Optional[str] = None, user_id: Optional[str] = None):
        self.session = requests.Session()
        self.token = token or os.environ.get("KEEP_TOKEN")
        self.user_id = user_id or os.environ.get("KEEP_USER_ID")
        self.username = ""
        self.mobile = ""

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        GPX_DIR.mkdir(parents=True, exist_ok=True)

        if not self.token and SESSION_FILE.exists():
            self._load_session()

    @property
    def is_authenticated(self) -> bool:
        return bool(self.token)

    def _get_headers(self, content_type: str = "application/json") -> Dict[str, str]:
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Keep/7.40.0",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        if content_type:
            headers["Content-Type"] = content_type
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _save_session(self, data: Dict[str, Any]) -> None:
        """Persist session token and user info to data/session.json."""
        self.token = data.get("token")
        self.user_id = data.get("userId") or data.get("user_id") or self.user_id
        self.username = data.get("username", self.username)
        self.mobile = data.get("mobile", self.mobile)

        payload = {
            "token": self.token,
            "user_id": self.user_id,
            "username": self.username,
            "mobile": self.mobile,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _load_session(self) -> None:
        """Load session from disk if valid."""
        try:
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.token = data.get("token")
                self.user_id = data.get("user_id")
                self.username = data.get("username", "")
                self.mobile = data.get("mobile", "")
        except Exception:
            pass

    def clear_session(self) -> None:
        """Clear local session."""
        self.token = None
        self.user_id = None
        self.username = ""
        self.mobile = ""
        if SESSION_FILE.exists():
            SESSION_FILE.unlink()

    # -------------------------------------------------------------------------
    # Authentication & QR Code
    # -------------------------------------------------------------------------
    def create_qrcode(self) -> Dict[str, Any]:
        """
        Request an official login QR code session from Keep backend.
        Returns dict with qrcode_id, image_url, text_qr (ASCII for terminal), and redirect_url.
        """
        resp = self.session.get(QRCODE_CREATE_API, timeout=10)
        res_json = resp.json()
        if not resp.ok or not res_json.get("ok"):
            raise RuntimeError(f"获取 Keep 登录二维码失败: {res_json.get('text', '接口异常')}")

        data = res_json.get("data", {})
        text_qr = ""
        if data.get("qrcodeTextUrl"):
            try:
                t_resp = self.session.get(data["qrcodeTextUrl"], timeout=5)
                if t_resp.ok:
                    text_qr = t_resp.text
            except Exception:
                pass

        return {
            "qrcode_id": data.get("qrcodeId"),
            "image_url": data.get("qrcodeUrl"),
            "text_qr": text_qr,
            "redirect_url": data.get("redirectUrl"),
        }

    def check_qrcode(self, qrcode_id: str) -> Dict[str, Any]:
        """
        Poll login check status for a specific qrcodeId.
        When user scans & authorizes in Keep App, returns ok: True with session token.
        """
        url = f"{QRCODE_CHECK_API}?qrcodeId={qrcode_id}"
        resp = self.session.get(url, timeout=10)
        try:
            return resp.json()
        except Exception:
            return {"ok": False, "data": None}

    def login_by_qrcode_cli(self, timeout_seconds: int = 180) -> Dict[str, Any]:
        """
        Pure CLI QR Code Login: prints ASCII QR code directly into the terminal,
        polls status until confirmed or timed out.
        """
        qr_info = self.create_qrcode()
        qid = qr_info["qrcode_id"]
        text_qr = qr_info["text_qr"]

        print("\n" + "=" * 50)
        print("  📱 Keep 官方扫码登录 (手机 Keep App 扫一扫)")
        print("=" * 50)
        print("💡 请打开手机【Keep App】 -> 点击右上角【扫一扫】扫描下方二维码：\n")

        if text_qr:
            print(text_qr)
        else:
            print(f"👉 二维码图片链接: {qr_info['image_url']}")

        print("\n⏳ 正在等待手机扫码并点击【允许登录】...")
        start_t = time.time()

        while time.time() - start_t < timeout_seconds:
            res = self.check_qrcode(qid)
            if res.get("ok"):
                data = res.get("data") or {}
                token = data.get("token") or (data.get("user") or {}).get("token") if isinstance(data, dict) else None
                if not token and isinstance(data, str):
                    token = data
                if token:
                    session = self.set_token(token)
                    print(f"\n🎉 扫码登录成功！欢迎回来，{session.get('username') or 'Keep 运动家'}！")
                    return session
            time.sleep(1.5)

        raise TimeoutError("扫码登录超时，请重新运行命令。")

    def login(self, mobile: str, password: str) -> Dict[str, Any]:
        """
        Authenticate with Keep using registered mobile number and password.
        Returns auth payload with JWT token.
        """
        headers = self._get_headers(content_type="application/x-www-form-urlencoded;charset=utf-8")
        data = {
            "mobile": mobile.strip(),
            "password": password.strip(),
        }

        resp = self.session.post(LOGIN_API, headers=headers, data=data, timeout=10)
        res_json = resp.json()

        if not resp.ok or not res_json.get("ok", False):
            err_msg = res_json.get("text") or res_json.get("data") or f"HTTP {resp.status_code}"
            raise ValueError(f"Keep 登录失败: {err_msg}")

        auth_data = res_json.get("data", {})
        token = auth_data.get("token")
        user_info = auth_data.get("user", {})
        user_id = user_info.get("_id") or user_info.get("id") or auth_data.get("userId")

        session_dict = {
            "token": token,
            "userId": user_id,
            "username": user_info.get("username", ""),
            "mobile": mobile,
        }
        self._save_session(session_dict)
        return session_dict

    def interactive_browser_login(self, timeout_seconds: int = 180) -> Dict[str, Any]:
        """
        Pop up an official Keep QR Code Scan & mobile login browser window via Playwright.
        Defaults to Keep App QR Code scanning, with password & token as alternative tabs.
        Upon successful scan or password submission, automatically captures credentials,
        persists to local data/session.json, and closes the browser window.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError("未检测到 playwright 依赖，请在终端执行: pip install playwright")

        # 1. Fetch official QR code session from Keep backend
        qr_info = self.create_qrcode()
        qid = qr_info["qrcode_id"]
        img_url = qr_info["image_url"]
        text_qr = qr_info.get("text_qr", "")

        # Print terminal ASCII QR code as well for multi-channel scanning convenience
        print("\n" + "=" * 54)
        print("  📱 Keep 官方扫码登录 (手机 Keep App 扫一扫)")
        print("=" * 54)
        if text_qr:
            print(text_qr)
        print("💡 屏幕正在弹出高清扫码窗口，你也可以直接扫描上方终端二维码。")
        print("   打开手机【Keep App】 -> 点击右上角【扫一扫】 -> 扫码并点击【允许登录】。\n")

        login_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>Keep AI 扫码登录认证</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background-color: #17181c;
      color: #ffffff;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
      display: flex;
      justify-content: center;
      align-items: center;
      min-height: 100vh;
      padding: 16px;
    }}
    .card {{
      width: 100%;
      max-width: 400px;
      background: #22242a;
      border-radius: 20px;
      padding: 28px 24px;
      box-shadow: 0 16px 40px rgba(0, 0, 0, 0.45);
      border: 1px solid #31353e;
      text-align: center;
    }}
    .brand-icon {{
      width: 52px;
      height: 52px;
      background: linear-gradient(135deg, #24c789 0%, #17a06c 100%);
      border-radius: 14px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      box-shadow: 0 6px 18px rgba(36, 199, 137, 0.35);
      margin-bottom: 10px;
    }}
    .brand-icon svg {{ width: 30px; height: 30px; fill: #ffffff; }}
    .brand-title {{
      font-size: 20px;
      font-weight: 700;
      color: #ffffff;
      letter-spacing: 0.5px;
    }}
    .brand-sub {{
      font-size: 13px;
      color: #8c93a4;
      margin-top: 4px;
      margin-bottom: 20px;
    }}
    .tabs {{
      display: flex;
      background: #191b20;
      border-radius: 10px;
      padding: 3px;
      margin-bottom: 20px;
    }}
    .tab-btn {{
      flex: 1;
      padding: 8px 0;
      text-align: center;
      font-size: 13px;
      border-radius: 8px;
      cursor: pointer;
      color: #8c93a4;
      transition: all 0.2s;
    }}
    .tab-btn.active {{
      background: #2c3039;
      color: #24c789;
      font-weight: 600;
    }}
    .qr-container {{
      background: #ffffff;
      border-radius: 16px;
      padding: 16px;
      display: inline-block;
      margin: 8px 0 16px 0;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3);
    }}
    .qr-container img {{
      width: 190px;
      height: 190px;
      display: block;
      image-rendering: pixelated;
    }}
    .qr-steps {{
      font-size: 13px;
      color: #b3bac6;
      line-height: 1.6;
      margin-bottom: 14px;
      text-align: left;
      background: #191b20;
      padding: 12px 16px;
      border-radius: 10px;
    }}
    .qr-steps span {{
      color: #24c789;
      font-weight: 600;
    }}
    .form-group {{
      margin-bottom: 16px;
      text-align: left;
    }}
    label {{
      display: block;
      font-size: 13px;
      color: #a6adb9;
      margin-bottom: 6px;
    }}
    input, textarea {{
      width: 100%;
      background: #191b20;
      border: 1px solid #333742;
      border-radius: 12px;
      padding: 12px 14px;
      color: #ffffff;
      font-size: 14px;
      outline: none;
      transition: border-color 0.2s;
    }}
    input:focus, textarea:focus {{
      border-color: #24c789;
    }}
    textarea {{
      resize: none;
      height: 90px;
      font-family: monospace;
      font-size: 12px;
    }}
    .status-box {{
      font-size: 13px;
      padding: 10px 12px;
      border-radius: 10px;
      margin-bottom: 16px;
      line-height: 1.4;
    }}
    .status-box.error {{
      background: rgba(239, 68, 68, 0.15);
      border: 1px solid rgba(239, 68, 68, 0.3);
      color: #f87171;
    }}
    .status-box.loading {{
      background: rgba(59, 130, 246, 0.15);
      border: 1px solid rgba(59, 130, 246, 0.3);
      color: #60a5fa;
    }}
    .status-box.success {{
      background: rgba(36, 199, 137, 0.15);
      border: 1px solid rgba(36, 199, 137, 0.3);
      color: #34d399;
    }}
    .submit-btn {{
      width: 100%;
      background: linear-gradient(135deg, #24c789 0%, #17a06c 100%);
      color: #ffffff;
      border: none;
      border-radius: 12px;
      padding: 13px 0;
      font-size: 15px;
      font-weight: 600;
      cursor: pointer;
      box-shadow: 0 6px 18px rgba(36, 199, 137, 0.3);
      transition: opacity 0.2s;
    }}
    .submit-btn:hover {{ opacity: 0.95; }}
    .footer-note {{
      font-size: 11px;
      color: #636b7b;
      text-align: center;
      margin-top: 18px;
      line-height: 1.4;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="brand">
      <div class="brand-icon">
        <svg viewBox="0 0 24 24"><path d="M13.49 5.48c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm-3.6 13.9l1-4.4 2.1 2v6h2v-7.5l-2.1-2 .6-3c1.3 1.5 3.3 2.5 5.5 2.5v-2c-1.9 0-3.5-1-4.3-2.4l-1-1.6c-.4-.6-1-1-1.7-1-.3 0-.5.1-.8.1l-5.2 2.2v4.7h2v-3.4l1.8-.8-1.6 8.1-4.9-.9-.4 2 6.5 1.2z"/></svg>
      </div>
      <div class="brand-title">Keep · 自由运动场</div>
      <div class="brand-sub">AI 个人运动资产与体能分析助手</div>
    </div>

    <div class="tabs">
      <div class="tab-btn active" id="tab-qr" onclick="switchTab('qr')">Keep 扫码登录</div>
      <div class="tab-btn" id="tab-pwd" onclick="switchTab('pwd')">手机密码</div>
      <div class="tab-btn" id="tab-token" onclick="switchTab('token')">Token 凭证</div>
    </div>

    <div id="status" class="status-box loading">
      ⏳ 正在等待手机【Keep App】扫码授权...
    </div>

    <!-- Tab 1: QR Code -->
    <div id="panel-qr">
      <div class="qr-container">
        <img src="{img_url}" alt="Keep 登录二维码" />
      </div>
      <div class="qr-steps">
        1. 打开手机 <span>Keep App</span><br>
        2. 点击首页或搜索栏旁边的 <span>【扫一扫】</span><br>
        3. 扫描上方二维码并在手机上点击 <span>【允许登录】</span>
      </div>
    </div>

    <!-- Tab 2: Password -->
    <div id="panel-pwd" style="display:none;">
      <form onsubmit="handlePwdSubmit(event)">
        <div class="form-group">
          <label for="mobile">Keep 绑定手机号</label>
          <input type="tel" id="mobile" placeholder="例如: 13800000000" maxlength="11" />
        </div>
        <div class="form-group">
          <label for="password">Keep 登录密码</label>
          <input type="password" id="password" placeholder="输入 Keep 密码" />
        </div>
        <button type="submit" class="submit-btn" id="pwd-btn">立即登录并授权</button>
      </form>
    </div>

    <!-- Tab 3: Token -->
    <div id="panel-token" style="display:none;">
      <form onsubmit="handleTokenSubmit(event)">
        <div class="form-group">
          <label for="token">Bearer Token 凭证</label>
          <textarea id="token" placeholder="粘贴从微信/App抓包获取到的 Token (例如: eyJhbGci...)"></textarea>
        </div>
        <button type="submit" class="submit-btn" id="token-btn">验证并保存凭证</button>
      </form>
    </div>

    <div class="footer-note">
      🔒 本地隔离安全会话 · 凭证仅持久化于本地 session.json · 绝不上云
    </div>
  </div>

  <script>
    function switchTab(mode) {{
      const tabs = ['qr', 'pwd', 'token'];
      tabs.forEach(t => {{
        document.getElementById('tab-' + t).classList.toggle('active', t === mode);
        document.getElementById('panel-' + t).style.display = (t === mode ? 'block' : 'none');
      }});
      if (mode === 'qr') {{
        showStatus('⏳ 正在等待手机【Keep App】扫码授权...', 'loading');
      }} else {{
        document.getElementById('status').style.display = 'none';
      }}
    }}

    function showStatus(text, type) {{
      const el = document.getElementById('status');
      el.className = 'status-box ' + type;
      el.innerText = text;
      el.style.display = 'block';
    }}

    async function handlePwdSubmit(e) {{
      e.preventDefault();
      const mobile = document.getElementById('mobile').value.trim();
      const password = document.getElementById('password').value.trim();
      const btn = document.getElementById('pwd-btn');
      if (!mobile || !password) return;

      btn.disabled = true;
      btn.innerText = '正在验证并连接 Keep...';
      showStatus('⏳ 正在向 Keep 官方网关发送登录验证...', 'loading');

      try {{
        const res = await window.py_login_pwd(mobile, password);
        if (res.success) {{
          showStatus('🎉 登录成功！欢迎回来，' + (res.username || 'Keep 运动家') + '！窗口即将自动关闭...', 'success');
          btn.innerText = '✅ 登录成功';
        }} else {{
          showStatus('❌ ' + (res.message || '登录失败，请检查账号密码'), 'error');
          btn.disabled = false;
          btn.innerText = '立即登录并授权';
        }}
      }} catch (err) {{
        showStatus('❌ 通信错误: ' + err, 'error');
        btn.disabled = false;
        btn.innerText = '立即登录并授权';
      }}
    }}

    async function handleTokenSubmit(e) {{
      e.preventDefault();
      const token = document.getElementById('token').value.trim();
      const btn = document.getElementById('token-btn');
      if (!token) return;

      btn.disabled = true;
      btn.innerText = '正在验证凭证...';
      showStatus('⏳ 正在验证 Token 有效性...', 'loading');

      try {{
        const res = await window.py_login_token(token);
        if (res.success) {{
          showStatus('🎉 凭证验证成功！欢迎回来，' + (res.username || 'Keep 运动家') + '！窗口即将自动关闭...', 'success');
          btn.innerText = '✅ 验证成功';
        }} else {{
          showStatus('❌ ' + (res.message || 'Token 无效或已过期'), 'error');
          btn.disabled = false;
          btn.innerText = '验证并保存凭证';
        }}
      }} catch (err) {{
        showStatus('❌ 通信错误: ' + err, 'error');
        btn.disabled = false;
        btn.innerText = '验证并保存凭证';
      }}
    }}
  </script>
</body>
</html>"""

        login_result = {"success": False, "message": "未完成登录"}

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(
                viewport={"width": 430, "height": 690},
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15",
            )
            page = context.new_page()

            def py_login_pwd(mobile: str, pwd: str) -> Dict[str, Any]:
                try:
                    res = self.login(mobile, pwd)
                    user = self.get_user_info()
                    login_result["success"] = True
                    login_result["username"] = user.username
                    login_result["user_id"] = user.user_id
                    login_result["message"] = "登录成功"
                    return {"success": True, "username": user.username, "userId": user.user_id}
                except Exception as e:
                    return {"success": False, "message": str(e)}

            def py_login_token(token_str: str) -> Dict[str, Any]:
                try:
                    res = self.set_token(token_str)
                    login_result["success"] = True
                    login_result["username"] = res.get("username", "")
                    login_result["user_id"] = res.get("userId", "")
                    login_result["message"] = "验证成功"
                    return {"success": True, "username": res.get("username", "")}
                except Exception as e:
                    return {"success": False, "message": str(e)}

            page.expose_function("py_login_pwd", py_login_pwd)
            page.expose_function("py_login_token", py_login_token)

            page.set_content(login_html)

            start_t = time.time()
            last_check_t = 0.0

            while time.time() - start_t < timeout_seconds:
                if login_result["success"]:
                    time.sleep(1.2)
                    break

                try:
                    if page.is_closed():
                        break
                except Exception:
                    break

                # Poll QR code status every 1.5 seconds
                now = time.time()
                if now - last_check_t >= 1.5:
                    last_check_t = now
                    try:
                        chk = self.check_qrcode(qid)
                        if chk.get("ok"):
                            chk_data = chk.get("data") or {}
                            tok = (
                                chk_data.get("token")
                                or (chk_data.get("user") or {}).get("token")
                                if isinstance(chk_data, dict)
                                else None
                            )
                            if not tok and isinstance(chk_data, str):
                                tok = chk_data

                            if tok:
                                session = self.set_token(tok)
                                login_result["success"] = True
                                login_result["username"] = session.get("username", "")
                                login_result["user_id"] = session.get("userId", "")
                                # Update UI banner
                                try:
                                    uname = login_result["username"] or "Keep 运动家"
                                    page.evaluate(
                                        f"showStatus('🎉 扫码授权成功！欢迎回来，{uname}！窗口即将自动关闭...', 'success')"
                                    )
                                except Exception:
                                    pass
                                time.sleep(1.2)
                                break
                    except Exception:
                        pass

                try:
                    page.wait_for_timeout(300)
                except Exception:
                    break

            try:
                browser.close()
            except Exception:
                pass

        if login_result["success"]:
            print(f"🎉 登录成功！欢迎回来，{login_result.get('username') or 'Keep 运动家'}！会话已安全持久化保存。")
        else:
            print("⏱️ 登录超时或窗口被手动关闭。")

        return login_result

    def set_token(self, token: str, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Directly configure bearer token from network capture or environment."""
        token = token.replace("Bearer ", "").strip()
        self.token = token
        if user_id:
            self.user_id = user_id
        # Verify and fetch user info
        user = self.get_user_info()
        session_dict = {
            "token": token,
            "userId": user.user_id,
            "username": user.username,
        }
        self._save_session(session_dict)
        return session_dict

    # -------------------------------------------------------------------------
    # User Profile & Dashboard
    # -------------------------------------------------------------------------
    def get_user_info(self) -> KeepUser:
        """Fetch current authenticated user profile and membership status."""
        if not self.is_authenticated:
            raise PermissionError("Keep 未登录，请先运行 login 命令或配置 KEEP_TOKEN")

        resp = self.session.get(DASHBOARD_USER_API, headers=self._get_headers(), timeout=10)
        res_json = resp.json()
        if not resp.ok or not res_json.get("ok", False):
            raise RuntimeError(f"获取用户信息失败: {res_json.get('text', '未知错误')}")

        data = res_json.get("data", {})
        self.user_id = data.get("id") or data.get("_id") or self.user_id
        self.username = data.get("username", self.username)

        # Update local session with latest username & user_id
        if SESSION_FILE.exists():
            try:
                with open(SESSION_FILE, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                cached["user_id"] = self.user_id
                cached["username"] = self.username
                with open(SESSION_FILE, "w", encoding="utf-8") as f:
                    json.dump(cached, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

        reg_ts = data.get("registrationTime")
        reg_date = ""
        if reg_ts:
            try:
                reg_date = datetime.fromtimestamp(reg_ts / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
            except Exception:
                pass

        pref_map = {
            "bodyweightTraining": "徒手力量训练",
            "yogaPilates": "瑜伽普拉提",
            "pilates": "普拉提",
            "running": "跑步",
            "cycling": "骑行",
        }
        raw_prefs = data.get("preference") or []
        translated_prefs = [pref_map.get(p, p) for p in raw_prefs]

        gender_val = "男" if str(data.get("gender")).upper() == "M" else ("女" if str(data.get("gender")).upper() == "F" else "未知")

        return KeepUser(
            user_id=self.user_id or "",
            username=data.get("username", "Keep运动家"),
            avatar=data.get("avatar", "") or "",
            gender=gender_val,
            bio=data.get("bio", "") or "",
            city=data.get("city", "") or "",
            member=bool(data.get("isMember") or data.get("member") or data.get("memberStatus")),
            duration_level=data.get("durationLevel", 0) or data.get("level", 0),
            running_level=data.get("runningDistanceLevel", 0),
            masked_mobile=data.get("maskedMobile", "") or "",
            height=data.get("height"),
            weight=data.get("weight"),
            birthday=(data.get("birthday") or "")[:10],
            registration_date=reg_date,
            preferences=translated_prefs,
        )

    def get_sports_summary(self) -> KeepSportsSummary:
        """
        Get cumulative sports metrics: total days, minutes, calories, and distances.
        Combines dashboard statistics and pwData endpoints.
        """
        if not self.is_authenticated:
            raise PermissionError("Keep 未登录，请先运行 login 命令或配置 KEEP_TOKEN")

        headers = self._get_headers()
        stats_resp = self.session.get(DASHBOARD_STATS_API, headers=headers, timeout=10)
        stats_data = stats_resp.json().get("data", {}) if stats_resp.ok else {}

        pw_resp = self.session.get(DASHBOARD_PWDATA_API, headers=headers, timeout=10)
        pw_data = pw_resp.json().get("data", {}) if pw_resp.ok else {}

        # Parse metrics (Keep uses totalTrainingDay, totalDuration in seconds)
        total_days = stats_data.get("totalTrainingDay", 0) or stats_data.get("totalDay", 0) or pw_data.get("totalDay", 0)
        dur_sec = stats_data.get("totalDuration", 0)
        total_minutes = stats_data.get("totalMinute", 0) or (dur_sec // 60)
        total_calories = stats_data.get("totalCalorie", 0)
        total_workout_count = stats_data.get("totalTraining", 0) or stats_data.get("totalWorkoutCount", 0)
        total_entries = stats_data.get("totalEntries", 0)

        # Distances breakdown
        running_m = stats_data.get("runningDistance", 0) or pw_data.get("runningDistance", 0)
        cycling_m = stats_data.get("cyclingDistance", 0) or pw_data.get("cyclingDistance", 0)
        walking_m = stats_data.get("walkingDistance", 0) or pw_data.get("walkingDistance", 0)

        levels = {
            "durationLevel": stats_data.get("durationLevel", 0),
            "runningDistanceLevel": stats_data.get("runningDistanceLevel", 0),
        }

        return KeepSportsSummary(
            total_days=total_days,
            total_minutes=total_minutes,
            total_calories=total_calories,
            total_workout_count=total_workout_count,
            total_entries=total_entries,
            running_distance_km=round(running_m / 1000.0, 2),
            cycling_distance_km=round(cycling_m / 1000.0, 2),
            walking_distance_km=round(walking_m / 1000.0, 2),
            levels=levels,
        )

    # -------------------------------------------------------------------------
    # Workout Records & Details
    # -------------------------------------------------------------------------
    def list_workouts(
        self,
        sport_type: str = "running",
        last_date: int = 0,
        limit: int = 15,
    ) -> Dict[str, Any]:
        """
        Query workout records for a specific sport type.
        sport_type options: 'running', 'cycling', 'hiking', 'walking', 'workout'
        Supports cursor pagination via lastTimestamp.
        """
        if not self.is_authenticated:
            raise PermissionError("Keep 未登录，请先运行 login 命令或配置 KEEP_TOKEN")

        params = {
            "dateUnit": "all",
            "type": sport_type,
            "lastDate": last_date,
        }
        resp = self.session.get(WORKOUT_LIST_API, headers=self._get_headers(), params=params, timeout=10)
        res_json = resp.json()

        if not resp.ok or not res_json.get("ok", False):
            raise RuntimeError(f"获取运动记录失败: {res_json.get('text', '接口异常')}")

        data = res_json.get("data", {})
        records = data.get("records", [])
        next_timestamp = data.get("lastTimestamp", 0)

        items: List[KeepWorkoutItem] = []
        for rec in records:
            logs = rec.get("logs", [])
            for item in logs:
                stats = item.get("stats", {})
                if not stats:
                    continue
                w_id = stats.get("id", "")
                dist_m = float(stats.get("distance", 0) or 0)
                dur_s = int(stats.get("duration", 0) or 0)
                cal = int(stats.get("calorie", 0) or 0)
                s_time = stats.get("startTime", "")

                # Average pace formatting
                avg_pace_str = None
                if dist_m > 0 and dur_s > 0:
                    pace_s = dur_s / (dist_m / 1000.0)
                    pace_min = int(pace_s // 60)
                    pace_sec = int(pace_s % 60)
                    avg_pace_str = f"{pace_min}'{pace_sec:02d}\""

                items.append(
                    KeepWorkoutItem(
                        workout_id=w_id,
                        sport_type=stats.get("type") or sport_type,
                        start_time=s_time,
                        duration_seconds=dur_s,
                        distance_meters=dist_m,
                        calories=cal,
                        avg_pace=avg_pace_str,
                        avg_heart_rate=stats.get("averageHeartRate"),
                        title=stats.get("name") or f"{sport_type} 运动",
                    )
                )
                if len(items) >= limit:
                    break
            if len(items) >= limit:
                break

        return {
            "sport_type": sport_type,
            "count": len(items),
            "next_timestamp": next_timestamp,
            "items": [item.to_dict() for item in items],
        }

    def get_workout_detail(self, workout_id: str, sport_type: str = "running") -> Dict[str, Any]:
        """
        Fetch full metrics of a single workout:
        Paces, heart rate breakdown, cadence, elevation gain, and optional GPS points.
        """
        if not self.is_authenticated:
            raise PermissionError("Keep 未登录，请先运行 login 命令或配置 KEEP_TOKEN")

        url = WORKOUT_DETAIL_API.format(sport_type=sport_type, run_id=workout_id)
        resp = self.session.get(url, headers=self._get_headers(), timeout=12)
        res_json = resp.json()

        if not resp.ok or not res_json.get("ok", False):
            raise RuntimeError(f"获取运动详情失败 ({workout_id}): {res_json.get('text', '接口异常')}")

        raw_data = res_json.get("data", {})

        # Basic Stats
        dist_m = float(raw_data.get("distance", 0) or 0)
        dur_s = int(raw_data.get("duration", 0) or 0)
        calories = int(raw_data.get("calorie", 0) or 0)
        start_time = raw_data.get("startTime", "")
        end_time = raw_data.get("endTime", "")

        # Pace & Cadence
        avg_pace_s = (dur_s / (dist_m / 1000.0)) if dist_m > 0 else 0
        avg_pace_min = int(avg_pace_s // 60)
        avg_pace_sec = int(avg_pace_s % 60)
        avg_pace_str = f"{avg_pace_min}'{avg_pace_sec:02d}\"" if avg_pace_s > 0 else "-"

        avg_cadence = raw_data.get("averageStepFrequency") or raw_data.get("avgCadence")
        avg_stride = raw_data.get("averageStepLength")
        elevation_gain = raw_data.get("elevationGain")

        # Heart rate parsing
        hr_info = raw_data.get("heartRate") or {}
        avg_hr = hr_info.get("averageHeartRate")
        max_hr = hr_info.get("maxHeartRate")
        hr_zones = hr_info.get("zones") or []

        # Decrypt Heart Rates stream if available
        hr_stream_b64 = hr_info.get("heartRates")
        hr_stream = []
        if hr_stream_b64:
            try:
                hr_stream = decode_keep_stream_data(hr_stream_b64, is_geo=False)
            except Exception:
                pass

        # Decrypt GPS points if available
        geo_b64 = raw_data.get("geoPoints")
        points = []
        if geo_b64:
            try:
                raw_points = decode_keep_stream_data(geo_b64, is_geo=True)
                for p in raw_points:
                    lat_gcj = p.get("latitude")
                    lon_gcj = p.get("longitude")
                    if lat_gcj and lon_gcj:
                        wgs_lat, wgs_lon = gcj02_to_wgs84(lat_gcj, lon_gcj)
                        points.append({
                            "lat": round(wgs_lat, 6),
                            "lon": round(wgs_lon, 6),
                            "ele": p.get("altitude", 0),
                            "time": p.get("timestamp") or p.get("unixTimestamp"),
                        })
            except Exception:
                pass

        # Kilometers / Split laps
        splits = []
        raw_splits = raw_data.get("splits") or raw_data.get("kilometers") or []
        for idx, sp in enumerate(raw_splits, 1):
            s_dur = sp.get("duration", 0)
            s_min = int(s_dur // 60)
            s_sec = int(s_dur % 60)
            splits.append({
                "km": idx,
                "pace": f"{s_min}'{s_sec:02d}\"",
                "duration_seconds": s_dur,
                "elevation": sp.get("elevationGain", 0),
            })

        return {
            "workout_id": workout_id,
            "sport_type": raw_data.get("type", sport_type),
            "start_time": start_time,
            "end_time": end_time,
            "distance_km": round(dist_m / 1000.0, 2),
            "duration_minutes": round(dur_s / 60.0, 1),
            "duration_seconds": dur_s,
            "calories": calories,
            "avg_pace": avg_pace_str,
            "avg_cadence": avg_cadence,
            "avg_stride_cm": avg_stride,
            "elevation_gain_m": elevation_gain,
            "heart_rate": {
                "average": avg_hr,
                "max": max_hr,
                "zones": hr_zones,
                "stream_points_count": len(hr_stream),
            },
            "gps_points_count": len(points),
            "splits": splits,
            "has_gps": len(points) > 0,
        }

    # -------------------------------------------------------------------------
    # Trajectory Export (GPX)
    # -------------------------------------------------------------------------
    def export_gpx(self, workout_id: str, sport_type: str = "running", output_path: Optional[str] = None) -> str:
        """
        Export workout GPS track into a standard WGS-84 GPX file.
        Includes elevation and heart rate extension if available.
        """
        url = WORKOUT_DETAIL_API.format(sport_type=sport_type, run_id=workout_id)
        resp = self.session.get(url, headers=self._get_headers(), timeout=12)
        res_json = resp.json()

        if not resp.ok or not res_json.get("ok", False):
            raise RuntimeError(f"无法导出 GPX: 获取记录详情失败")

        raw_data = res_json.get("data", {})
        geo_b64 = raw_data.get("geoPoints")
        if not geo_b64:
            raise ValueError(f"该运动记录 ({workout_id}) 无 GPS 轨迹数据（可能为室内运动或未记录轨迹）")

        raw_points = decode_keep_stream_data(geo_b64, is_geo=True)
        if not raw_points:
            raise ValueError("解密后的 GPS 轨迹点为空")

        # Decode HR stream if available
        hr_info = raw_data.get("heartRate") or {}
        hr_stream_b64 = hr_info.get("heartRates")
        hr_map: Dict[int, int] = {}
        if hr_stream_b64:
            try:
                hr_stream = decode_keep_stream_data(hr_stream_b64, is_geo=False)
                for pt in hr_stream:
                    ts = pt.get("timestamp") or pt.get("unixTimestamp")
                    if ts and "heartRate" in pt:
                        hr_map[ts // 1000] = pt["heartRate"]
            except Exception:
                pass

        # Build GPX XML
        gpx = ET.Element(
            "gpx",
            {
                "version": "1.1",
                "creator": "Keep AI Agent (Antigravity)",
                "xmlns": "http://www.topografix.com/GPX/1/1",
                "xmlns:gpxtpx": "http://www.garmin.com/xmlschemas/TrackPointExtension/v1",
            },
        )
        trk = ET.SubElement(gpx, "trk")
        name = ET.SubElement(trk, "name")
        name.text = f"Keep_{sport_type}_{workout_id}"
        trkseg = ET.SubElement(trk, "trkseg")

        for p in raw_points:
            lat_gcj = p.get("latitude")
            lon_gcj = p.get("longitude")
            if not lat_gcj or not lon_gcj:
                continue

            w_lat, w_lon = gcj02_to_wgs84(lat_gcj, lon_gcj)
            trkpt = ET.SubElement(trkseg, "trkpt", {"lat": f"{w_lat:.6f}", "lon": f"{w_lon:.6f}"})

            # Elevation
            alt = p.get("altitude")
            if alt is not None:
                ele = ET.SubElement(trkpt, "ele")
                ele.text = f"{alt:.1f}"

            # Timestamp
            ts = p.get("timestamp") or p.get("unixTimestamp")
            if ts:
                # Keep timestamps in deciseconds or milliseconds
                sec = ts // 1000 if ts > 10_000_000_000 else ts
                dt = datetime.fromtimestamp(sec, tz=timezone.utc)
                time_el = ET.SubElement(trkpt, "time")
                time_el.text = dt.strftime("%Y-%m-%dT%H:%M:%SZ")

                # Heart rate extension
                if sec in hr_map:
                    ext = ET.SubElement(trkpt, "extensions")
                    tpx = ET.SubElement(ext, "gpxtpx:TrackPointExtension")
                    hr_el = ET.SubElement(tpx, "gpxtpx:hr")
                    hr_el.text = str(hr_map[sec])

        # Pretty print XML
        rough_string = ET.tostring(gpx, "utf-8")
        reparsed = minidom.parseString(rough_string)
        pretty_xml = reparsed.toprettyxml(indent="  ")

        if not output_path:
            output_path = str(GPX_DIR / f"keep_{sport_type}_{workout_id}.gpx")

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(pretty_xml)

        return output_path

    # -------------------------------------------------------------------------
    # Badges, Medals & Achievements
    # -------------------------------------------------------------------------
    def get_achievements(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch all earned badges and official Keep physical/online medals."""
        if not self.is_authenticated:
            raise PermissionError("Keep 未登录，请先运行 login 命令或配置 KEEP_TOKEN")

        uid = user_id or self.user_id
        params = {"uid": uid} if uid else {}
        resp = self.session.get(ACHIEVEMENTS_API, headers=self._get_headers(), params=params, timeout=10)
        res_json = resp.json()

        if not resp.ok or not res_json.get("ok", False):
            raise RuntimeError(f"获取勋章成就失败: {res_json.get('text', '接口异常')}")

        data = res_json.get("data", {})
        achievements_list = data.get("achievements", []) or data.get("badges", [])

        results = []
        for item in achievements_list:
            results.append({
                "badge_id": item.get("id"),
                "name": item.get("name") or item.get("title", "成就勋章"),
                "description": item.get("description", ""),
                "icon": item.get("icon") or item.get("imageUrl", ""),
                "achieved": bool(item.get("achieved", True)),
                "achieve_date": item.get("achieveDate") or item.get("date", ""),
            })
        return results

    # -------------------------------------------------------------------------
    # Courses & Workout Global Search
    # -------------------------------------------------------------------------
    def search_courses(self, keyword: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Search Keep fitness courses, workouts, and exercises by keyword.
        Example: '燃脂', '腹肌', '拉伸', '帕梅拉', 'HIIT'
        """
        if not self.is_authenticated:
            raise PermissionError("Keep 未登录，请先运行 login 命令或配置 KEEP_TOKEN")

        params = {
            "keyword": keyword,
            "trainer_gender": "m",
        }
        resp = self.session.get(SEARCH_GLOBAL_API, headers=self._get_headers(), params=params, timeout=10)
        res_json = resp.json()

        if not resp.ok or not res_json.get("ok", False):
            raise RuntimeError(f"搜索课程失败: {res_json.get('text', '接口异常')}")

        data = res_json.get("data", {})
        workout_cards = []

        result_obj = data.get("result", {})
        # Keep uses 'course' and 'exercise' sections with 'entites' key
        courses_list = (result_obj.get("course", {}) or {}).get("entites", [])
        exercises_list = (result_obj.get("exercise", {}) or {}).get("entites", [])

        all_entries = []
        for c in courses_list:
            c["_category"] = "官方课程"
            all_entries.append(c)
        for e in exercises_list:
            e["_category"] = "单个动作"
            all_entries.append(e)

        # Fallback to general sections if result structure varies
        if not all_entries:
            for section in data.get("items", []) or data.get("sections", []):
                sec_type = section.get("type", "课程")
                for item in section.get("items", []):
                    item["_category"] = sec_type
                    all_entries.append(item)

        for item in all_entries:
            w_title = item.get("title") or item.get("name")
            if not w_title:
                continue
            workout_cards.append({
                "id": item.get("id"),
                "title": w_title,
                "sub_title": item.get("desc") or item.get("subTitle", ""),
                "difficulty": item.get("difficulty") or item.get("level") or "K2",
                "duration_minutes": item.get("duration", 0),
                "calories": item.get("calorie", 0),
                "cover": item.get("pic") or item.get("cover") or item.get("picture", ""),
                "category": item.get("_category", "课程"),
                "schema": item.get("schema", ""),
            })
            if len(workout_cards) >= limit:
                break

        return workout_cards

    # -------------------------------------------------------------------------
    # Social Rankings
    # -------------------------------------------------------------------------
    def get_ranking(self, date_str: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get friends' daily workout ranking list.
        date_str format: YYYYMMDD (defaults to today).
        """
        if not self.is_authenticated:
            raise PermissionError("Keep 未登录，请先运行 login 命令或配置 KEEP_TOKEN")

        date_val = date_str or datetime.now().strftime("%Y%m%d")
        params = {"date": date_val}
        resp = self.session.get(RANKING_LIST_API, headers=self._get_headers(), params=params, timeout=10)
        res_json = resp.json()

        if not resp.ok or not res_json.get("ok", False):
            return []

        data = res_json.get("data", {})
        rank_items = data.get("ranks", []) or data.get("rankingList", [])
        results = []
        for r in rank_items:
            u = r.get("user", {})
            results.append({
                "rank": r.get("rank"),
                "username": u.get("username", "运动好友"),
                "score": r.get("score") or r.get("distance", 0),
                "avatar": u.get("avatar", ""),
            })
        return results

    # -------------------------------------------------------------------------
    # Markdown Formatting for LLM & Terminal
    # -------------------------------------------------------------------------
    def format_summary_markdown(self, user: KeepUser, summary: KeepSportsSummary) -> str:
        """Format personal sports summary into a clean, presentation-ready Markdown."""
        body_stats = []
        if user.height:
            body_stats.append(f"**身高**：{user.height} cm")
        if user.weight:
            body_stats.append(f"**体重**：{user.weight} kg")
        if user.height and user.weight:
            bmi = round(user.weight / ((user.height / 100.0) ** 2), 1)
            body_stats.append(f"**BMI**：{bmi}")
        if user.gender and user.gender != "未知":
            body_stats.append(f"**性别**：{user.gender}")

        body_str = " | ".join(body_stats) if body_stats else ""
        pref_str = "、".join(user.preferences) if user.preferences else "暂无"

        lines = [
            f"# 🏃 {user.username} 的 Keep 运动资产大盘",
            "",
            f"> **会员身份**：{'✨ VIP 会员' if user.member else '普通会员'} | **等级**：Lv.{user.duration_level} | **注册时间**：{user.registration_date or '未知'}",
        ]
        if body_str:
            lines.append(f"> 🧍‍♂️ **身体档案**：{body_str} | **偏好项目**：{pref_str}")
        lines.extend([
            "",
            "### 📊 核心累计指标",
            "",
            "| 指标项 | 累计数据 | 单位 | 说明 |",
            "| :--- | :--- | :--- | :--- |",
            f"| 📅 **累计训练天数** | **{summary.total_days}** | 天 | 坚持运动的生命历程 |",
            f"| ⏱️ **累计运动时长** | **{summary.total_minutes}** | 分钟 ({summary.total_minutes // 60} 小时 {summary.total_minutes % 60} 分) | 真实运动总时间 |",
            f"| 🔥 **累计燃烧热量** | **{summary.total_calories:,}** | kCal (千卡) | 相当于消耗了约 {round(summary.total_calories / 7700, 1)} kg 纯脂肪 |",
            f"| 🏃 **累计跑步里程** | **{summary.running_distance_km}** | 公里 (km) | 户外 + 室内跑步总合 |",
            f"| 🚴 **累计骑行里程** | **{summary.cycling_distance_km}** | 公里 (km) | 户外骑行 |",
            f"| 🚶 **累计健走里程** | **{summary.walking_distance_km}** | 公里 (km) | 户外健步走 |",
            f"| 📝 **社区打卡动态** | **{summary.total_entries}** | 条 | 历史发布的运动打卡日记 |",
            "",
            f"💪 **累计打卡总次数**：**{summary.total_workout_count}** 次",
        ])
        return "\n".join(lines)

    def format_workout_detail_markdown(self, detail: Dict[str, Any]) -> str:
        """Format single workout detail into comprehensive Markdown report."""
        hr = detail.get("heart_rate", {})
        splits = detail.get("splits", [])

        lines = [
            f"# 🏅 Keep 运动记录分析报告 - {detail.get('sport_type', '').upper()}",
            "",
            f"> **开始时间**：`{detail.get('start_time')}`  ",
            f"> **运动编号**：`{detail.get('workout_id')}`",
            "",
            "### ⚡ 核心表现",
            "",
            "| 距离 | 运动耗时 | 平均配速 | 消耗热量 | 平均心率 | 最大心率 | 平均步频 | 累计爬升 |",
            "| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
            f"| **{detail.get('distance_km')} km** | **{detail.get('duration_minutes')} 分钟** | **{detail.get('avg_pace')}** | **{detail.get('calories')} kcal** | **{hr.get('average') or '-'} bpm** | **{hr.get('max') or '-'} bpm** | **{detail.get('avg_cadence') or '-'} spm** | **{detail.get('elevation_gain_m') or 0} m** |",
            "",
        ]

        if splits:
            lines.extend([
                "### 📈 每公里分段配速 (Splits)",
                "",
                "| 公里数 | 单公里配速 | 耗时 | 爬升 |",
                "| :---: | :---: | :---: | :---: |",
            ])
            for sp in splits:
                m = sp['duration_seconds'] // 60
                s = sp['duration_seconds'] % 60
                lines.append(f"| 第 {sp['km']} km | **{sp['pace']}** | {m}分{s:02d}秒 | +{sp.get('elevation', 0)}m |")
            lines.append("")

        if detail.get("has_gps"):
            lines.append(f"> 🗺️ **GPS 轨迹**：已记录 **{detail.get('gps_points_count')}** 个经纬度轨迹点，支持调用 `keep export-gpx` 导出标准 GPX 轨迹。")
            lines.append("")

        return "\n".join(lines)


# Singleton helper
_client_instance: Optional[KeepClient] = None


def get_keep_client() -> KeepClient:
    """Get or create singleton KeepClient."""
    global _client_instance
    if _client_instance is None:
        _client_instance = KeepClient()
    return _client_instance
