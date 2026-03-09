from typing import Dict, List
from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int, List[WebSocket]] = {}

    async def connect(self, canteen_id: int, websocket: WebSocket):
        await websocket.accept()

        if canteen_id not in self.active_connections:
            self.active_connections[canteen_id] = []

        self.active_connections[canteen_id].append(websocket)
        print(f"✅ WS Connected | Canteen {canteen_id} | Total: {len(self.active_connections[canteen_id])}")

    def disconnect(self, canteen_id: int, websocket: WebSocket):
        if canteen_id in self.active_connections:
            if websocket in self.active_connections[canteen_id]:
                self.active_connections[canteen_id].remove(websocket)

        print(f"❌ WS Disconnected | Canteen {canteen_id}")

    async def broadcast(self, canteen_id: int, message: dict):
        connections = self.active_connections.get(canteen_id, [])

        print(f"🔥 Broadcasting to {canteen_id} | Connections: {len(connections)}")

        dead_connections = []

        for websocket in connections:
            try:
                await websocket.send_json(message)
            except:
                dead_connections.append(websocket)

        for ws in dead_connections:
            self.active_connections[canteen_id].remove(ws)


# 🔥 THIS LINE IS CRITICAL
manager = ConnectionManager()