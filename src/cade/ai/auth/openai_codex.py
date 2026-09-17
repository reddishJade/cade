"""OpenAI Codex (ChatGPT Plus/Pro) OAuth 授权实现。

支持标准 PKCE 浏览器回调授权与 Device Code 终端无头授权，
支持 Token 自动刷新与 account_id 解析。
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import logging
import secrets
import threading
import time
import urllib.parse
import webbrowser
from collections.abc import Callable
from typing import Any, cast

import httpx

from .types import AuthCredential

_LOGGER = logging.getLogger(__name__)

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTH_BASE_URL = "https://auth.openai.com"
AUTHORIZE_URL = f"{AUTH_BASE_URL}/oauth/authorize"
TOKEN_URL = f"{AUTH_BASE_URL}/oauth/token"
REDIRECT_URI = "http://localhost:1455/auth/callback"
DEVICE_USER_CODE_URL = f"{AUTH_BASE_URL}/api/accounts/deviceauth/usercode"
DEVICE_TOKEN_URL = f"{AUTH_BASE_URL}/api/accounts/deviceauth/token"
DEVICE_VERIFICATION_URI = f"{AUTH_BASE_URL}/codex/device"
DEVICE_REDIRECT_URI = f"{AUTH_BASE_URL}/deviceauth/callback"
SCOPE = "openid profile email offline_access"


def generate_pkce() -> tuple[str, str]:
    """生成 PKCE verifier 与 S256 challenge。"""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def extract_account_and_expiry_from_jwt(
    jwt_token: str,
) -> tuple[str | None, int | None]:
    """从 JWT Token Payload 中安全解析 chatgpt_account_id 与过期时间。"""
    parts = jwt_token.split(".")
    if len(parts) < 2:
        return None, None
    try:
        padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
        payload_bytes = base64.urlsafe_b64decode(padded)
        payload = json.loads(payload_bytes)
        auth_info = payload.get("https://api.openai.com/auth", {})
        account_id = auth_info.get("chatgpt_account_id")
        exp = payload.get("exp")
        return (str(account_id) if account_id else None), exp
    except (
        ValueError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        KeyError,
        IndexError,
        TypeError,
    ) as exc:
        _LOGGER.debug("Failed to extract info from JWT: %s", exc)
        return None, None


class _OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    """用于接收 OAuth 回调临时服务器的请求处理器。"""

    def do_GET(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        if parsed_url.path != "/auth/callback":
            self.send_response(404)
            self.end_headers()
            return

        cb_server = cast(_OAuthCallbackServer, self.server)
        query = urllib.parse.parse_qs(parsed_url.query)
        cb_server.received_code = query.get("code", [None])[0]
        cb_server.received_state = query.get("state", [None])[0]
        cb_server.received_error = query.get("error", [None])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        html = """
        <html>
        <body style="font-family: sans-serif; text-align: center; padding: 50px;">
            <h2 style="color: #10a37f;">Cade authorization successful</h2>
            <p>Connected to your ChatGPT subscription account. You can close this browser window and return to the terminal.</p>
        </body>
        </html>
        """
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, format: str, *args: Any) -> None:
        pass


class _OAuthCallbackServer(http.server.HTTPServer):
    received_code: str | None = None
    received_state: str | None = None
    received_error: str | None = None


def login_openai_codex_browser(
    open_browser: bool = True,
    notify_callback: Callable[[str], None] | None = None,
    timeout_seconds: int = 180,
) -> AuthCredential:
    """通过系统默认浏览器执行 PKCE 授权登录。"""
    verifier, challenge = generate_pkce()
    state = secrets.token_urlsafe(16)

    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": "cade",
    }
    auth_url = f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"

    if notify_callback:
        notify_callback(
            f"Complete ChatGPT subscription authorization in your browser.\n"
            f"If the browser does not open automatically, visit:\n{auth_url}"
        )

    server = _OAuthCallbackServer(("127.0.0.1", 1455), _OAuthCallbackHandler)
    server_thread = threading.Thread(target=server.handle_request, daemon=True)
    server_thread.start()

    if open_browser:
        webbrowser.open(auth_url)

    start_time = time.time()
    while server_thread.is_alive():
        if time.time() - start_time > timeout_seconds:
            server.server_close()
            raise TimeoutError(
                "OpenAI Codex browser authorization timed out; please retry."
            )
        time.sleep(0.5)

    server.server_close()

    if server.received_error:
        raise RuntimeError(
            f"OpenAI Codex authorization failed: {server.received_error}"
        )

    if not server.received_code:
        raise RuntimeError("Could not obtain an authorization code from the callback.")

    if server.received_state != state:
        raise RuntimeError("OAuth state mismatch; a CSRF attack may be in progress.")

    return exchange_code_for_token(server.received_code, verifier, REDIRECT_URI)


def login_openai_codex_device_code(
    notify_callback: Callable[[str, str], None],
    timeout_seconds: int = 900,
) -> AuthCredential:
    """通过 Device Code（适合远程、SSH 或无头环境）授权登录。"""
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            DEVICE_USER_CODE_URL,
            json={"client_id": CLIENT_ID},
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Failed to obtain device code ({resp.status_code}): {resp.text}"
            )
        data = resp.json()
        device_code = data["device_code"]
        user_code = data["user_code"]
        interval = data.get("interval", 5)

    notify_callback(DEVICE_VERIFICATION_URI, user_code)

    start_time = time.time()
    with httpx.Client(timeout=30.0) as client:
        while time.time() - start_time < timeout_seconds:
            time.sleep(interval)
            poll_resp = client.post(
                DEVICE_TOKEN_URL,
                json={
                    "client_id": CLIENT_ID,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
                headers={"Content-Type": "application/json"},
            )
            if poll_resp.status_code == 200:
                token_data = poll_resp.json()
                authorization_code = token_data.get("authorization_code")
                code_verifier = token_data.get("code_verifier")
                if authorization_code and code_verifier:
                    return exchange_code_for_token(
                        authorization_code, code_verifier, DEVICE_REDIRECT_URI
                    )
                return _build_credential_from_token_response(token_data)

            err_data = poll_resp.json() if poll_resp.content else {}
            err_code = err_data.get("error")
            if err_code == "authorization_pending":
                continue
            if err_code == "slow_down":
                interval += 5
                continue
            raise RuntimeError(
                f"Device Code authorization failed ({poll_resp.status_code}): {poll_resp.text}"
            )

    raise TimeoutError("Device code authorization timed out; please retry.")


def exchange_code_for_token(
    code: str,
    verifier: str,
    redirect_uri: str,
) -> AuthCredential:
    """使用授权码与 PKCE verifier 换取 Token。"""
    data = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": redirect_uri,
    }
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(TOKEN_URL, data=data)
        if resp.status_code != 200:
            raise RuntimeError(
                f"Failed to exchange authorization code for an access token ({resp.status_code}): {resp.text}"
            )
        return _build_credential_from_token_response(resp.json())


def refresh_openai_codex_token(refresh_token: str) -> AuthCredential:
    """使用 refresh_token 刷新访问令牌。"""
    data = {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "refresh_token": refresh_token,
    }
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(TOKEN_URL, data=data)
        if resp.status_code != 200:
            raise RuntimeError(
                f"Token refresh failed ({resp.status_code}): {resp.text}"
            )
        token_data = resp.json()
        if "refresh_token" not in token_data:
            token_data["refresh_token"] = refresh_token
        return _build_credential_from_token_response(token_data)


def _build_credential_from_token_response(
    data: dict[str, Any],
) -> AuthCredential:
    access_token = data.get("access_token", "")
    refresh_token = data.get("refresh_token")
    expires_in = data.get("expires_in")
    account_id, exp = extract_account_and_expiry_from_jwt(access_token)

    now = int(time.time())
    if expires_in:
        expires = now + int(expires_in)
    elif exp:
        expires = int(exp)
    else:
        expires = now + 3600

    return AuthCredential(
        type="oauth",
        access=access_token,
        refresh=refresh_token,
        expires=expires,
        account_id=account_id,
        provider="openai-codex",
    )


class OpenAICodexAuthProvider:
    """OpenAI Codex 的登录、Token 交换与刷新实现。"""

    id = "openai-codex"

    def login(
        self,
        *,
        method: str,
        notify_callback: Callable[..., None] | None = None,
    ) -> AuthCredential:
        """按指定交互方式执行 Codex OAuth 登录。"""
        if method == "device_code":
            if notify_callback is None:
                raise ValueError("Device code login requires notify_callback")
            return login_openai_codex_device_code(notify_callback=notify_callback)
        if method == "browser":
            return login_openai_codex_browser(notify_callback=notify_callback)
        raise ValueError(f"Unsupported OpenAI Codex login method: {method}")

    def refresh(self, credential: AuthCredential) -> AuthCredential:
        """使用已存凭据刷新 Codex Access Token。"""
        if not credential.refresh:
            raise ValueError("OpenAI Codex credentials are missing a refresh token")
        return refresh_openai_codex_token(credential.refresh)
