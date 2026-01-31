from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import httpx
import os
import uuid
import re
import base64
import json
import asyncio
from typing import Dict, Set
from datetime import datetime
import xml.etree.ElementTree as ET

app = FastAPI()

WECHAT_API_URL = os.getenv("WECHAT_API_URL", "http://localhost:30001/api")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")
WINDOWS_SAVE_DIR = os.getenv("WINDOWS_SAVE_DIR", "")

# 图片下载目录
IMG_DOWNLOAD_DIR = os.getenv("IMG_DOWNLOAD_DIR", "/tmp/wechat_images")
os.makedirs(IMG_DOWNLOAD_DIR, exist_ok=True)
app.mount("/files", StaticFiles(directory=IMG_DOWNLOAD_DIR), name="files")


# ==================== WebSocket 连接管理器 ====================
class ConnectionManager:
    """WebSocket连接管理器，管理所有活跃的WebSocket连接"""
    
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.connection_info: Dict[str, dict] = {}
    
    async def connect(self, websocket: WebSocket, client_id: str = None) -> str:
        await websocket.accept()
        if not client_id:
            client_id = str(uuid.uuid4())[:8]
        self.active_connections[client_id] = websocket
        self.connection_info[client_id] = {
            "connected_at": datetime.now().isoformat(),
            "client_id": client_id
        }
        print(f"[WebSocket] 客户端连接: {client_id}, 当前连接数: {len(self.active_connections)}")
        return client_id
    
    def disconnect(self, client_id: str):
        if client_id in self.active_connections:
            del self.active_connections[client_id]
        if client_id in self.connection_info:
            del self.connection_info[client_id]
        print(f"[WebSocket] 客户端断开: {client_id}, 当前连接数: {len(self.active_connections)}")
    
    async def send_personal_message(self, message: dict, client_id: str) -> bool:
        if client_id in self.active_connections:
            try:
                await self.active_connections[client_id].send_json(message)
                return True
            except Exception as e:
                print(f"[WebSocket] 发送消息失败 {client_id}: {e}")
                self.disconnect(client_id)
        return False
    
    async def broadcast(self, message: dict, exclude: Set[str] = None):
        exclude = exclude or set()
        disconnected = []
        for client_id, connection in self.active_connections.items():
            if client_id not in exclude:
                try:
                    await connection.send_json(message)
                except Exception as e:
                    print(f"[WebSocket] 广播消息失败 {client_id}: {e}")
                    disconnected.append(client_id)
        for client_id in disconnected:
            self.disconnect(client_id)
    
    def get_connection_count(self) -> int:
        return len(self.active_connections)
    
    def get_all_clients(self) -> list:
        return list(self.connection_info.values())


manager = ConnectionManager()


# ==================== 图片处理函数 ====================
def is_image_url(text: str) -> bool:
    if not text:
        return False
    pattern = r'^https?://.*\.(jpg|jpeg|png|gif|webp|bmp)(\?.*)?$|^https?://.*/(0|132|64)$'
    return bool(re.match(pattern, text.lower()))


def is_base64_image(text: str) -> bool:
    if not text:
        return False
    return text.startswith("data:image/")


def save_base64_image(data_url: str) -> str:
    try:
        if ";base64," in data_url:
            header, base64_data = data_url.split(";base64,", 1)
            ext = ".png"
            if "jpeg" in header or "jpg" in header:
                ext = ".jpg"
            elif "gif" in header:
                ext = ".gif"
            elif "webp" in header:
                ext = ".webp"
            image_bytes = base64.b64decode(base64_data)
            filename = f"{uuid.uuid4().hex}{ext}"
            filepath = os.path.join(IMG_DOWNLOAD_DIR, filename)
            with open(filepath, "wb") as f:
                f.write(image_bytes)
            print(f"Base64图片保存成功: {filepath} ({len(image_bytes)} bytes)")
            return filepath
    except Exception as e:
        print(f"Base64图片保存失败: {e}")
    return None


async def download_image(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url)
            if response.status_code == 200:
                ext = ".jpg"
                if "png" in url.lower():
                    ext = ".png"
                elif "gif" in url.lower():
                    ext = ".gif"
                elif "webp" in url.lower():
                    ext = ".webp"
                filename = f"{uuid.uuid4().hex}{ext}"
                filepath = os.path.join(IMG_DOWNLOAD_DIR, filename)
                with open(filepath, "wb") as f:
                    f.write(response.content)
                print(f"图片下载成功: {url} -> {filepath}")
                return filepath
    except Exception as e:
        print(f"图片下载失败: {e}")
    return None


