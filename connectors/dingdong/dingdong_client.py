#!/usr/bin/env python3
"""
Dingdong Maicai (叮咚买菜) API Client.

Provides capabilities for:
1. Resolving nearby store (前置仓/站点) based on geographic coordinates.
2. Checking store service availability, delivery times, and station metadata.
3. Fetching product category taxonomy for a station.
4. Retrieving real-time local hot keywords and trends.
5. Searching and retrieving goods with full metadata:
   - Price (现价, 原价, 会员价)
   - Stock (库存量, 缺货状态)
   - Specs & Description (商品详细介绍, 净重规格)
   - Sales & Selling Points (累计销量, 决策标签)
"""

import os
import re
import json
import time
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Dict, Any, List, Optional, Union


class DingDongClient:
    """Client for interacting with Dingdong Maicai API endpoints."""

    BASE_MAICAI_URL = "https://maicai.api.ddxq.mobi"
    BASE_SUNQUAN_URL = "https://sunquan.api.ddxq.mobi"

    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 15_5 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E217 "
        "MicroMessenger/6.8.0(0x16080000) NetType/WIFI Language/zh_CN "
        "Branch/Br_trunk MiniProgramEnv/Mac"
    )

    def __init__(self, cookie: Optional[str] = None):
        """
        Initialize the DingDong client.
        :param cookie: Optional DDXQSESSID cookie string for authenticated endpoints.
                       Defaults to environment variable DDMC_COOKIE if available.
        """
        self.cookie = cookie or os.getenv("DDMC_COOKIE", "")
        # Check candidate .env files if not found in environment
        if not self.cookie:
            candidate_envs = [
                Path(__file__).parent / ".env",
                Path.home() / "Desktop" / "dingdong" / ".env",
                Path.home() / ".agents" / "skills" / "dingdong-ai" / ".env",
                Path(__file__).resolve().parent.parent.parent / ".agents" / "skills" / "dingdong-ai" / ".env",
            ]
            for env_path in candidate_envs:
                if env_path.is_file():
                    try:
                        for line in env_path.read_text(encoding="utf-8").splitlines():
                            if line.startswith("DDMC_COOKIE="):
                                self.cookie = line.split("=", 1)[1].strip()
                                break
                    except Exception:
                        pass
                if self.cookie:
                    break

        if self.cookie and not self.cookie.startswith("DDXQSESSID=") and ";" not in self.cookie:
            self.cookie = f"DDXQSESSID={self.cookie}"

    def _request(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Union[Dict[str, Any], bytes, str]] = None,
        method: str = "GET",
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 8,
    ) -> Dict[str, Any]:
        """Perform HTTP request (GET/POST) and parse JSON response."""
        if params:
            query_string = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            delimiter = "&" if "?" in url else "?"
            url = f"{url}{delimiter}{query_string}"

        req_headers = {
            "User-Agent": self.DEFAULT_USER_AGENT,
            "Origin": "https://wx.m.ddxq.mobi",
            "Referer": "https://wx.m.ddxq.mobi/",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        if self.cookie:
            req_headers["Cookie"] = self.cookie

        # Automatically inject ddmc headers if present in params or data
        for src in [params, data]:
            if isinstance(src, dict):
                if "station_id" in src and src["station_id"]:
                    req_headers["ddmc-station-id"] = str(src["station_id"])
                if "city_number" in src and src["city_number"]:
                    req_headers["ddmc-city-number"] = str(src["city_number"])
                if "channel" in src and src["channel"]:
                    req_headers["ddmc-channel"] = str(src["channel"])
                if "api_version" in src and src["api_version"]:
                    req_headers["ddmc-api-version"] = str(src["api_version"])
                if "app_client_id" in src and src["app_client_id"]:
                    req_headers["ddmc-app-client-id"] = str(src["app_client_id"])

        encoded_data = None
        if data is not None or method.upper() == "POST":
            method = "POST"
            if isinstance(data, dict):
                encoded_data = urllib.parse.urlencode(data).encode("utf-8")
                req_headers["Content-Type"] = "application/x-www-form-urlencoded"
            elif isinstance(data, (bytes, bytearray)):
                encoded_data = data
            elif isinstance(data, str):
                encoded_data = data.encode("utf-8")

        if headers:
            req_headers.update(headers)

        req = urllib.request.Request(url, data=encoded_data, headers=req_headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            res_text = response.read().decode("utf-8")
            if not res_text:
                return {}
            return json.loads(res_text)

    def check_station_by_coordinates(
        self, longitude: float, latitude: float
    ) -> Dict[str, Any]:
        """
        Check whether there is a Dingdong store (前置仓) nearby for given coordinates.
        :param longitude: 经度 (e.g., 121.633998)
        :param latitude: 纬度 (e.g., 31.188597)
        :return: Dict containing station info, availability status, and metadata.
        """
        url = f"{self.BASE_SUNQUAN_URL}/api/v2/user/location/refresh/"
        params = {
            "longitude": longitude,
            "latitude": latitude,
        }
        res = self._request(url, params=params)
        if not res or not res.get("success"):
            return {
                "has_station": False,
                "message": res.get("message", "定位查询失败"),
                "raw": res,
            }

        data = res.get("data", {})
        station_id = data.get("station_id")
        station_info = data.get("station_info", {})
        village_id = data.get("village_id", "")

        # When within coverage, station_id is non-empty. When outside coverage, station_id is empty.
        is_covered = bool(station_id and station_id.strip())

        return {
            "has_station": is_covered,
            "station_id": station_id if is_covered else "",
            "station_name": station_info.get("name", ""),
            "city_number": data.get("city_number", "0101"),
            "city_name": station_info.get("city_name", ""),
            "business_time": station_info.get("business_time", ""),
            "phone": station_info.get("phone", ""),
            "village_id": village_id,
            "coordinates": {"longitude": longitude, "latitude": latitude},
            "status_text": "在叮咚买菜配送范围内" if is_covered else "超出叮咚买菜当前配送范围",
        }

    def get_categories(self, station_id: str) -> List[Dict[str, Any]]:
        """
        Fetch full category taxonomy for a specific store.
        :param station_id: 前置仓 ID
        :return: List of top-level categories and subcategories.
        """
        url = f"{self.BASE_MAICAI_URL}/homeApi/newCategories"
        params = {
            "station_id": station_id,
            "api_version": "9.49.1",
        }
        res = self._request(url, params=params)
        categories = []
        if res.get("success") and "data" in res and "cate" in res["data"]:
            for top_cate in res["data"]["cate"]:
                sub_cates = []
                for sub in top_cate.get("cate", []):
                    sub_cates.append({
                        "id": sub.get("id"),
                        "name": sub.get("name"),
                        "image": sub.get("category_image_url"),
                    })
                categories.append({
                    "id": top_cate.get("id"),
                    "name": top_cate.get("name"),
                    "subcategories": sub_cates,
                })
        return categories

    def get_hot_keywords(
        self, station_id: str, city_number: str = "0101"
    ) -> List[str]:
        """
        Get real-time popular search terms and trending groceries for the local store.
        """
        url = f"{self.BASE_MAICAI_URL}/search/rollHotKeyword"
        params = {
            "station_id": station_id,
            "city_number": city_number,
        }
        res = self._request(url, params=params)
        words = []
        if res.get("success") and "data" in res and "keywords" in res["data"]:
            for item in res["data"]["keywords"]:
                word = item.get("word") or item.get("showWord")
                if word and word not in words:
                    words.append(word)
        return words

    def get_ranked_goods(
        self,
        station_id: str,
        city_number: str = "0101",
        keyword: Optional[str] = None,
        limit: int = 15,
    ) -> List[Dict[str, Any]]:
        """
        Get trending / ranked products for a station (open without login).
        Includes price, original price, VIP price, stock number, detailed specs, and sales.
        :param station_id: 站点 ID
        :param city_number: 城市编号
        :param keyword: Optional keyword filter
        :param limit: Maximum products to return
        """
        url = f"{self.BASE_MAICAI_URL}/search/rankingList"
        params = {
            "station_id": station_id,
            "city_number": city_number,
        }
        res = self._request(url, params=params)
        goods = []
        if res.get("success") and "data" in res and "ranking_lists" in res["data"]:
            for rlist in res["data"]["ranking_lists"]:
                rank_title = rlist.get("rank_info", {}).get("list_title", "热榜")
                for item in rlist.get("list", []):
                    name = item.get("product_name") or item.get("name", "")
                    spec = item.get("spec", "")
                    tags = item.get("decision_information") or item.get("recommended_reason") or []

                    # If keyword provided, perform match
                    if keyword:
                        kw_lower = keyword.lower()
                        match = (
                            kw_lower in name.lower()
                            or kw_lower in spec.lower()
                            or any(kw_lower in t.lower() for t in tags)
                        )
                        if not match:
                            continue

                    goods.append({
                        "id": item.get("id"),
                        "name": name,
                        "price": float(item.get("price", 0)),
                        "origin_price": float(item.get("origin_price") or item.get("price", 0)),
                        "vip_price": float(item.get("vip_price")) if item.get("vip_price") else None,
                        "stock_number": item.get("stock_number", 0),
                        "is_in_stock": item.get("stock_number", 0) > 0 and item.get("status") == 1,
                        "spec": spec,
                        "total_sales": item.get("total_sales", 0),
                        "tags": tags,
                        "sale_unit": item.get("sale_unit", ""),
                        "net_weight": f"{item.get('net_weight', '')}{item.get('net_weight_unit', '')}",
                        "rank_title": rank_title,
                        "image_url": item.get("small_image", ""),
                    })

                    if len(goods) >= limit:
                        return goods
        return goods

    def search_goods(
        self,
        station_id: str,
        keyword: str,
        city_number: str = "0101",
        page: int = 1,
        page_size: int = 20,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Search for goods in the specified station.
        If a valid cookie is present, calls the online searchProduct API.
        Otherwise, searches within station ranking lists and popular items with an advisory notice.
        """
        effective_limit = limit or page_size
        # If user configured a cookie, try the official searchProduct endpoint
        if self.cookie:
            url = f"{self.BASE_MAICAI_URL}/search/searchProduct"
            params = {
                "keyword": keyword,
                "station_id": station_id,
                "city_number": city_number,
                "page": page,
                "page_size": page_size,
                "channel": "applet",
                "api_version": "9.50.0",
                "app_client_id": "4",
            }
            try:
                res = self._request(url, params=params)
                if res.get("success") and "data" in res:
                    raw_list = res["data"].get("result_list") or res["data"].get("product_list") or []
                    items = []
                    for item in raw_list:
                        item_name = item.get("product_name") or item.get("name")
                        if not item_name:
                            continue
                        items.append({
                            "id": item.get("id"),
                            "name": item_name,
                            "price": float(item.get("price", 0)),
                            "origin_price": float(item.get("origin_price") or item.get("price", 0)),
                            "vip_price": float(item.get("vip_price")) if item.get("vip_price") else None,
                            "stock_number": item.get("stock_number", 0),
                            "is_in_stock": item.get("stock_number", 0) > 0,
                            "spec": item.get("spec", ""),
                            "total_sales": item.get("total_sales", 0),
                            "tags": item.get("decision_information") or [],
                            "image_url": item.get("small_image", ""),
                        })
                    return {
                        "mode": "authenticated_search",
                        "total": len(items),
                        "items": items,
                        "notice": "已使用已配置的 Cookie 进行在线全量搜索。",
                    }
            except Exception as e:
                # If cookie expired or blocked, fallback
                pass

        # Fallback mode: Search within station's active ranked catalog
        fallback_goods = self.get_ranked_goods(station_id, city_number=city_number, keyword=keyword, limit=effective_limit)
        return {
            "mode": "open_ranking_search",
            "total": len(fallback_goods),
            "items": fallback_goods,
            "notice": (
                "当前处于免登录开放模式。已在「张江站/当前门店」的实时热销榜与精选商品中匹配。"
                "如需进行全库长尾商品搜索，可设置环境变量 DDMC_COOKIE（从小程序或H5抓取 DDXQSESSID）。"
            ),
        }

    @staticmethod
    def _is_mongodb_object_id(val: str) -> bool:
        """Check if string is a standard 24-character hex MongoDB ObjectId."""
        if not val or not isinstance(val, str):
            return False
        cleaned = val.strip().strip("'\"`")
        return bool(re.fullmatch(r"[0-9a-fA-F]{24}", cleaned))

    @staticmethod
    def _clean_product_query(query: str) -> str:
        """
        Clean and extract pure product name or search keywords from arbitrary input
        (handles markdown images, URLs, quotation marks, leading numbers, etc.).
        """
        if not query or not isinstance(query, str):
            return ""
        s = str(query).strip().strip("'\"`")

        # Handle markdown image format: ![alt](url)
        img_match = re.search(r"!\[(.*?)\]\(.*?\)", s)
        if img_match:
            alt = img_match.group(1).strip()
            alt = re.sub(r"\|\d+", "", alt).strip()
            if alt:
                s = alt

        # Strip URL if passed bare url
        if s.startswith("http://") or s.startswith("https://"):
            return ""

        # Strip leading numbers/bullets like "1. ", "1、", "#1 ", "[1] "
        s = re.sub(r"^(#|\d+[\.、\s]|\[\d+\]\s*)+", "", s).strip()
        # Strip surrounding braces/brackets like 【...】 or [...]
        if (s.startswith("【") and s.endswith("】")) or (s.startswith("[") and s.endswith("]")):
            s = s[1:-1].strip()

        return s

    def resolve_product(
        self,
        station_id: str,
        query_or_name: str,
        city_number: str = "0101",
    ) -> Optional[Dict[str, Any]]:
        """
        Intelligently resolves a product name, keyword, or fuzzy string to an exact in-stock product.
        Returns product dict with id, name, price, spec, stock_number, etc. or None.
        """
        clean_name = self._clean_product_query(query_or_name)
        if not clean_name:
            return None

        # 1. Search with the cleaned name
        res = self.search_goods(station_id, keyword=clean_name, city_number=city_number, limit=15)
        items = res.get("items", [])

        # 2. If nothing found and clean_name has spec suffix (e.g. "普罗旺斯番茄 约500g" -> "普罗旺斯番茄")
        if not items:
            simplified = re.sub(r"(\s*(约)?\d+(g|kg|ml|L|个|只|盒|份|包|罐|瓶)(/\w+)?.*)$", "", clean_name, flags=re.IGNORECASE).strip()
            if simplified and simplified != clean_name:
                res = self.search_goods(station_id, keyword=simplified, city_number=city_number, limit=15)
                items = res.get("items", [])

        if not items:
            return None

        # 3. Match scoring
        # Priority 1: Exact match on name
        for it in items:
            if it.get("name") and it["name"].strip() == clean_name:
                return it

        # Priority 2: In-stock items where clean_name is a substring of item name or vice versa
        for it in items:
            it_name = it.get("name", "").strip()
            if it.get("is_in_stock") and (clean_name in it_name or it_name in clean_name):
                return it

        # Priority 3: Any in-stock item in the search results
        for it in items:
            if it.get("is_in_stock"):
                return it

        # Priority 4: First returned item
        return items[0]

    def _raw_add_to_cart(
        self,
        station_id: str,
        product_id: str,
        count: int = 1,
        city_number: str = "0101",
    ) -> Dict[str, Any]:
        """Low-level POST to cart/add."""
        url = f"{self.BASE_MAICAI_URL}/cart/add"
        payload = {
            "station_id": station_id,
            "city_number": city_number,
            "api_version": "9.50.0",
            "app_client_id": "4",
            "channel": "applet",
            "products": json.dumps([{"id": product_id, "count": count}]),
        }

        data = urllib.parse.urlencode(payload).encode("utf-8")
        req_headers = {
            "User-Agent": self.DEFAULT_USER_AGENT,
            "Origin": "https://wx.m.ddxq.mobi",
            "Referer": "https://wx.m.ddxq.mobi/",
            "Cookie": self.cookie,
            "Content-Type": "application/x-www-form-urlencoded",
            "ddmc-city-number": city_number,
            "ddmc-station-id": station_id,
            "ddmc-channel": "applet",
            "ddmc-api-version": "9.50.0",
            "ddmc-app-client-id": "4",
        }

        req = urllib.request.Request(url, data=data, headers=req_headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=8) as response:
                res = json.loads(response.read().decode("utf-8"))
                if res.get("success"):
                    return {
                        "success": True,
                        "product_id": product_id,
                        "message": f"成功将商品 (ID: {product_id}, 数量: {count}) 加入叮咚买菜购物车！",
                        "data": res.get("data", {}),
                    }
                return {
                    "success": False,
                    "product_id": product_id,
                    "message": res.get("msg") or res.get("message", "加购请求未成功"),
                    "raw": res,
                }
        except urllib.error.HTTPError as e:
            return {
                "success": False,
                "product_id": product_id,
                "message": f"加购 HTTP 错误: {e.code}（可能是 Cookie 已过期或触发网关校验）",
            }
        except Exception as e:
            return {
                "success": False,
                "product_id": product_id,
                "message": f"加购异常: {str(e)}",
            }

    def add_to_cart(
        self,
        station_id: str,
        product_id: str,
        count: int = 1,
        city_number: str = "0101",
        product_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Add a product to user's Dingdong shopping cart.
        Supports both exact 24-character product ID and product name/fuzzy search keyword.
        If a name/keyword is provided or the ID cannot be found directly,
        intelligently resolves the matching product and its real ID automatically.
        :param station_id: 前置仓站点 ID
        :param product_id: 商品 ID 或 商品名称（例如 '5cb88c88bdb36542305186e3' 或 '普罗旺斯番茄'）
        :param count: 加购数量 (默认 1)
        :param city_number: 城市编码 (默认 0101)
        :param product_name: 可选商品名称备注
        """
        if not self.cookie:
            return {
                "success": False,
                "message": "添加购物车失败：未配置用户登录凭证 (DDMC_COOKIE)。加购属于用户账户资产操作，请先抓包配置 Cookie。",
            }

        product_id_str = str(product_id or "").strip()
        product_name_str = str(product_name or "").strip()

        # Check if product_id_str is a standard 24-hex MongoDB ObjectId
        if self._is_mongodb_object_id(product_id_str):
            res = self._raw_add_to_cart(station_id, product_id_str, count, city_number)
            if res.get("success"):
                if product_name_str:
                    res["product_name"] = product_name_str
                    res["message"] = f"成功将商品【{product_name_str}】(ID: {product_id_str}, 数量: {count}) 加入叮咚买菜购物车！"
                return res

            # If failed with "商品不存在" or similar, and product_name was provided, fallback to resolve
            err_msg = res.get("message", "")
            if ("商品不存在" in err_msg or "下架" in err_msg) and product_name_str:
                resolved = self.resolve_product(station_id, product_name_str, city_number)
                if resolved and resolved.get("id") and resolved["id"] != product_id_str:
                    fallback_res = self._raw_add_to_cart(station_id, resolved["id"], count, city_number)
                    if fallback_res.get("success"):
                        fallback_res["product_name"] = resolved["name"]
                        fallback_res["resolved_from"] = product_name_str
                        fallback_res["message"] = (
                            f"原商品 ID (`{product_id_str}`) 已失效，系统已自动根据名称智能检索并匹配在售商品"
                            f"【{resolved['name']}】(新 ID: `{resolved['id']}`)，成功加入叮咚买菜购物车（数量: {count}）！"
                        )
                        return fallback_res
            return res

        # Otherwise, product_id is not a 24-hex ID (it may be a product name, markdown image, keyword, etc.)
        query = product_name_str or product_id_str
        resolved = self.resolve_product(station_id, query, city_number)
        if not resolved or not resolved.get("id"):
            return {
                "success": False,
                "message": (
                    f"未能根据「{query}」自动识别并匹配到有效的叮咚在售商品。\n"
                    f"建议先调用 search_dingdong_goods 搜索确认菜品全名或商品 ID 后再尝试加购。"
                ),
            }

        real_id = resolved["id"]
        matched_name = resolved["name"]
        res = self._raw_add_to_cart(station_id, real_id, count, city_number)
        if res.get("success"):
            return {
                "success": True,
                "product_id": real_id,
                "product_name": matched_name,
                "spec": resolved.get("spec", ""),
                "price": resolved.get("price", 0),
                "message": f"已智能识别并匹配商品【{matched_name}】(ID: `{real_id}`)，成功加入叮咚买菜购物车（数量: {count}）！",
                "data": res.get("data", {}),
            }
        else:
            return {
                "success": False,
                "product_id": real_id,
                "product_name": matched_name,
                "message": f"已识别商品【{matched_name}】(ID: `{real_id}`)，但加购接口返回: {res.get('message', '未知错误')}",
                "raw": res,
            }

    def get_cart(self, station_id: str, city_number: str = "0101") -> Dict[str, Any]:
        """
        Inspect current user's shopping cart items and totals.
        Requires authenticated DDMC_COOKIE.
        """
        if not self.cookie:
            return {
                "success": False,
                "message": "查看购物车失败：未配置 DDMC_COOKIE。",
            }

        url = f"{self.BASE_MAICAI_URL}/cart/index"
        params = {
            "station_id": station_id,
            "city_number": city_number,
            "is_load": "1",
            "api_version": "9.50.0",
            "app_client_id": "4",
        }
        res = self._request(url, params=params)
        return res

    def set_cart_all_checked(self, station_id: str, is_check: int = 1, city_number: str = "0101") -> Dict[str, Any]:
        """
        全量选中或全量取消选中购物车中的所有商品。
        :param station_id: 站点 ID
        :param is_check: 1 为全选，0 为全不选
        :param city_number: 城市编号
        """
        if not self.cookie:
            return {"success": False, "message": "未登录或未配置 DDMC_COOKIE"}
        url = f"{self.BASE_MAICAI_URL}/cart/allCheck"
        params = {
            "station_id": station_id,
            "city_number": city_number,
            "is_check": str(is_check),
            "is_load": "1",
            "api_version": "9.50.0",
            "app_client_id": "4",
        }
        return self._request(url, params=params)

    def update_cart_product_check(
        self,
        station_id: str,
        product_id: str,
        is_check: int = 1,
        cart_id: Optional[str] = None,
        city_number: str = "0101",
    ) -> Dict[str, Any]:
        """
        更新购物车中单个商品的勾选状态（打勾或取消打勾）。
        :param station_id: 站点 ID
        :param product_id: 商品 ID
        :param is_check: 1 为勾选，0 为取消勾选
        :param cart_id: 购物车记录 ID（未传时默认使用 product_id）
        :param city_number: 城市编号
        """
        if not self.cookie:
            return {"success": False, "message": "未登录或未配置 DDMC_COOKIE"}
        url = f"{self.BASE_MAICAI_URL}/cart/updateCheck"
        product_obj = {
            "id": product_id,
            "is_check": is_check,
            "cart_id": cart_id or product_id,
        }
        payload = {
            "station_id": station_id,
            "city_number": city_number,
            "product": json.dumps(product_obj),
            "is_load": "1",
            "api_version": "9.50.0",
            "app_client_id": "4",
        }
        return self._request(url, method="POST", data=payload)

    def delete_cart_items(
        self,
        product_items: List[Dict[str, str]],
        station_id: str,
        city_number: str = "0101",
    ) -> Dict[str, Any]:
        """
        从购物车中批量删除商品。
        :param product_items: 商品列表 [{"id": "...", "cart_id": "..."}]
        :param station_id: 站点 ID
        :param city_number: 城市编号
        """
        if not self.cookie:
            return {"success": False, "message": "未登录或未配置 DDMC_COOKIE"}
        if not product_items:
            return {"success": True, "message": "未提供待删除的商品"}
        url = f"{self.BASE_MAICAI_URL}/cart/del"
        payload = {
            "station_id": station_id,
            "city_number": city_number,
            "products": json.dumps(product_items),
            "is_load": "1",
            "api_version": "9.50.0",
            "app_client_id": "4",
        }
        return self._request(url, method="POST", data=payload)

    def select_cart_items(
        self,
        product_identifiers: Union[str, List[str]],
        exclusive: bool = True,
        station_id: Optional[str] = None,
        city_number: str = "0101",
    ) -> Dict[str, Any]:
        """
        智能选中购物车中的指定商品（支持商品名称模糊匹配或商品 ID）。
        :param product_identifiers: 商品名称（如 "苹果", "番茄"）或商品 ID 列表，可传逗号分隔字符串或列表
        :param exclusive: 是否排他性选中（默认 True：先将购物车全不选，只单独勾选指定商品；False 则保留其他已有勾选）
        :param station_id: 站点 ID（可选，未传时自动使用默认收货地址对应站点）
        :param city_number: 城市编号
        """
        if not self.cookie:
            return {"success": False, "message": "未登录或未配置 DDMC_COOKIE"}

        # 1. 解析目标站点与城市
        cur_station_id = station_id
        cur_city_number = city_number
        if not cur_station_id:
            addrs_res = self.get_addresses()
            addrs = addrs_res.get("addresses", [])
            if addrs:
                target_addr = next((a for a in addrs if a.get("is_default")), addrs[0])
                cur_station_id = target_addr.get("station_id") or ""
                cur_city_number = target_addr.get("city_number") or city_number

        if not cur_station_id:
            return {"success": False, "message": "缺少有效的站点 ID (station_id)，请先配置或添加收货地址"}

        # 2. 规范化商品标识符
        if isinstance(product_identifiers, str):
            parts = re.split(r"[,，、\n;；]+", product_identifiers)
            raw_targets = [p.strip() for p in parts if p.strip()]
        else:
            raw_targets = [str(p).strip() for p in product_identifiers if str(p).strip()]

        if not raw_targets:
            return {"success": False, "message": "未指定要勾选的商品名称或商品 ID"}

        # 3. 读取购物车全量商品列表
        cart_res = self.get_cart(cur_station_id, cur_city_number)
        if not cart_res.get("success"):
            return {"success": False, "message": f"拉取购物车失败: {cart_res.get('message', '未知错误')}"}

        data = cart_res.get("data", {})
        eff_groups = data.get("product", {}).get("effective", [])
        all_products = []
        for g in eff_groups:
            for p in g.get("products", []):
                all_products.append(p)

        if not all_products:
            return {"success": False, "message": "当前购物车为空，无可勾选的商品"}

        # 4. 匹配目标商品
        matched_products = []
        matched_pids = set()
        unmatched_targets = []

        for target in raw_targets:
            target_lower = target.lower()
            hit = False
            for p in all_products:
                pid = str(p.get("id", ""))
                pname = str(p.get("product_name") or p.get("name", "")).lower()
                if pid == target or target_lower in pname or pname in target_lower:
                    if pid not in matched_pids:
                        matched_pids.add(pid)
                        matched_products.append(p)
                    hit = True
            if not hit:
                unmatched_targets.append(target)

        if not matched_products:
            return {
                "success": False,
                "message": f"在当前购物车中未找到匹配的商品: {', '.join(unmatched_targets)}",
                "cart_products": [p.get("product_name") for p in all_products],
            }

        # 5. 执行勾选逻辑
        if exclusive:
            # 先将购物车全部取消勾选
            self.set_cart_all_checked(cur_station_id, is_check=0, city_number=cur_city_number)

        # 逐一勾选匹配的商品
        for p in matched_products:
            pid = str(p.get("id", ""))
            cart_id = str(p.get("cart_id") or pid)
            self.update_cart_product_check(cur_station_id, pid, is_check=1, cart_id=cart_id, city_number=cur_city_number)

        # 6. 重新拉取购物车以确认更新后的状态与金额
        updated_cart = self.get_cart(cur_station_id, cur_city_number)
        up_data = updated_cart.get("data", {})
        total_payable = up_data.get("total_money", "0.00")
        goods_total = up_data.get("goods_real_money", "0.00")
        freight = up_data.get("freight_money", "0.00")

        selected_items = []
        for p in matched_products:
            selected_items.append({
                "id": p.get("id"),
                "name": p.get("product_name") or p.get("name"),
                "count": p.get("count", 1),
                "price": str(p.get("price", "0.00")),
                "total_price": str(p.get("total_price", "0.00")),
            })

        return {
            "success": True,
            "message": f"成功选中 {len(matched_products)} 件商品进行结算！已自动排除其余商品。",
            "exclusive": exclusive,
            "selected_count": len(matched_products),
            "selected_products": selected_items,
            "unmatched_targets": unmatched_targets,
            "pricing": {
                "goods_total": goods_total,
                "freight": freight,
                "total_payable": total_payable,
            },
        }

    def remove_cart_items(
        self,
        product_identifiers: Optional[Union[str, List[str]]] = None,
        clear_all: bool = False,
        station_id: Optional[str] = None,
        city_number: str = "0101",
    ) -> Dict[str, Any]:
        """
        从购物车中移除指定商品，或一键清空购物车。
        :param product_identifiers: 商品名称（支持模糊匹配）或商品 ID 列表
        :param clear_all: 是否清空全部购物车商品（默认 False）
        :param station_id: 站点 ID
        :param city_number: 城市编号
        """
        if not self.cookie:
            return {"success": False, "message": "未登录或未配置 DDMC_COOKIE"}

        # 1. 解析目标站点与城市
        cur_station_id = station_id
        cur_city_number = city_number
        if not cur_station_id:
            addrs_res = self.get_addresses()
            addrs = addrs_res.get("addresses", [])
            if addrs:
                target_addr = next((a for a in addrs if a.get("is_default")), addrs[0])
                cur_station_id = target_addr.get("station_id") or ""
                cur_city_number = target_addr.get("city_number") or city_number

        if not cur_station_id:
            return {"success": False, "message": "缺少有效的站点 ID (station_id)，请先配置或添加收货地址"}

        # 2. 读取当前购物车
        cart_res = self.get_cart(cur_station_id, cur_city_number)
        if not cart_res.get("success"):
            return {"success": False, "message": f"拉取购物车失败: {cart_res.get('message', '未知错误')}"}

        data = cart_res.get("data", {})
        eff_groups = data.get("product", {}).get("effective", [])
        invalid_list = data.get("product", {}).get("invalid", [])
        all_products = []
        for g in eff_groups:
            for p in g.get("products", []):
                all_products.append(p)
        for p in invalid_list:
            all_products.append(p)

        if not all_products:
            return {"success": True, "message": "当前购物车已经是空的，无需删除。"}

        # 3. 确定要删除的项
        to_del = []
        deleted_names = []
        if clear_all:
            for p in all_products:
                pid = str(p.get("id", ""))
                cid = str(p.get("cart_id") or pid)
                to_del.append({"id": pid, "cart_id": cid})
                deleted_names.append(p.get("product_name") or p.get("name", "商品"))
        else:
            if not product_identifiers:
                return {"success": False, "message": "请指定要删除的商品名称/ID，或设置 clear_all=True"}
            if isinstance(product_identifiers, str):
                parts = re.split(r"[,，、\n;；]+", product_identifiers)
                raw_targets = [p.strip() for p in parts if p.strip()]
            else:
                raw_targets = [str(p).strip() for p in product_identifiers if str(p).strip()]

            matched_pids = set()
            for target in raw_targets:
                target_lower = target.lower()
                for p in all_products:
                    pid = str(p.get("id", ""))
                    pname = str(p.get("product_name") or p.get("name", "")).lower()
                    cid = str(p.get("cart_id") or pid)
                    if pid == target or target_lower in pname or pname in target_lower:
                        if pid not in matched_pids:
                            matched_pids.add(pid)
                            to_del.append({"id": pid, "cart_id": cid})
                            deleted_names.append(p.get("product_name") or p.get("name", pid))

            if not to_del:
                return {
                    "success": False,
                    "message": f"在购物车中未找到要删除的商品: {product_identifiers}",
                }

        # 4. 执行删除 API
        del_res = self.delete_cart_items(to_del, cur_station_id, cur_city_number)
        if not del_res.get("success"):
            return {"success": False, "message": f"删除商品失败: {del_res.get('msg', '未知错误')}"}

        return {
            "success": True,
            "message": f"成功从购物车移除 {len(to_del)} 件商品: {', '.join(deleted_names)}",
            "deleted_count": len(to_del),
            "deleted_items": deleted_names,
            "remaining_count": max(0, len(all_products) - len(to_del)),
        }

    def get_user_info(self) -> Dict[str, Any]:
        """
        Inspect current logged-in user's profile info (nickname, mobile, vip status).
        Requires authenticated DDMC_COOKIE.
        """
        if not self.cookie:
            return {
                "success": False,
                "message": "未登录或未配置 DDMC_COOKIE",
            }
        url = f"{self.BASE_MAICAI_URL}/user/info"
        res = self._request(url)
        return res

    def get_addresses(self) -> Dict[str, Any]:
        """
        Get the current user's saved delivery addresses.
        Requires authenticated DDMC_COOKIE.
        """
        if not self.cookie:
            return {"success": False, "message": "未登录或未配置 DDMC_COOKIE", "addresses": []}

        url = f"{self.BASE_SUNQUAN_URL}/api/v1/user/address/"
        res = self._request(url)
        if res.get("success"):
            addrs = res.get("data", {}).get("valid_address", [])
            formatted = []
            for a in addrs:
                formatted.append({
                    "id": a.get("id"),
                    "user_name": a.get("user_name"),
                    "mobile": a.get("mobile"),
                    "address": a.get("location", {}).get("address", ""),
                    "detail": a.get("addr_detail", ""),
                    "is_default": a.get("is_default", 0) == 1,
                    "station_id": a.get("station_id", ""),
                    "city_number": a.get("city_number", "0101"),
                })
            return {"success": True, "addresses": formatted}
        return {"success": False, "message": res.get("message", res.get("msg", "获取地址失败")), "addresses": []}

    def get_checkout_url(self) -> str:
        """
        获取叮咚买菜移动端/H5一键直达结算与支付页面的链接。
        """
        return "https://wx.m.ddxq.mobi/#/pages/mainPackage/cart/cart"

    def preview_order(
        self,
        station_id: Optional[str] = None,
        address_id: Optional[str] = None,
        city_number: str = "0101",
        product_identifiers: Optional[Union[str, List[str]]] = None,
    ) -> Dict[str, Any]:
        """
        装配生成订单全景审批单（供用户审核确认）：
        1. 订单里的商品明细（名称、规格、数量、单价、小计）
        2. 订单送达地址（收件人、联系电话、具体地址与门牌号）
        3. 配送履约时段（最快预计送达时间）
        4. 订单金额全量明细（商品总额、基础运费、免运费门槛差额提示、最终扣款金额）
        5. 直达结算付款页链接（供非免密用户自主点击支付）
        :param product_identifiers: 可选。指定本次仅结算选中的商品（商品名称或ID），自动反选其余商品
        """
        if not self.cookie:
            return {"success": False, "message": "未登录或未配置 DDMC_COOKIE，无法预览订单"}

        # 1. 解析目标地址
        addrs_res = self.get_addresses()
        addrs = addrs_res.get("addresses", [])
        if not addrs:
            return {
                "success": False,
                "message": "未找到有效收货地址，请先在叮咚买菜添加或同步收货地址。",
            }

        target_addr = None
        if address_id:
            for a in addrs:
                if a["id"] == address_id:
                    target_addr = a
                    break
        if not target_addr:
            target_addr = next((a for a in addrs if a.get("is_default")), addrs[0])

        cur_station_id = station_id or target_addr.get("station_id") or ""
        cur_city_number = city_number or target_addr.get("city_number", "0101")

        # 1.5 若指定了结算商品，先执行精准排他性勾选
        if product_identifiers:
            sel_res = self.select_cart_items(
                product_identifiers,
                exclusive=True,
                station_id=cur_station_id,
                city_number=cur_city_number,
            )
            if not sel_res.get("success"):
                return sel_res

        # 2. 拉取购物车商品快照
        cart_res = self.get_cart(cur_station_id, cur_city_number)
        if not cart_res.get("success"):
            return {
                "success": False,
                "message": f"拉取购物车信息失败: {cart_res.get('message', '未知错误')}",
            }

        data = cart_res.get("data", {})
        products = []
        for group in data.get("new_order_product_list", []):
            for p in group.get("products", []):
                products.append({
                    "id": p.get("id"),
                    "name": p.get("product_name") or p.get("name", "商品"),
                    "spec": p.get("spec") or "",
                    "count": p.get("count", 1),
                    "price": str(p.get("price", "0.00")),
                    "total_price": str(p.get("total_price", "0.00")),
                    "image": p.get("small_image", ""),
                })

        if not products:
            return {
                "success": False,
                "message": "当前叮咚买菜购物车为空，请先搜索商品并加入购物车后发起结算审批。",
            }

        goods_total = float(data.get("goods_real_money") or data.get("total_money") or 0.0)
        freight_money = float(data.get("freight_money") or 0.0)
        final_total = round(goods_total + (freight_money if freight_money > 0 else 0.0), 2)

        free_freight_info = data.get("free_freight_notice", {})
        free_freight_tip = ""
        if isinstance(free_freight_info, dict):
            tip1 = free_freight_info.get("title1", "")
            tip2 = free_freight_info.get("applet_title2", "")
            if tip1 or tip2:
                free_freight_tip = f"{tip1}（{tip2}）".strip("（）")

        return {
            "success": True,
            "address": {
                "id": target_addr["id"],
                "user_name": target_addr["user_name"],
                "mobile": target_addr["mobile"],
                "full_address": f"{target_addr['address']} {target_addr['detail']}".strip(),
                "is_default": target_addr.get("is_default", False),
            },
            "station_id": cur_station_id,
            "city_number": cur_city_number,
            "products": products,
            "pricing": {
                "goods_total": f"{goods_total:.2f}",
                "freight": f"{freight_money:.2f}",
                "free_freight_tip": free_freight_tip,
                "final_total": f"{final_total:.2f}",
            },
            "delivery_window": "今日即时配送（最快 30~45 分钟内送达）",
            "checkout_url": self.get_checkout_url(),
        }

    @staticmethod
    def get_order_links(order_number: str) -> Dict[str, str]:
        """
        Generate direct payment, order detail, and mini-program URLs for a given order.
        """
        return {
            "pay_url": f"https://wx.m.ddxq.mobi/#/pages/repayPackage/repay/repay?orderNumber={order_number}",
            "detail_url": f"https://wx.m.ddxq.mobi/#/pages/orderPackage/mineOrderDetail/mineOrderDetail?order_number={order_number}",
            "orders_list_url": "https://wx.m.ddxq.mobi/#/pages/orderPackage/mineOrders/mineOrders",
            "miniprogram_pay_path": f"pages/repayPackage/repay/repay?orderNumber={order_number}",
            "miniprogram_detail_path": f"pages/orderPackage/mineOrderDetail/mineOrderDetail?order_number={order_number}",
            "app_scheme": f"ddmc://order/detail?order_number={order_number}",
        }

    def get_order_history(self, page: int = 1, limit: int = 10) -> Dict[str, Any]:
        """
        Fetch user's historical orders list.
        Requires authenticated DDMC_COOKIE.
        :param page: Page number (starts from 1)
        :param limit: Number of orders per page
        """
        if not self.cookie:
            return {"success": False, "message": "未登录或未配置 DDMC_COOKIE"}
        url = f"{self.BASE_MAICAI_URL}/order/list"
        params = {"page": page, "limit": limit}
        res = self._request(url, params=params)
        if res.get("code") == 0:
            raw_orders = res.get("data", {}).get("order_list", [])
            formatted = []
            for o in raw_orders:
                products = []
                for p in o.get("product", []):
                    products.append({
                        "id": p.get("id"),
                        "name": p.get("product_name"),
                        "count": p.get("count"),
                        "price": p.get("price"),
                        "total_price": p.get("total_price"),
                        "image": p.get("small_image"),
                    })
                ctime = o.get("create_time", 0)
                formatted.append({
                    "order_number": o.get("order_number"),
                    "status_msg": o.get("order_status_msg", "已完成"),
                    "total_money": o.get("total_money"),
                    "pay_money": o.get("pay_money"),
                    "freight_money": o.get("freight_money"),
                    "create_time": ctime,
                    "create_time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ctime)) if ctime else "",
                    "pay_type_msg": o.get("pay_type_msg", ""),
                    "station_id": o.get("station_id"),
                    "products": products,
                })
            return {
                "success": True,
                "total_count": len(formatted),
                "orders": formatted,
            }
        return {"success": False, "message": res.get("msg", "获取订单列表失败")}

    def get_order_detail(self, order_number: str) -> Dict[str, Any]:
        """
        Fetch full details of a specific historical order.
        Requires authenticated DDMC_COOKIE.
        """
        if not self.cookie:
            return {"success": False, "message": "未登录或未配置 DDMC_COOKIE"}
        url = f"{self.BASE_MAICAI_URL}/order/detail"
        params = {"order_number": order_number}
        res = self._request(url, params=params)
        if res.get("code") == 0 or res.get("success"):
            data = res.get("data", {})
            links = self.get_order_links(order_number)
            return {"success": True, "data": data, "links": links}
        return {"success": False, "message": res.get("msg", "获取订单详情失败")}

    @staticmethod
    def interactive_login(timeout_seconds: int = 180) -> str:
        """
        Pop up an official mobile Dingdong Maicai window via Playwright.
        The user inputs their phone number and SMS code in the real UI.
        Upon successful login, automatically extracts DDXQSESSID, saves to .env, and closes window.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print("❌ 未安装 playwright，请先运行: pip install playwright")
            return ""

        print("\n🌐 正在唤起叮咚买菜官方登录界面（全新无痕手机窗口）...")
        print("💡 请在弹出的窗口中输入需要绑定的【新手机号】并完成短信验证码登录。")
        print("   登录成功后，脚本将自动提取新账号 Cookie、覆盖保存配置并自动关闭窗口。\n")

        cookie_val = ""
        with sync_playwright() as p:
            iphone = p.devices.get("iPhone 12", {
                "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 15_5 like Mac OS X) AppleWebKit/605.1.15",
                "viewport": {"width": 390, "height": 844},
                "device_scale_factor": 3,
                "is_mobile": True,
                "has_touch": True,
            })
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(**iphone)
            page = context.new_page()
            page.goto("https://wx.m.ddxq.mobi/#/pages/common/login/login", timeout=30000)

            start_t = time.time()
            while time.time() - start_t < timeout_seconds:
                try:
                    cookies = context.cookies()
                except Exception:
                    # Window was closed by user
                    break

                for c in cookies:
                    if c.get("name") == "DDXQSESSID" and c.get("value"):
                        cookie_val = c.get("value")
                        break
                if cookie_val:
                    break
                time.sleep(1)

            try:
                browser.close()
            except Exception:
                pass

        if cookie_val:
            env_file = Path(__file__).parent / ".env"
            env_file.write_text(f"DDMC_COOKIE=DDXQSESSID={cookie_val}\n", encoding="utf-8")
            print("🎉 登录成功！已自动提取并在本地持久化保存 DDXQSESSID。")
            # 立即验证账号身份
            try:
                temp_client = DingDongClient(cookie=f"DDXQSESSID={cookie_val}")
                info_res = temp_client.get_user_info()
                u_data = info_res.get("data", {})
                if u_data:
                    name = u_data.get("name", "叮咚用户")
                    phone = u_data.get("hiddenMobile", u_data.get("mobile", "未知"))
                    vip = "👑 会员" if u_data.get("is_vip") == 1 else "普通用户"
                    print(f"👤 当前生效账号: 【{name}】 ({phone}) [{vip}]")
            except Exception:
                pass
            return cookie_val
        else:
            print("⏱️ 登录超时或窗口被关闭。")
            return ""
