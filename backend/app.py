import queue
from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from models import OrderItem, User, MenuItem ,Order , Canteen, PrepType ,Base
from database import get_db, engine
from fastapi import HTTPException,Body
from typing import List
from datetime import datetime,timezone, timedelta
from pydantic import BaseModel,EmailStr
from fastapi import WebSocket, WebSocketDisconnect
from websocket_manager import manager
from fastapi.middleware.cors import CORSMiddleware
from security import verify_password,hash_password
from schemas import UserCreate
import requests
import time
import asyncio
from schemas import BatchOrderCreate
# Ensure metadata is loaded (safe even if tables exist)

@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
AI_ENGINE_URL = "https://johnette-unmethodising-junita.ngrok-free.dev/"

def get_live_queue_data_for_canteen(canteen_id: int):
    try:
        response = requests.get(AI_ENGINE_URL, timeout=2)
        return response.json()
    except:
        return {
            "queue_count": 0,
            "average_service_seconds": 10
        }
def recalculate_eta(db: Session, canteen_id: int):

    # Get active kitchen orders in FIFO order
    orders = db.query(Order).filter(
        Order.canteen_id == canteen_id,
        Order.status.in_(["PLACED", "PREPARING"])
    ).order_by(Order.created_at.asc()).all()

    now_ts = int(time.time())
    cumulative_delay = 0
    updated_etas = []

    for order in orders:

        # Get items for this order
        items = (
            db.query(OrderItem, MenuItem)
            .join(MenuItem, MenuItem.id == OrderItem.menu_item_id)
            .filter(OrderItem.order_id == order.id)
            .all()
        )

        # Determine preparation time for this order
        prep_times = [menu.prep_time_seconds for _, menu in items]

        # If no items (edge case)
        order_prep_time = max(prep_times) if prep_times else 60

        # Total wait = cumulative previous + own prep time
        cumulative_delay += order_prep_time

        ready_at = now_ts + cumulative_delay

        order.estimated_wait_time = cumulative_delay
        order.estimated_ready_at = ready_at

        updated_etas.append({
            "order_id": order.id,
            "estimated_wait_time": cumulative_delay,
            "estimated_ready_at": ready_at
        })

    db.commit()

    return updated_etas

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # 🔥 Dev mode universal
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



IST = timezone(timedelta(hours=5, minutes=30))

def to_ist(dt):
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone(IST)
class LoginInput(BaseModel):
    student_id: str
    password: str

class LoginRequest(BaseModel):
    student_id: str
    password: str


class CanteenCreate(BaseModel):
    name: str
class OrderItemInput(BaseModel):
    menu_item_id: int
    quantity: int

class OrderCreate(BaseModel):
    user_id: int
    canteen_id: int
    items: List[OrderItemInput]
    payment_mode: str  # WALLET / UPI / CASH
    

class OrderStatusUpdate(BaseModel):
    order_id: int
    status: str   

@app.post("/internal/queue-update")
async def queue_update(data: dict = Body(...)):

    canteen_id = data["canteen_id"]
    queue_count = data["queue_count"]
    avg_sec = data["average_service_seconds"]

    db: Session = Depends(get_db)

    ready_orders = db.query(Order).filter(
        Order.canteen_id == canteen_id,
        Order.status == "READY"
    ).order_by(Order.created_at.asc()).all()

    now_ts = int(time.time())

    for index, order in enumerate(ready_orders):
        pickup_wait = (queue_count + index) * avg_sec
        ready_at = now_ts + pickup_wait

        order.estimated_wait_time = pickup_wait
        order.estimated_ready_at = ready_at

        await manager.broadcast(
            canteen_id,
            {
                "event": "PICKUP_QUEUE_UPDATE",
                "order_id": order.id,
                "people_in_line": queue_count,
                "estimated_ready_at": ready_at
            }
        )

    db.commit()
    return {"ok": True}

