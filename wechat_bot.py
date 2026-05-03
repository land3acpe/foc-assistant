"""FOC-Assistant 微信网关 —— 基于腾讯 iLink Bot API

将 FOC-Assistant 接入个人微信，通过 iLink 官方 API 收发消息。
启动后扫码登录，即可在微信中给 Agent 下达任务。

协议: https://ilinkai.weixin.qq.com
"""

import asyncio
import base64
import json
import os
import random
import struct
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiohttp

from config import (
    MAX_ITERATIONS,
    WECHAT_DANGER_ALLOW,
    WECHAT_MAX_RESPONSE_LEN,
    WECHAT_SESSION_TIMEOUT,
)
from agent import AgentCallbacks, agent_loop

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

ILINK_BASE = "https://ilinkai.weixin.qq.com"
TOKEN_FILE = Path(__file__).parent / ".wechat_token.json"
QR_FILE = Path(__file__).parent / ".wechat_qr.png"

POLL_TIMEOUT = 35          # 长轮询保持秒数
TYPING_INTERVAL = 4        # "正在输入..." 发送间隔
RECONNECT_DELAY = 3        # 重连等待秒数
SESSION_CLEANUP = 600      # 会话清理间隔 (10 分钟)
MAX_USER_MSG_LEN = 2000    # 单条收到的消息最大处理长度

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class BotToken:
    token: str
    expires_at: float
    bot_uin: str = ""

@dataclass
class WeChatSession:
    wx_uin: str
    context_token: str = ""
    last_active: float = field(default_factory=time.time)
    is_processing: bool = False

# ---------------------------------------------------------------------------
# iLink 认证 & HTTP 工具
# ---------------------------------------------------------------------------

def _rand_uin() -> str:
    """生成随机 X-WECHAT-UIN: uint32 → 十进制字符串 → base64"""
    n = random.randint(1, 2**32 - 1)
    return base64.b64encode(str(n).encode()).decode()

def _auth_headers(token: str) -> dict:
    return {
        "AuthorizationType": "ilink_bot_token",
        "Authorization": f"Bearer {token}",
        "X-WECHAT-UIN": _rand_uin(),
        "Content-Type": "application/json",
    }

def _now_ts() -> float:
    return time.time()

# ---------------------------------------------------------------------------
# WeChatBot
# ---------------------------------------------------------------------------

