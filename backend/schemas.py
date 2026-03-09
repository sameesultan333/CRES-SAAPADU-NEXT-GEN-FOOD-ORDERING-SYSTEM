from pydantic import BaseModel, EmailStr
from typing import Optional

class UserCreate(BaseModel):
    student_id: Optional[str] = None
    staff_id: Optional[str] = None
    name: str
    email: str
    phone: str
    dob: Optional[str] = None
    department: str
    year: Optional[str] = None
    designation: Optional[str] = None
    password: str
    role: str = "student"
    

class UserLogin(BaseModel):
    student_id: str
    password: str

from pydantic import BaseModel
from typing import List
class OrderItemInput(BaseModel):
    menu_item_id: int
    quantity: int


class BatchCanteenOrder(BaseModel):
    canteen_id: int
    items: List[OrderItemInput]

class BatchOrderCreate(BaseModel):
    user_id: int
    payment_mode: str
    canteens: List[BatchCanteenOrder]