@app.get("/api/test/db")
def test_db(db: Session = Depends(get_db)):
    canteens = db.query(Canteen).all()
    return {"canteens": [c.name for c in canteens]}


# Create user API
@app.post("/users/create")
def create_user(user: UserCreate, db: Session = Depends(get_db)):

    # Check by student_id OR staff_id depending on role
    lookup_id = user.student_id if user.role == "student" else user.staff_id

    if not lookup_id:
        raise HTTPException(status_code=400, detail="ID is required")

    existing = db.query(User).filter(User.student_id == lookup_id).first()
    if existing:
        raise HTTPException(status_code=400, detail="User already exists")

    new_user = User(
        student_id  = lookup_id,           # store whichever ID was provided
        name        = user.name,
        email       = user.email,
        phone       = user.phone,
        department  = user.department,
        year        = user.year or "",     # optional now
        password    = hash_password(user.password),
        wallet_balance = 0.0,
        role        = user.role,           # use role from frontend
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return {"message": "User created successfully"}
# wallet payment API
@app.post("/wallet/pay")
def wallet_payment(
    student_id: str,
    amount: float,
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.student_id == student_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.wallet_balance < amount:
        raise HTTPException(status_code=400, detail="Insufficient balance")

    user.wallet_balance -= amount
    db.commit()

    return {
        "message": "Payment successful",
        "remaining_balance": user.wallet_balance
    }

# List users API
@app.get("/users")
def list_users(db: Session = Depends(get_db)):
    users = db.query(User).all()
    return users



@app.post("/canteens/create")
def create_canteen(
    canteen: CanteenCreate,
    db: Session = Depends(get_db)
):
    new_canteen = Canteen(
        name=canteen.name,
        is_active=True
    )
    db.add(new_canteen)
    db.commit()
    db.refresh(new_canteen)

    return {
        "message": "Canteen created",
        "canteen_id": new_canteen.id,
        "name": new_canteen.name
    }
#ALL CANTEENS

@app.get("/canteens")
def get_canteens(db: Session = Depends(get_db)):
    return db.query(Canteen).filter(Canteen.is_active == True).all()

# Create menu item API

@app.post("/menu/create")
def create_menu_item(
    name: str,
    price: float,
    stock: int,
    canteen_id: int,
    is_veg: bool,
    prep_type: PrepType = PrepType.RA,
    db: Session = Depends(get_db)
):
    
    # 🔥 AUTO ASSIGN PREP TIME
    if prep_type == PrepType.RA:
        prep_time = 60
    elif prep_type == PrepType.COOK:
        prep_time = 180
    else:
        prep_time = 60  # safety fallback

    item = MenuItem(
        name=name,
        price=price,
        stock=stock,
        canteen_id=canteen_id,
        is_veg=is_veg,
        prep_type=prep_type,
        prep_time_seconds=prep_time
    )

    db.add(item)
    db.commit()
    db.refresh(item)

    return {
        "message": "Menu item added",
        "item_id": item.id,
        "prep_type": prep_type,
        "prep_time_seconds": prep_time
    }
#VIEW MENU ITEMS FOR A CANTEEN
@app.get("/menu/{canteen_id}")
def get_menu(canteen_id: int, db: Session = Depends(get_db)):
    items = db.query(MenuItem).filter(
        MenuItem.canteen_id == canteen_id
    ).all()

    return [
        {
            "id": item.id,
            "name": item.name,
            "price": item.price,
            "stock": item.stock,
            "canteen_id": item.canteen_id,
            "is_veg": item.is_veg   # ✅ IMPORTANT
        }
        for item in items
    ]


@app.post("/order/place")
async def place_order(
    payload: BatchOrderCreate,
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == payload.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    created_orders = []

    for canteen_order in payload.canteens:

        total_amount = 0
        menu_cache = []

        for item in canteen_order.items:
            menu = db.query(MenuItem).filter(
                MenuItem.id == item.menu_item_id,
                MenuItem.canteen_id == canteen_order.canteen_id
            ).first()

            if not menu:
                raise HTTPException(status_code=404, detail="Menu item not found")

            if menu.stock < item.quantity:
                raise HTTPException(
                    status_code=400,
                    detail=f"{menu.name} out of stock"
                )

            total_amount += menu.price * item.quantity
            menu_cache.append((menu, item.quantity))

        if payload.payment_mode == "WALLET":
            if user.wallet_balance < total_amount:
                raise HTTPException(status_code=400, detail="Insufficient wallet balance")
            user.wallet_balance -= total_amount

        # Orders ahead
        orders_ahead = db.query(Order).filter(
            Order.canteen_id == canteen_order.canteen_id,
            Order.status.in_(["PLACED", "PREPARING"])
        ).count()

        # Live service rate
                # get max prep time for this order
        prep_times = [menu.prep_time_seconds for menu, _ in menu_cache]
        order_prep_time = max(prep_times) if prep_times else 60

        wait_seconds = (orders_ahead * order_prep_time) + order_prep_time
        # 🔥 DEFINE ready_at properly
        ready_at = int(time.time()) + wait_seconds
        order = Order(
            user_id=payload.user_id,
            canteen_id=canteen_order.canteen_id,
            status="PLACED",
            payment_mode=payload.payment_mode,
            estimated_wait_time=wait_seconds,
            estimated_ready_at=ready_at,
            order_type="COUNTER" if payload.payment_mode == "CASH" else "ONLINE"
        )

        db.add(order)
        db.commit()
        db.refresh(order)

        for menu, qty in menu_cache:
            db.add(OrderItem(
                order_id=order.id,
                menu_item_id=menu.item_id if hasattr(menu, 'item_id') else menu.id,
                quantity=qty
            ))
            menu.stock -= qty

        db.commit()

        print("🔥 Broadcasting NEW_ORDER:", order.id)
        await manager.broadcast(
            canteen_order.canteen_id,
            {
                "event": "NEW_ORDER",
                "order_id": order.id,
                "status": order.status,
                "estimated_wait_time": wait_seconds,
                "estimated_ready_at": ready_at
            }
        )

        created_orders.append({
            "order_id": order.id,
            "canteen_id": canteen_order.canteen_id,
            "estimated_wait_time": wait_seconds,
            "estimated_ready_at": ready_at
        })

    return {"orders": created_orders}


@app.put("/order/update-status")
async def update_order_status(
    payload: OrderStatusUpdate,
    db: Session = Depends(get_db)
):
    order = db.query(Order).filter(Order.id == payload.order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    order.status = payload.status.upper()
    db.commit()

    # 🔥 Recalculate and broadcast ETA updates for everyone in the queue
    updated_etas = recalculate_eta(db, order.canteen_id)
    for eta_info in updated_etas:
        await manager.broadcast(
            order.canteen_id,
            {
                "event": "ETA_UPDATE",
                "order_id": eta_info["order_id"],
                "estimated_wait_time": eta_info["estimated_wait_time"],
                "estimated_ready_at": eta_info["estimated_ready_at"]
            }
        )

    # 🔥 REAL-TIME PUSH for status update
    await manager.broadcast(
        order.canteen_id,
        {
            "event": "ORDER_STATUS_UPDATE",
            "order_id": order.id,
            "status": order.status
        }
    )

    if order.status == "DELIVERED":
        print(f"✅ Broadcasting DELIVERED for Order #{order.id}")
        await manager.broadcast(
            order.canteen_id,
            {
                "event": "ORDER_DELIVERED",
                "order_id": order.id
            }
        )

    return {
        "order_id": order.id,
        "status": order.status
    }




class WalletTopUp(BaseModel):
    user_id: int
    amount: float

@app.websocket("/ws/canteen/{canteen_id}")
async def websocket_endpoint(websocket: WebSocket, canteen_id: int):
    await manager.connect(canteen_id, websocket)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(canteen_id, websocket)
@app.get("/orders/canteen/{canteen_id}")
def get_orders_for_canteen(canteen_id: int, db: Session = Depends(get_db)):
    orders = (
        db.query(Order, User)
        .join(User, User.id == Order.user_id)
        .filter(
            Order.canteen_id == canteen_id,
            Order.status != "DELIVERED"
        )
        .order_by(Order.created_at.desc())
        .all()
    )

    response = []

    for order, user in orders:
        items = (
            db.query(OrderItem, MenuItem)
            .join(MenuItem, MenuItem.id == OrderItem.menu_item_id)
            .filter(OrderItem.order_id == order.id)
            .all()
        )

        canteen = db.query(Canteen).filter(Canteen.id == order.canteen_id).first()
        response.append({
            "order_id": order.id,
            "status": order.status,
            "payment_mode": order.payment_mode,
            "student_name": user.name,
            "canteen_name": canteen.name if canteen else "Canteen",
            "items": [
                {
                    "name": menu.name,
                    "price": menu.price,
                    "quantity": item.quantity
                }
                for item, menu in items
            ]
        })

    return response



# Delete canteen (with cascade delete for all related data)
@app.delete("/canteens/{canteen_id}")
async def delete_canteen(canteen_id: int, db: Session = Depends(get_db)):
    canteen = db.query(Canteen).filter(Canteen.id == canteen_id).first()
    
    if not canteen:
        raise HTTPException(status_code=404, detail="Canteen not found")
    
    # Get all menu items for this canteen
    menu_items = db.query(MenuItem).filter(MenuItem.canteen_id == canteen_id).all()
    menu_item_ids = [item.id for item in menu_items]
    
    # Step 1: Delete order items that reference these menu items
    if menu_item_ids:
        db.query(OrderItem).filter(OrderItem.menu_item_id.in_(menu_item_ids)).delete(synchronize_session=False)
    
    # Step 2: Delete orders for this canteen
    db.query(Order).filter(Order.canteen_id == canteen_id).delete()
    
    # Step 3: Delete menu items
    db.query(MenuItem).filter(MenuItem.canteen_id == canteen_id).delete()
    
    # Step 4: Delete the canteen
    db.delete(canteen)
    db.commit()
    
    return {"message": f"Canteen {canteen_id} and all related data deleted successfully"}


# Delete a single menu item
@app.delete("/menu/{menu_item_id}")
async def delete_menu_item(menu_item_id: int, db: Session = Depends(get_db)):
    menu_item = db.query(MenuItem).filter(MenuItem.id == menu_item_id).first()
    
    if not menu_item:
        raise HTTPException(status_code=404, detail="Menu item not found")
    
    # Delete order items that reference this menu item
    db.query(OrderItem).filter(OrderItem.menu_item_id == menu_item_id).delete()
    
    # Delete the menu item
    db.delete(menu_item)
    db.commit()
    
    return {"message": f"Menu item {menu_item_id} deleted successfully"}


# Delete a user
@app.delete("/users/{user_id}")
async def delete_user(user_id: int, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Get all orders for this user
    user_orders = db.query(Order).filter(Order.user_id == user_id).all()
    order_ids = [order.id for order in user_orders]
    
    # Delete order items for these orders
    if order_ids:
        db.query(OrderItem).filter(OrderItem.order_id.in_(order_ids)).delete(synchronize_session=False)
    
    # Delete orders
    db.query(Order).filter(Order.user_id == user_id).delete()
    
    # Delete the user
    db.delete(user)
    db.commit()
    
    return {"message": f"User {user_id} and all related data deleted successfully"}


# Delete an order
@app.delete("/orders/{order_id}")
async def delete_order(order_id: int, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.id == order_id).first()
    
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    # Delete order items first
    db.query(OrderItem).filter(OrderItem.order_id == order_id).delete()
    
    # Delete the order
    db.delete(order)
    db.commit()
    
    return {"message": f"Order {order_id} deleted successfully"}



@app.post("/users/login")
def login_user(
    data: LoginRequest,
    db: Session = Depends(get_db)
):

    user = db.query(User).filter(User.student_id == data.student_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Invalid student ID")

    if not verify_password(data.password, user.password):
        raise HTTPException(status_code=401, detail="Invalid password")

    return {
        "id": user.id,
        "student_id": user.student_id,
        "name": user.name,
        "email": user.email,
        "phone": user.phone,
        "department": user.department,
        "year": user.year,
        "role": user.role
    }
@app.put("/orders/clear/{canteen_id}")
async def clear_all_orders(canteen_id: int, db: Session = Depends(get_db)):
    orders = db.query(Order).filter(
        Order.canteen_id == canteen_id,
        Order.status != "DELIVERED"
    ).all()

    for order in orders:
        order.status = "DELIVERED"

    db.commit()

    # 🔥 Recalculate and broadcast ETA updates for remaining queue (if any)
    updated_etas = recalculate_eta(db, canteen_id)
    for eta_info in updated_etas:
        await manager.broadcast(
            canteen_id,
            {
                "event": "ETA_UPDATE",
                "order_id": eta_info["order_id"],
                "estimated_wait_time": eta_info["estimated_wait_time"],
                "estimated_ready_at": eta_info["estimated_ready_at"]
            }
        )

    return {
        "message": f"All orders for canteen {canteen_id} marked as DELIVERED",
        "count": len(orders)
    }
@app.get("/orders/user/history/{user_id}")
def get_user_order_history(user_id: int, db: Session = Depends(get_db)):
    orders = (
        db.query(Order)
        .filter(
            Order.user_id == user_id,
            Order.status == "DELIVERED"
        )
        .order_by(Order.created_at.desc())
        .all()
    )

    result = []
    for order in orders:
        canteen = db.query(Canteen).filter(Canteen.id == order.canteen_id).first()
        items = (
            db.query(OrderItem, MenuItem)
            .join(MenuItem, MenuItem.id == OrderItem.menu_item_id)
            .filter(OrderItem.order_id == order.id)
            .all()
        )

        order_total = sum(menu.price * item.quantity for item, menu in items)

        result.append({
            "order_id": order.id,
            "canteen_name": canteen.name if canteen else "Canteen",
            "payment_mode": order.payment_mode,
            "created_at": to_ist(order.created_at).isoformat(),
            "total_amount": order_total,
            "status": order.status,
            "items": [
                {
                    "name": menu.name,
                    "price": menu.price,
                    "quantity": item.quantity
                }
                for item, menu in items
            ]
        })

    return result
@app.get("/orders/history/{canteen_id}")
def get_order_history(canteen_id: int, db: Session = Depends(get_db)):
    canteen = db.query(Canteen).filter(Canteen.id == canteen_id).first()
    orders = (
        db.query(Order)
        .filter(
            Order.canteen_id == canteen_id,
            Order.status == "DELIVERED"
        )
        .order_by(Order.created_at.desc())
        .all()
    )

    result = []
    for order in orders:
        items = (
            db.query(OrderItem, MenuItem)
            .join(MenuItem, MenuItem.id == OrderItem.menu_item_id)
            .filter(OrderItem.order_id == order.id)
            .all()
        )

        result.append({
            "order_id": order.id,
            "canteen_name": canteen.name if canteen else "Canteen",
            "payment_mode": order.payment_mode,
            "created_at": to_ist(order.created_at).isoformat(),
            "items": [
                {
                    "name": menu.name,
                    "price": menu.price,
                    "quantity": item.quantity
                }
                for item, menu in items
            ]
        })

    return result

@app.put("/admin/order/status")
async def admin_update_order_status(
    order_id: int,
    status: str,
    db: Session = Depends(get_db)
):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    status = status.upper()
    if status not in ["PREPARING", "READY"]:
        raise HTTPException(status_code=400, detail="Invalid status")

    # 🔥 1️⃣ Update status in DB
    order.status = status
    db.commit()

    # 🔥 2️⃣ Immediately broadcast status change (NO WAIT)
    print("🔥 Broadcasting STATUS:", order.id, order.status)
    await manager.broadcast(
        order.canteen_id,
        {
            "event": "ORDER_STATUS_UPDATE",
            "order_id": order.id,
            "status": order.status
        }
    )

    # 🔥 3️⃣ Recalculate kitchen queue (non-blocking to user perception)
    updated_etas = recalculate_eta(db, order.canteen_id)

    for eta_info in updated_etas:
        await manager.broadcast(
            order.canteen_id,
            {
                "event": "ETA_UPDATE",
                "order_id": eta_info["order_id"],
                "estimated_wait_time": eta_info["estimated_wait_time"],
                "estimated_ready_at": eta_info["estimated_ready_at"]
            }
        )

    # 🔥 4️⃣ If READY → activate pickup (camera-based waiting)
    if status == "READY":

        try:
            queue_data = get_live_queue_data_for_canteen(order.canteen_id)

            people = queue_data.get("queue_count", 0)
            avg_sec = queue_data.get("average_service_seconds", 10)

            pickup_wait = int(people * avg_sec)
            ready_at = int(time.time()) + pickup_wait

            order.estimated_wait_time = pickup_wait
            order.estimated_ready_at = ready_at
            db.commit()

            await manager.broadcast(
                order.canteen_id,
                {
                    "event": "PICKUP_QUEUE_UPDATE",
                    "order_id": order.id,
                    "people_in_line": people,
                    "estimated_ready_at": ready_at
                }
            )

        except Exception as e:
            print("Camera queue fetch failed:", e)

    return {"success": True}

@app.put("/order/confirm-pickup")
async def confirm_pickup(
    order_id: int,
    db: Session = Depends(get_db)
):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(404, "Order not found")

    if order.status != "READY":
        raise HTTPException(400, "Order not ready for pickup")

    order.status = "DELIVERED"
    db.commit()

    # 🔥 Recalculate and broadcast ETA updates
    updated_etas = recalculate_eta(db, order.canteen_id)
    for eta_info in updated_etas:
        await manager.broadcast(
            order.canteen_id,
            {
                "event": "ETA_UPDATE",
                "order_id": eta_info["order_id"],
                "estimated_wait_time": eta_info["estimated_wait_time"],
                "estimated_ready_at": eta_info["estimated_ready_at"]
            }
        )

    await manager.broadcast(
        order.canteen_id,
        {
            "event": "ORDER_DELIVERED",
            "order_id": order.id
        }
    )

    return {"status": "DELIVERED"}
@app.get("/orders/user/{user_id}")
def get_user_active_orders(user_id: int, db: Session = Depends(get_db)):

    orders = (
        db.query(Order)
        .filter(
            Order.user_id == user_id,
            Order.status != "DELIVERED"
        )
        .order_by(Order.created_at.desc())
        .all()
    )

    queue_data = get_live_queue_data_for_canteen(order.canteen_id)
    avg_service_time = queue_data.get("average_service_seconds", 20)

    result = []

    for order in orders:

        orders_ahead = db.query(Order).filter(
            Order.canteen_id == order.canteen_id,
            Order.status.in_(["PLACED", "PREPARING"]),
            Order.created_at < order.created_at
        ).count()

        dynamic_estimate = int((orders_ahead + 1) * avg_service_time)
        ready_at = int(time.time()) + dynamic_estimate

        items = (
            db.query(OrderItem, MenuItem)
            .join(MenuItem, MenuItem.id == OrderItem.menu_item_id)
            .filter(OrderItem.order_id == order.id)
            .all()
        )

        result.append({
            "order_id": order.id,
            "canteen_id": order.canteen_id,
            "status": order.status,
            "estimated_wait_time": dynamic_estimate,
            "estimated_ready_at": order.estimated_ready_at or ready_at,
            "items": [
                {
                    "name": menu.name,
                    "quantity": item.quantity
                }
                for item, menu in items
            ]
        })

    return result
@app.get("/admin/stats/{canteen_id}")
def get_admin_stats(canteen_id: int, db: Session = Depends(get_db)):
    today = datetime.now(IST).date()

    orders = (
        db.query(Order)
        .filter(Order.canteen_id == canteen_id)
        .all()
    )

    today_orders = 0
    today_revenue = 0
    total_revenue = 0
    active_orders = 0

    for order in orders:
        items = (
            db.query(OrderItem, MenuItem)
            .join(MenuItem, MenuItem.id == OrderItem.menu_item_id)
            .filter(OrderItem.order_id == order.id)
            .all()
        )

        order_total = sum(menu.price * item.quantity for item, menu in items)
        total_revenue += order_total

        if order.status != "DELIVERED":
            active_orders += 1

        if to_ist(order.created_at).date() == today:
            today_orders += 1
            today_revenue += order_total

    return {
        "today_orders": today_orders,
        "today_revenue": today_revenue,
        "total_revenue": total_revenue,
        "active_orders": active_orders
    }


class StockUpdate(BaseModel):
    menu_item_id: int
    stock: int

@app.put("/menu/update-stock")
async def update_stock(data: StockUpdate, db: Session = Depends(get_db)):

    item = db.query(MenuItem).filter(MenuItem.id == data.menu_item_id).first()

    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    item.stock = data.stock
    db.commit()

    # 🔥 Broadcast realtime update
    await manager.broadcast(
        item.canteen_id,
        {
            "event": "STOCK_UPDATE",
            "menu_item_id": item.id,
            "stock": item.stock
        }
    )

    return {"message": "Stock updated"}

@app.get("/track-order/{order_id}")
def track_order(order_id: int, db: Session = Depends(get_db)):

    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    canteen = db.query(Canteen).filter(Canteen.id == order.canteen_id).first()

    items = (
        db.query(OrderItem, MenuItem)
        .join(MenuItem, MenuItem.id == OrderItem.menu_item_id)
        .filter(OrderItem.order_id == order.id)
        .all()
    )

    return {
        "order_id": order.id,
        "canteen_id": order.canteen_id,
        "canteen_name": canteen.name if canteen else "Canteen",
        "status": order.status,
        "estimated_wait_time": order.estimated_wait_time,
        "estimated_ready_at": order.estimated_ready_at,
        "items": [
            {
                "name": menu.name,
                "price": menu.price,
                "quantity": item.quantity
            }
            for item, menu in items
        ]
    }


@app.put("/admin/reset/{canteen_id}")
def reset_canteen(canteen_id: int, db: Session = Depends(get_db)):

    # Get order IDs first
    orders = db.query(Order).filter(
        Order.canteen_id == canteen_id
    ).all()

    order_ids = [o.id for o in orders]

    if order_ids:
        # Delete order items first
        db.query(OrderItem).filter(
            OrderItem.order_id.in_(order_ids)
        ).delete(synchronize_session=False)

    # Then delete orders
    db.query(Order).filter(
        Order.canteen_id == canteen_id
    ).delete(synchronize_session=False)

    db.commit()

    return {"message": "Canteen reset"}
@app.delete("/track-order/{order_id}")
def delete_track_order(order_id: int, db: Session = Depends(get_db)):

    order = db.query(Order).filter(Order.id == order_id).first()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # delete order items first
    db.query(OrderItem).filter(OrderItem.order_id == order_id).delete()

    # delete order
    db.delete(order)
    db.commit()

    return {"message": f"Track order {order_id} deleted"}

@app.delete("/track-order/clear/all")
def clear_all_track_orders(db: Session = Depends(get_db)):

    # delete order items
    db.query(OrderItem).delete()

    # delete orders
    db.query(Order).delete()

    db.commit()

    return {"message": "All track orders cleared"}