class WeChatBot:
    """微信 Bot 主类"""

    def __init__(self):
        self.token: Optional[BotToken] = None
        self.sessions: dict[str, WeChatSession] = {}
        self._cursor: str = ""
        self._http: Optional[aiohttp.ClientSession] = None
        self._running = False

    # ---- 登录流程 ----

    async def login(self) -> bool:
        """执行二维码登录全流程。成功返回 True。"""
        # 关闭旧的 HTTP 会话
        old_http = self._http
        if old_http:
            try:
                await old_http.close()
            except Exception:
                pass

        # 使用独立的 cookie jar 维持登录会话
        jar = aiohttp.CookieJar()
        self._http = aiohttp.ClientSession(cookie_jar=jar)

        # 1. 获取二维码
        print("[WeChat] 正在获取登录二维码...")
        try:
            async with self._http.get(
                f"{ILINK_BASE}/ilink/bot/get_bot_qrcode",
                params={"bot_type": "3"},
                headers={"X-WECHAT-UIN": _rand_uin()},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                content_type = resp.headers.get("Content-Type", "")
                body = await resp.read()
                print(f"[WeChat] QR 响应: Content-Type={content_type}, size={len(body)} bytes")
        except Exception as e:
            print(f"[WeChat] 获取二维码失败: {e}")
            self._http = old_http
            return False

        # 处理响应：先尝试 JSON 解析（无论 Content-Type），再处理二进制
        qrcode_ref: Optional[str] = None  # 用于状态轮询的引用
        img_url: Optional[str] = None

        try:
            data = json.loads(body)
            print(f"[WeChat] QR JSON: {json.dumps(data, ensure_ascii=False)[:300]}")
            qrcode_ref = data.get("qrcode_buf") or data.get("qrcode") or data.get("token") or str(resp.url)
            img_url = data.get("qrcode_img_content") or data.get("qrcode_url") or data.get("url")

            if img_url:
                # 生成本地二维码图片，方便手机扫描
                try:
                    import qrcode as qrlib
                    qr_img = qrlib.make(img_url)
                    qr_img.save(str(QR_FILE))
                    print(f"\n  [WeChat] 二维码已保存到: {QR_FILE}")
                    print(f"  或直接打开链接: {img_url}\n")
                except Exception:
                    print(f"\n  [WeChat] 请打开以下链接用微信扫描:")
                    print(f"  {img_url}\n")
            elif qrcode_ref:
                # 尝试作为 base64 图片解码
                try:
                    img_data = base64.b64decode(qrcode_ref)
                    QR_FILE.write_bytes(img_data)
                    print(f"\n  [WeChat] 二维码已保存到: {QR_FILE}")
                    print(f"  请打开该图片用微信扫描\n")
                except Exception:
                    print(f"\n  [WeChat] 二维码引用: {qrcode_ref[:100]}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            if "image" in content_type or "octet-stream" in content_type:
                QR_FILE.write_bytes(body)
                print(f"\n  [WeChat] 二维码已保存到: {QR_FILE}")
                print(f"  请打开该图片用微信扫描\n")
                qrcode_ref = str(resp.url)
            else:
                text = body.decode("utf-8", errors="ignore").strip()
                print(f"[WeChat] QR 响应文本: {text[:300]}")
                if text.startswith("http"):
                    print(f"\n[WeChat] 请打开链接用微信扫描:\n  {text}\n")
                    qrcode_ref = text
                else:
                    QR_FILE.write_bytes(body)
                    print(f"\n[WeChat] 已保存到: {QR_FILE} (未知格式)")
                    qrcode_ref = str(resp.url)

        # 2. 轮询扫码状态
        print("[WeChat] 等待扫码 (最长 120 秒)...")
        poll_headers = {"X-WECHAT-UIN": _rand_uin()}

        for attempt in range(120):
            await asyncio.sleep(1)
            try:
                # 尝试多种参数格式
                params = {}
                if qrcode_ref:
                    for key in ("qrcode_buf", "qrcode", "token", "qrcode_url"):
                        params[key] = qrcode_ref
                        break  # 只用一个 key

                async with self._http.get(
                    f"{ILINK_BASE}/ilink/bot/get_qrcode_status",
                    params=params,
                    headers=poll_headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    ctype = resp.headers.get("Content-Type", "")
                    raw = await resp.read()
                    if "json" in ctype:
                        status = json.loads(raw)
                    else:
                        text = raw.decode("utf-8", errors="ignore")
                        try:
                            status = json.loads(text)
                        except json.JSONDecodeError:
                            # 纯文本响应，检查是否包含 token
                            if len(text) > 10 and ":" not in text:
                                status = {"ret": 0, "bot_token": text}
                            else:
                                if attempt % 15 == 0:
                                    print(f"  [{attempt}s] 非 JSON 响应: {text[:100]}")
                                continue
            except Exception as e:
                if attempt % 15 == 0:
                    print(f"  [{attempt}s] 状态查询异常: {e}")
                continue

            status_code = status.get("ret") or status.get("status") or status.get("code")
            if status_code in (0, "0", "scanned", "confirmed", "success"):
                bot_token = status.get("bot_token") or status.get("token") or status.get("access_token")
                if bot_token:
                    self.token = BotToken(
                        token=bot_token,
                        expires_at=_now_ts() + 23 * 3600,
                    )
                    self._save_token()
                    print(f"[WeChat] 登录成功! Token: {bot_token[:16]}...")
                    return True
                print(f"[WeChat] 扫码成功但未获取到 token: {json.dumps(status, ensure_ascii=False)[:200]}")
                return False

            if status_code in ("waiting", "pending", 1, "1", None):
                if attempt % 10 == 0:
                    print(f"  ...等待扫码 ({attempt}s)")
                continue

            # 其他状态
            if attempt % 15 == 0:
                print(f"  [{attempt}s] status: {json.dumps(status, ensure_ascii=False)[:150]}")

        print("[WeChat] 扫码超时 (120s)")
        return False

    async def _load_or_login(self) -> bool:
        """加载已存储的 token 或重新登录"""
        if self._load_token() and self.token and self.token.expires_at > _now_ts():
            print(f"[WeChat] 使用已保存的 token, 过期时间: {datetime.fromtimestamp(self.token.expires_at).strftime('%H:%M:%S')}")
            if not self._http:
                self._http = aiohttp.ClientSession()
            return True
        return await self.login()

    def _load_token(self) -> bool:
        if TOKEN_FILE.exists():
            try:
                data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
                self.token = BotToken(**data)
                return True
            except Exception:
                pass
        return False

    def _save_token(self):
        if self.token:
            TOKEN_FILE.write_text(
                json.dumps({"token": self.token.token, "expires_at": self.token.expires_at, "bot_uin": self.token.bot_uin}),
                encoding="utf-8",
            )

    # ---- 消息接收 ----

    async def _long_poll(self) -> list[dict]:
        """35s 长轮询，返回新消息列表"""
        if not self._http or not self.token:
            return []

        try:
            async with self._http.post(
                f"{ILINK_BASE}/ilink/bot/getupdates",
                json={"get_updates_buf": self._cursor},
                headers=_auth_headers(self.token.token),
                timeout=aiohttp.ClientTimeout(total=POLL_TIMEOUT + 10),
            ) as resp:
                if resp.status in (401, 403):
                    print(f"[WeChat] Token 过期 (HTTP {resp.status})，重新登录...")
                    self.token = None
                    return []
                if resp.status != 200:
                    text = await resp.text()
                    print(f"[WeChat] getupdates HTTP {resp.status}: {text[:200]}")
                    return []

                data = await resp.json()

                # 更新游标
                new_cursor = data.get("get_updates_buf") or data.get("next_buf") or ""
                if new_cursor:
                    self._cursor = new_cursor

                msgs = data.get("msgs") or data.get("messages") or []
                return msgs

        except asyncio.TimeoutError:
            return []
        except aiohttp.ClientError as e:
            print(f"[WeChat] 长轮询网络错误: {e}")
            return []
        except Exception as e:
            print(f"[WeChat] 长轮询异常: {e}")
            return []

    # ---- 消息发送 ----

    async def send_message(self, session: WeChatSession, text: str) -> bool:
        """发送文本消息到指定用户"""
        if not self._http or not self.token:
            return False

        # 长文本分段发送
        chunks = self._split_text(text)
        success = True
        for chunk in chunks:
            if not await self._send_one(session, chunk):
                success = False
        return success

    async def _send_one(self, session: WeChatSession, text: str) -> bool:
        """发送单条消息"""
        client_id = f"{int(time.time()*1000)}_{random.randint(1000,9999)}"
        body = {
            "msg": {
                "to_user_id": session.wx_uin,
                "from_user_id": f"{session.wx_uin}@im.wechat",
                "client_id": client_id,
                "context_token": session.context_token,
                "message_type": 2,
                "message_state": 2,
                "item_list": [
                    {"type": 1, "text": text}
                ],
            }
        }

        try:
            async with self._http.post(
                f"{ILINK_BASE}/ilink/bot/sendmessage",
                json=body,
                headers=_auth_headers(self.token.token),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    return True
                text_resp = await resp.text()
                print(f"[WeChat] sendmessage HTTP {resp.status}: {text_resp[:150]}")
                return False
        except Exception as e:
            print(f"[WeChat] 发送消息失败: {e}")
            return False

    async def send_typing(self, session: WeChatSession) -> bool:
        """发送"正在输入..."状态"""
        if not self._http or not self.token:
            return False

        body = {
            "msg": {
                "to_user_id": session.wx_uin,
                "from_user_id": f"{session.wx_uin}@im.wechat",
                "context_token": session.context_token,
                "message_type": 2,
                "message_state": 1,  # typing indicator
            }
        }

        try:
            async with self._http.post(
                f"{ILINK_BASE}/ilink/bot/sendtyping",
                json=body,
                headers=_auth_headers(self.token.token),
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _split_text(self, text: str) -> list[str]:
        """将长文本按合理边界分段"""
        if len(text) <= WECHAT_MAX_RESPONSE_LEN:
            return [text]

        chunks = []
        remaining = text
        while len(remaining) > WECHAT_MAX_RESPONSE_LEN:
            # 优先在换行处断开
            split_at = remaining.rfind("\n", 0, WECHAT_MAX_RESPONSE_LEN)
            if split_at < WECHAT_MAX_RESPONSE_LEN // 2:
                split_at = remaining.rfind(" ", 0, WECHAT_MAX_RESPONSE_LEN)
            if split_at < WECHAT_MAX_RESPONSE_LEN // 2:
                split_at = WECHAT_MAX_RESPONSE_LEN
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:].lstrip()
        if remaining:
            chunks.append(remaining)
        return chunks

    # ---- Agent 调度 ----

    async def _run_agent(self, session: WeChatSession, text: str) -> str:
        """在 executor 中运行 agent_loop，收集完整响应"""
        full_parts: list[str] = []
        tool_call_count = [0]

        def on_token(t: str):
            full_parts.append(t)

        def on_tool_call(name: str, args: dict):
            tool_call_count[0] += 1

        def on_tool_result(result: str):
            pass  # 工具结果不直接展示

        def on_danger_confirm(command: str) -> bool:
            if WECHAT_DANGER_ALLOW:
                print(f"[SEC] 微信用户请求执行危险命令: {command[:100]}")
                return False
            print(f"[SEC] 自动拒绝危险命令: {command[:100]}")
            return False

        def on_status(msg: str):
            pass  # 状态行不回传微信

        def on_complete(summary: str, elapsed: float, calls: int):
            pass  # Agent 完成，主逻辑会处理

        callbacks = AgentCallbacks(
            on_token=on_token,
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result,
            on_danger_confirm=on_danger_confirm,
            on_status=on_status,
            on_complete=on_complete,
        )

        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None,
                agent_loop,
                text,
                MAX_ITERATIONS,
                callbacks,
            )
            return result or "".join(full_parts) or "(Agent 未产生输出)"
        except Exception as e:
            import traceback
            traceback.print_exc()
            return f"[ERROR] Agent 执行异常: {e}"

    # ---- 消息处理 ----

    async def _handle_message(self, msg: dict):
        """处理单条微信消息"""
        item_list = msg.get("item_list") or []
        if not item_list:
            return

        # 只处理文本消息 (type=1)
        text_content = ""
        for item in item_list:
            if item.get("type") == 1:
                text_content += item.get("text", "")
            elif item.get("type") == 2:
                text_content = "[图片]"
            elif item.get("type") == 3:
                text_content = "[语音]"
            elif item.get("type") == 4:
                text_content = "[文件]"
            else:
                text_content = f"[不支持的消息类型]"

        if not text_content:
            return

        # 获取用户标识
        wx_uin = msg.get("from_user_id", "").replace("@im.wechat", "")
        context_token = msg.get("context_token", "")

        if not wx_uin or not context_token:
            print(f"[WeChat] 消息缺少 uin/context_token: {json.dumps(msg, ensure_ascii=False)[:200]}")
            return

        # 获取或创建会话
        if wx_uin not in self.sessions:
            self.sessions[wx_uin] = WeChatSession(wx_uin=wx_uin)

        session = self.sessions[wx_uin]
        session.context_token = context_token
        session.last_active = _now_ts()

        if session.is_processing:
            await self.send_message(session, "(正在处理上一条任务，请稍候...)")
            return

        # 截断过长输入
        if len(text_content) > MAX_USER_MSG_LEN:
            text_content = text_content[:MAX_USER_MSG_LEN] + "\n...(输入过长已截断)"

        print(f"\n[WeChat] 收到任务 [{wx_uin[:8]}...]: {text_content[:80]}")

        session.is_processing = True

        # 启动"正在输入"定时器
        typing_active = True
        async def typing_loop():
            while typing_active:
                await self.send_typing(session)
                await asyncio.sleep(TYPING_INTERVAL)

        typing_task = asyncio.create_task(typing_loop())

        try:
            response = await self._run_agent(session, text_content)
            await self.send_message(session, response)
            print(f"[WeChat] 回复 [{wx_uin[:8]}...]: {response[:80]}...")
        except Exception as e:
            await self.send_message(session, f"[错误] {e}")
        finally:
            typing_active = False
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass
            session.is_processing = False

    # ---- 会话清理 ----

    async def _cleanup_sessions(self):
        """定期清理过期会话"""
        while self._running:
            await asyncio.sleep(SESSION_CLEANUP)
            cutoff = _now_ts() - WECHAT_SESSION_TIMEOUT
            stale = [uin for uin, s in self.sessions.items() if s.last_active < cutoff and not s.is_processing]
            for uin in stale:
                del self.sessions[uin]
            if stale:
                print(f"[WeChat] 清理了 {len(stale)} 个过期会话")

    # ---- 主循环 ----

    async def run(self):
        """Bot 主循环"""
        print("=" * 55)
        print("  FOC-Assistant WeChat Bot")
        print(f"  API: {ILINK_BASE}")
        print(f"  启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 55)

        # 初始化 HTTP 会话
        if not self._http:
            self._http = aiohttp.ClientSession()

        self._running = True

        # 启动会话清理后台任务
        cleanup_task = asyncio.create_task(self._cleanup_sessions())

        while self._running:
            try:
                # 确保已登录
                if not self.token or self.token.expires_at < _now_ts():
                    if not await self._load_or_login():
                        print("[WeChat] 登录失败，30 秒后重试...")
                        await asyncio.sleep(30)
                        continue

                # 长轮询收消息
                msgs = await self._long_poll()

                # 如果 token 过期（_long_poll 会清掉 token）
                if not self.token:
                    continue

                for msg in msgs:
                    asyncio.create_task(self._handle_message(msg))

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[WeChat] 主循环异常: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(RECONNECT_DELAY)

        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass

        if self._http:
            await self._http.close()

        print("[WeChat] Bot 已停止")

    async def stop(self):
        """优雅停止"""
        self._running = False


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main():
    """启动微信 Bot"""
    print()
    print("  FOC-Assistant → 微信 Bot 模式")
    print()
    print("  启动后请用微信扫描二维码登录")
    print("  登录成功后，在微信中发送消息即可给 Agent 下达任务")
    print()

    bot = WeChatBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        print("\n[WeChat] 收到中断信号，正在退出...")
    finally:
        print("[WeChat] 已退出")


if __name__ == "__main__":
    main()