# ==================== 核心消息处理函数 ====================
def _parse_img_xml(xml_str: str) -> dict:
    try:
        root = ET.fromstring(xml_str)
        img = root.find("img")
        if img is None:
            return {}
        return {
            "aeskey": img.attrib.get("aeskey"),
            "cdnthumburl": img.attrib.get("cdnthumburl"),
            "cdnmidimgurl": img.attrib.get("cdnmidimgurl"),
            "length": img.attrib.get("length"),
        }
    except Exception:
        return {}


def _parse_file_xml(xml_str: str) -> dict:
    try:
        root = ET.fromstring(xml_str)
        appmsg = root.find("appmsg")
        if appmsg is None:
            return {}
        appattach = appmsg.find("appattach")
        if appattach is None:
            return {}
        return {
            "title": appmsg.findtext("title") or "",
            "fileext": appattach.findtext("fileext") or "",
            "totallen": appattach.findtext("totallen") or "",
            "attachid": appattach.findtext("attachid") or "",
            "cdnattachurl": appattach.findtext("cdnattachurl") or "",
            "aeskey": appattach.findtext("aeskey") or "",
            "md5": appmsg.findtext("md5") or "",
            "overwrite_newmsgid": appattach.findtext("overwrite_newmsgid") or "",
        }
    except Exception:
        return {}


