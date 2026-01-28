#!/usr/bin/env python3
"""
WebSocket 和 API 测试脚本
用于测试 main.py 的 WebSocket 和 HTTP API 功能
"""

import asyncio
import httpx
import json
import sys
from datetime import datetime

# 尝试导入 websockets，如果没有安装则提示
try:
    import websockets
except ImportError:
    print("请先安装 websockets: pip install websockets")
    sys.exit(1)

# 服务器地址配置
SERVER_HOST = "localhost"
SERVER_PORT = 9000
HTTP_BASE_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"
WS_BASE_URL = f"ws://{SERVER_HOST}:{SERVER_PORT}"


def print_header(title: str):
    """打印标题"""
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def print_result(success: bool, message: str):
    """打印测试结果"""
    status = "✅ 成功" if success else "❌ 失败"
    print(f"{status}: {message}")


async def test_http_root():
    """测试 HTTP GET / 端点"""
    print_header("测试 HTTP GET /")
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{HTTP_BASE_URL}/")
            data = response.json()
            print(f"响应状态码: {response.status_code}")
            print(f"响应内容: {json.dumps(data, ensure_ascii=False, indent=2)}")
            print_result(response.status_code == 200, "HTTP GET / 正常工作")
            return True
    except Exception as e:
        print_result(False, f"HTTP GET / 失败: {e}")
        return False


async def test_http_api():
    """测试 HTTP POST /api 端点"""
    print_header("测试 HTTP POST /api")
    try:
        test_data = {
            "type": 1,
            "wxid": "test_user",
            "content": "这是一条API测试消息",
            "timestamp": datetime.now().isoformat()
        }
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(f"{HTTP_BASE_URL}/api", json=test_data)
            print(f"发送数据: {json.dumps(test_data, ensure_ascii=False)}")
            print(f"响应状态码: {response.status_code}")
            print(f"响应内容: {response.text}")
            print_result(response.status_code == 200, "HTTP POST /api 正常工作")
            return True
    except Exception as e:
        print_result(False, f"HTTP POST /api 失败: {e}")
        return False


async def test_ws_status():
    """测试 WebSocket 状态端点"""
    print_header("测试 GET /ws/status")
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{HTTP_BASE_URL}/ws/status")
            data = response.json()
            print(f"响应状态码: {response.status_code}")
            print(f"当前连接数: {data.get('connection_count', 0)}")
            print(f"客户端列表: {data.get('clients', [])}")
            print_result(response.status_code == 200, "WebSocket 状态查询正常")
            return True
    except Exception as e:
        print_result(False, f"WebSocket 状态查询失败: {e}")
        return False


async def test_websocket_connection():
    """测试 WebSocket 连接和消息收发"""
    print_header("测试 WebSocket 连接和消息收发")
    client_id = "test_client_001"
    
    try:
        async with websockets.connect(f"{WS_BASE_URL}/ws/{client_id}") as ws:
            print(f"WebSocket 连接成功: {client_id}")
            
            # 1. 接收欢迎消息
            welcome = await asyncio.wait_for(ws.recv(), timeout=5)
            welcome_data = json.loads(welcome)
            print(f"收到欢迎消息: {json.dumps(welcome_data, ensure_ascii=False)}")
            print_result(welcome_data.get("type") == "connected", "收到连接确认消息")
            
            # 2. 测试心跳
            print("\n--- 测试心跳 (ping/pong) ---")
            await ws.send(json.dumps({"type": "ping"}))
            print("发送: ping")
            pong = await asyncio.wait_for(ws.recv(), timeout=5)
            pong_data = json.loads(pong)
            print(f"收到: {json.dumps(pong_data, ensure_ascii=False)}")
            print_result(pong_data.get("type") == "pong", "心跳响应正常")
            
            # 3. 测试 API 消息
            print("\n--- 测试通过 WebSocket 发送 API 消息 ---")
            api_msg = {
                "type": "api",
                "request_id": "req_001",
                "data": {
                    "type": 1,
                    "wxid": "ws_test_user",
                    "content": "通过WebSocket发送的API消息"
                }
            }
            await ws.send(json.dumps(api_msg))
            print(f"发送: {json.dumps(api_msg, ensure_ascii=False)}")
            
            try:
                response = await asyncio.wait_for(ws.recv(), timeout=10)
                response_data = json.loads(response)
                print(f"收到响应: {json.dumps(response_data, ensure_ascii=False, indent=2)}")
                print_result(True, "API 消息通过 WebSocket 发送成功")
            except asyncio.TimeoutError:
                print_result(False, "等待API响应超时")
            
            print("\nWebSocket 连接测试完成")
            
        print_result(True, "WebSocket 连接和消息收发测试完成")
        return True
        
    except Exception as e:
        print_result(False, f"WebSocket 测试失败: {e}")
        return False


async def run_all_tests():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("  微信消息服务 - API & WebSocket 测试")
    print(f"  服务器: {HTTP_BASE_URL}")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    results = {}
    
    results["HTTP GET /"] = await test_http_root()
    results["HTTP POST /api"] = await test_http_api()
    results["WebSocket 状态"] = await test_ws_status()
    results["WebSocket 连接和收发"] = await test_websocket_connection()
    
    print_header("测试汇总")
    passed = sum(1 for r in results.values() if r)
    failed = len(results) - passed
    
    for test_name, result in results.items():
        status = "✅ 通过" if result else "❌ 失败"
        print(f"  {status}: {test_name}")
    
    print(f"\n总计: {len(results)} 项, 通过: {passed}, 失败: {failed}")
    return failed == 0


async def listen_websocket():
    """持续监听 WebSocket 消息"""
    client_id = f"listener_{datetime.now().strftime('%H%M%S')}"
    print(f"开始监听，客户端ID: {client_id}")
    print("按 Ctrl+C 退出")
    try:
        async with websockets.connect(f"{WS_BASE_URL}/ws/{client_id}") as ws:
            while True:
                msg = await ws.recv()
                data = json.loads(msg)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] {json.dumps(data, ensure_ascii=False)}")
    except KeyboardInterrupt:
        print("\n已停止监听")


def main():
    print("\n微信消息服务测试工具")
    print("-" * 40)
    print("1. 运行所有测试")
    print("2. 仅测试 HTTP API")
    print("3. 仅测试 WebSocket")
    print("4. 持续监听 WebSocket 消息")
    print("-" * 40)
    
    choice = input("请选择 (1-4): ").strip()
    
    if choice == "1":
        asyncio.run(run_all_tests())
    elif choice == "2":
        async def http_tests():
            await test_http_root()
            await test_http_api()
        asyncio.run(http_tests())
    elif choice == "3":
        async def ws_tests():
            await test_ws_status()
            await test_websocket_connection()
        asyncio.run(ws_tests())
    elif choice == "4":
        asyncio.run(listen_websocket())
    else:
        asyncio.run(run_all_tests())


if __name__ == "__main__":
    main()
