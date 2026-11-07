from datetime import date, timedelta
from typing import Literal
from uuid import uuid4
from pydantic import BaseModel, Field, model_validator


def identifier(prefix):
    return f"SIM-{prefix}-{uuid4().hex[:10]}"


class MealSlot(BaseModel):
    day: date
    meal: Literal["早餐","午餐","晚餐"]

    @property
    def key(self):
        return f"{self.day.isoformat()}:{self.meal}"


class BookingNeed(BaseModel):
    city: str = Field(min_length=1,max_length=100)
    check_in: date
    check_out: date
    men: int = Field(ge=0,le=2000)
    women: int = Field(ge=0,le=2000)
    meals: list[MealSlot] = Field(min_length=1,max_length=42)
    preferred_style: str = Field(default="", max_length=300)
    team_profile: str = Field(default="", max_length=100)
    desired_outcomes: list[str] = Field(default_factory=list)
    activity_notes: str = Field(default="", max_length=1000)
    daily_meal_budget_cents: int | None = Field(default=None,gt=0,le=1000000)
    hotel_grade: int = Field(default=2,ge=1,le=3)
    require_bonfire: bool = False
    require_fireworks: bool = False
    dietary: str = Field(default="荤素搭配，偏辣；保留清淡选择，过敏与忌口待组织者核实",max_length=1000)

    @model_validator(mode="after")
    def validate_need(self):
        if not 1 <= (self.check_out-self.check_in).days <= 14 or not 1 <= self.people <= 2000:
            raise ValueError("住宿需1至14晚，参与人数需1至2000人")
        if len({m.key for m in self.meals}) != len(self.meals):
            raise ValueError("同一天同一餐不能重复")
        if any(not self.check_in <= m.day <= self.check_out for m in self.meals):
            raise ValueError("餐次必须在本次活动日期内")
        return self

    @property
    def people(self):
        return self.men+self.women

    @property
    def rooms(self):
        return (self.men+1)//2+(self.women+1)//2

    @property
    def nights(self):
        return [(self.check_in+timedelta(days=i)).isoformat() for i in range((self.check_out-self.check_in).days)]


class Hotel(BaseModel):
    id: str = Field(min_length=1,max_length=80)
    name: str = Field(min_length=1,max_length=100)
    rooms_by_night: dict[str,int]
    room_rate_cents: int = Field(gt=0,le=10000000)
    grade: int = Field(ge=1,le=3)
    breakfast_included: bool = True
    plaza_capacity: int = Field(default=0,ge=0,le=10000)
    bonfire: Literal["支持","不支持","待核实"] = "待核实"
    fireworks: Literal["支持","不支持","待核实"] = "待核实"
    transfer_minutes: int = Field(default=20,ge=0,le=600)
    cancellation_terms: str = Field(default="模拟条款：取消、改期及退款需另行确认，不自动退款",max_length=400)


class Restaurant(BaseModel):
    id: str = Field(min_length=1,max_length=80)
    name: str = Field(min_length=1,max_length=100)
    seats_by_meal: dict[str,int]
    price_per_person_cents: int = Field(gt=0,le=1000000)
    menu: list[str] = Field(min_length=1,max_length=12)
    dietary_note: str = Field(max_length=1000)
    cancellation_terms: str = Field(default="模拟条款：人数、餐次调整及取消需提前确认，不自动退款",max_length=400)


class SupplierWorld(BaseModel):
    hotels: list[Hotel] = Field(min_length=2,max_length=4)
    restaurants: list[Restaurant] = Field(min_length=2,max_length=4)


class ContactAction(BaseModel):
    kind: Literal["hotel","restaurant"]
    supplier_id: str
    message: str = Field(min_length=1,max_length=400)
    meal_keys: list[str] = Field(default_factory=list,max_length=42)


class ContactPlan(BaseModel):
    summary: str = Field(max_length=500)
    actions: list[ContactAction] = Field(min_length=1,max_length=8)


class SupplierQuote(BaseModel):
    id: str
    kind: Literal["hotel","restaurant"]
    supplier_id: str
    supplier_name: str
    meal_keys: list[str] = Field(default_factory=list)
    total_cents: int
    capacity_ok: bool
    blockers: list[str] = Field(default_factory=list)
    conditions: dict[str,str] = Field(default_factory=dict)
    details: list[str] = Field(default_factory=list)
    inventory_signature: str
    valid_until_tick: int


class SupplierOrder(BaseModel):
    id: str = Field(default_factory=lambda: identifier("ORDER"))
    customer_id: str
    campaign_id: str | None = None
    case_id: str | None = None
    need: BookingNeed
    service_fee_cents: int = Field(ge=0,le=10000000)
    simulation_day: date
    status: str = "inquiring"
    world: SupplierWorld | None = None
    world_source: str = ""
    tick: int = 0
    quotes: list[SupplierQuote] = Field(default_factory=list)
    selected_quote_ids: list[str] = Field(default_factory=list)
    accepted_conditions: list[str] = Field(default_factory=list)
    customer_signature: str = ""
    coupon_id: str | None = None
    discount_cents: int = 0
    customer_paid_cents: int = 0
    supplier_paid_cents: int = 0
    confirmations: list[dict] = Field(default_factory=list)
    contacts: list[dict] = Field(default_factory=list)
    handoffs: list[dict] = Field(default_factory=list)
    delivered: bool = False