async def _cdn_download_img(file_id: str, aes_key: str, save_path: str, img_type: int = 2):
    if not file_id or not aes_key:
        return False, "missing file_id/aes_key"
    payload = {
        "type": 73,
        "img_type": img_type,
        "file_id": file_id,
        "aes_key": aes_key,
        "save_path": save_path,
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(WECHAT_API_URL, json=payload)
            txt = r.text[:500]
            print(f"CDN下载图片状态: {r.status_code} {txt}")
            return r.status_code == 200, f"{r.status_code}:{txt}"
    except Exception as e:
        print(f"CDN下载图片失败: {e}")
        return False, str(e)


async def _cdn_download_file(file_id: str, aes_key: str, save_path: str):
    if not file_id or not aes_key:
        return False, "missing file_id/aes_key"
    payload = {
        "type": 75,
        "file_id": file_id,
        "aes_key": aes_key,
        "save_path": save_path,
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(WECHAT_API_URL, json=payload)
            txt = r.text[:500]
            print(f"CDN下载文件状态: {r.status_code} {txt}")
            return r.status_code == 200, f"{r.status_code}:{txt}"
    except Exception as e:
        print(f"CDN下载文件失败: {e}")
        return False, str(e)


async def process_message(body: dict, source: str = "api") -> dict:
    print(f"[{source.upper()}] 接收到的消息:", body)

    if body.get("type") == 8:
        path = body.get("path", "")
        if is_base64_image(path):
            local_path = save_base64_image(path)
            if local_path:
                body["path"] = local_path
            else:
                return {"status": "error", "message": "Base64图片保存失败"}
        elif is_image_url(path):
            local_path = await download_image(path)
            if local_path:
                body["path"] = local_path
            else:
                return {"status": "error", "message": "图片下载失败"}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(WECHAT_API_URL, json=body)
            print(f"转发响应状态: {response.status_code}")
            try:
                response_data = response.json()
            except:
                response_data = response.text

            if manager.get_connection_count() > 0:
                await manager.broadcast({
                    "type": "message_processed",
                    "source": source,
                    "data": body,
                    "forward_status": response.status_code
                })

            return {
                "status": "success",
                "message": "消息已接收并转发",
                "source": source,
                "data": body,
                "forward_status": response.status_code,
                "forward_response": response_data
            }
    except Exception as forward_error:
        print(f"转发消息时出错: {forward_error}")
        return {
            "status": "partial_success",
            "message": f"消息已接收但转发失败: {str(forward_error)}",
            "source": source,
            "data": body
        }


# ==================== HTTP API 端点 ====================
@app.post("/api")
async def receive_message(request: Request):
    try:
        body = await request.json()
        return await process_message(body, source="api")
    except Exception as e:
        print(f"处理消息时出错: {e}")
        return {"status": "error", "message": str(e)}


@app.get("/")
async def read_root():
    return {
        "message": "FastAPI 服务正在运行（支持API和WebSocket）",
        "endpoints": {
            "api": "POST /api",
            "xml": "POST /xml",
            "msg": "POST /msg",
            "websocket": "WS /ws/{client_id}",
            "ws_status": "GET /ws/status"
        },
        "websocket_connections": manager.get_connection_count()
    }


@app.post("/xml")
async def receive_xml(request: Request):
    """接收原始XML（公众号/回调），解析后广播到WS"""
    try:
        raw = await request.body()
        xml_str = raw.decode("utf-8", errors="ignore").strip()
        if not xml_str:
            return PlainTextResponse("empty", status_code=400)

        root = ET.fromstring(xml_str)
        def _t(tag):
            el = root.find(tag)
            return el.text if el is not None else None

        msg_type_text = (_t("MsgType") or "").lower()
        msg_type_map = {
            "text": 1,
            "image": 3,
            "voice": 34,
            "video": 43,
            "shortvideo": 43,
            "location": 48,
            "link": 492,
            "file": 493,
            "event": 10000,
        }
        msg_type = msg_type_map.get(msg_type_text, 49)

        data = {
            "type": 100,
            "msg_type": msg_type,
            "wx_id": _t("FromUserName"),
            "content": _t("Content"),
            "sender": _t("FromUserName"),
            "time_stamp": int(_t("CreateTime") or 0),
            "is_self_msg": 0,
            "is_pc_msg": 0,
            "xml_content": xml_str,
            "url": _t("PicUrl"),
            "media_id": _t("MediaId"),
            "msg_id": _t("MsgId"),
            "event": _t("Event"),
            "event_key": _t("EventKey"),
        }

        if manager.get_connection_count() > 0:
            await manager.broadcast({
                "type": "msg_received",
                "data": data,
                "timestamp": datetime.now().isoformat()
            })

        return PlainTextResponse("success")
    except Exception as e:
        print(f"XML处理失败: {e}")
        return PlainTextResponse("error", status_code=500)


# 转发目标配置（可根据需要修改，为空则不转发）
MSG_FORWARD_TARGETS = [
    # "https://ai.livetools.top/wechat/msg",
    # "http://localhost:8069/wechat/msg",
]




MSG_FORWARD_TARGETS = [
    # "https://ai.livetools.top/wechat/msg",
    # "http://localhost:8069/wechat/msg",
]


@app.post("/msg")
async def rec_msg(data: dict):
    """处理接收到的消息并转发到配置的目标"""
    print(f"收到消息: {data}")

    # 尝试处理图片XML -> CDN下载得到本地路径
    if data.get("msg_type") == 3 and isinstance(data.get("content"), str) and "<img" in data.get("content", ""):
        meta = _parse_img_xml(data.get("content", ""))
        file_id = meta.get("cdnmidimgurl") or meta.get("cdnthumburl")
        aes_key = meta.get("aeskey")
        if file_id and aes_key and not data.get("path"):
            msg_id = data.get("msg_id") or uuid.uuid4().hex
            save_path = os.path.join(IMG_DOWNLOAD_DIR, f"{msg_id}.jpg")
            api_save_path = save_path
            if WINDOWS_SAVE_DIR:
                api_save_path = os.path.join(WINDOWS_SAVE_DIR, f"{msg_id}.jpg")
            ok, detail = await _cdn_download_img(file_id, aes_key, api_save_path, img_type=2)
            if ok and os.path.exists(save_path):
                data["path"] = save_path
                data["cdn_file_id"] = file_id
                if PUBLIC_BASE_URL:
                    data["url"] = f"{PUBLIC_BASE_URL.rstrip('/')}/files/{os.path.basename(save_path)}"
                print(f"CDN图片已保存: {save_path}")
            else:
                data["cdn_error"] = detail

    # 尝试处理文件XML -> CDN下载得到本地路径
    if data.get("msg_type") == 49 and isinstance(data.get("content"), str) and "<appmsg" in data.get("content", ""):
        meta = _parse_file_xml(data.get("content", ""))
        file_id = meta.get("cdnattachurl")
        aes_key = meta.get("aeskey")
        title = meta.get("title") or "file"
        ext = meta.get("fileext") or "dat"
        if file_id and aes_key and not data.get("path"):
            msg_id = meta.get("overwrite_newmsgid") or data.get("msg_id") or uuid.uuid4().hex
            filename = f"{msg_id}.{ext}"
            save_path = os.path.join(IMG_DOWNLOAD_DIR, filename)
            api_save_path = save_path
            if WINDOWS_SAVE_DIR:
                api_save_path = os.path.join(WINDOWS_SAVE_DIR, filename)
            ok, detail = await _cdn_download_file(file_id, aes_key, api_save_path)
            if ok and os.path.exists(save_path):
                data["path"] = save_path
                data["cdn_file_id"] = file_id
                data["file_name"] = title
                if PUBLIC_BASE_URL:
                    data["url"] = f"{PUBLIC_BASE_URL.rstrip('/')}/files/{os.path.basename(save_path)}"
                print(f"CDN文件已保存: {save_path}")
            else:
                data["cdn_error"] = detail

    forward_results = []

    # 先广播给 WebSocket 客户端
    if manager.get_connection_count() > 0:
        await manager.broadcast({
            "type": "msg_received",
            "data": data,
            "timestamp": datetime.now().isoformat()
        })
        print(f"[WebSocket] 消息已广播给 {manager.get_connection_count()} 个客户端")
    
    # 如果没有配置转发目标，直接返回成功
    if not MSG_FORWARD_TARGETS:
        return {
            "status": "success", 
            "message": "消息已接收（无转发目标配置）",
            "websocket_broadcast": manager.get_connection_count() > 0
        }
    
    # 转发到所有配置的目标
    async with httpx.AsyncClient(timeout=10) as client:
        for target_url in MSG_FORWARD_TARGETS:
            try:
                response = await client.post(target_url, json=data)
                print(f"转发到 {target_url} 成功，状态码: {response.status_code}")
                forward_results.append({
                    "url": target_url,
                    "status": "success",
                    "status_code": response.status_code
                })
            except Exception as e:
                print(f"转发到 {target_url} 失败: {e}")
                forward_results.append({
                    "url": target_url,
                    "status": "failed",
                    "error": str(e)
                })
    
    # 统计成功/失败数量
    success_count = sum(1 for r in forward_results if r["status"] == "success")
    
    if success_count == len(MSG_FORWARD_TARGETS):
        return {"status": "success", "message": "消息已接收并全部转发成功", "results": forward_results}
    elif success_count > 0:
        return {"status": "partial_success", "message": "消息已接收，部分转发成功", "results": forward_results}
    else:
        return {"status": "forward_failed", "message": "消息已接收但转发全部失败", "results": forward_results}


# ==================== WebSocket 端点 ====================
@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    await manager.connect(websocket, client_id)
    
    await manager.send_personal_message({
        "type": "connected",
        "client_id": client_id,
        "message": "WebSocket连接成功",
        "timestamp": datetime.now().isoformat()
    }, client_id)
    
    try:
        while True:
            data = await websocket.receive_text()
            try:
                message = json.loads(data)
                print(f"[WebSocket] 收到来自 {client_id} 的消息: {message}")
                
                msg_type = message.get("type", "message")
                
                if msg_type == "ping":
                    await manager.send_personal_message({
                        "type": "pong",
                        "timestamp": datetime.now().isoformat()
                    }, client_id)
                
                elif msg_type == "api":
                    result = await process_message(message.get("data", {}), source="websocket")
                    await manager.send_personal_message({
                        "type": "api_response",
                        "request_id": message.get("request_id"),
                        "result": result
                    }, client_id)
                
                elif msg_type == "broadcast":
                    await manager.broadcast({
                        "type": "broadcast",
                        "from": client_id,
                        "message": message.get("message"),
                        "timestamp": datetime.now().isoformat()
                    })
                
                else:
                    result = await process_message(message, source="websocket")
                    await manager.send_personal_message({
                        "type": "response",
                        "result": result
                    }, client_id)
                    
            except json.JSONDecodeError:
                await manager.send_personal_message({
                    "type": "error",
                    "message": "无效的JSON格式"
                }, client_id)
                
    except WebSocketDisconnect:
        manager.disconnect(client_id)
    except Exception as e:
        print(f"[WebSocket] 错误 {client_id}: {e}")
        manager.disconnect(client_id)


@app.websocket("/ws")
async def websocket_endpoint_auto(websocket: WebSocket):
    client_id = str(uuid.uuid4())[:8]
    await websocket_endpoint(websocket, client_id)


@app.get("/ws/status")
async def websocket_status():
    return {
        "connection_count": manager.get_connection_count(),
        "clients": manager.get_all_clients()
    }


@app.post("/ws/broadcast")
async def broadcast_message(data: dict):
    if manager.get_connection_count() == 0:
        return {"status": "warning", "message": "没有活跃的WebSocket连接"}
    
    await manager.broadcast({
        "type": "api_broadcast",
        "data": data,
        "timestamp": datetime.now().isoformat()
    })
    
    return {"status": "success", "message": f"消息已广播给 {manager.get_connection_count()} 个客户端"}


if __name__ == "__main__":
    print("启动FastAPI服务，监听端口9000...")
    print("支持: HTTP API + WebSocket")
    print("  - POST /api, POST /msg")
    print("  - WS /ws/{client_id}")
    print("  - GET /ws/status")
    uvicorn.run(app, host="0.0.0.0", port=9000)
