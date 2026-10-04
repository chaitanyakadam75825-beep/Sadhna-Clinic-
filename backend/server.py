from fastapi import FastAPI, APIRouter, HTTPException, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
import os, uuid, hmac, jwt

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")
client = AsyncIOMotorClient(os.environ["MONGO_URL"])
db = client[os.environ["DB_NAME"]]
app = FastAPI(title="Sadhana Clinic Queue API")
api = APIRouter(prefix="/api")
bearer = HTTPBearer(auto_error=False)

SLOTS = {"morning": ["11:00 AM", "11:30 AM", "12:00 PM", "12:30 PM"], "evening": ["07:00 PM", "07:30 PM", "08:00 PM", "08:30 PM"]}
FEES = {"first": (500, 50, 450), "followup": (400, 40, 360)}

class Booking(BaseModel):
    patient_name: str = Field(min_length=2)
    phone: str = Field(min_length=10)
    appointment_date: str
    batch: str
    time_slot: str
    visit_type: str

class PinCheck(BaseModel):
    pin: str

class StatusUpdate(BaseModel):
    status: str

class PrescriptionIn(BaseModel):
    appointment_id: str
    diagnosis: str
    medications: str
    diet_advice: str = ""
    follow_up_date: str = ""

class WalkIn(BaseModel):
    patient_name: str
    phone: str
    visit_type: str = "first"
    appointment_date: str

class DelayIn(BaseModel):
    minutes: int

def issue_token(role: str):
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": f"clinic-{role}", "role": role, "iat": now, "exp": now.timestamp() + 600}, os.environ["JWT_SECRET"], algorithm="HS256")

def require_role(role: str):
    async def dependency(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
        if not credentials:
            raise HTTPException(401, "Authentication required")
        try:
            claims = jwt.decode(credentials.credentials, os.environ["JWT_SECRET"], algorithms=["HS256"])
        except jwt.PyJWTError:
            raise HTTPException(401, "Invalid or expired token")
        if claims.get("role") != role:
            raise HTTPException(403, "Insufficient permissions")
        return claims
    return dependency

StaffAuth = Depends(require_role("staff"))
DoctorAuth = Depends(require_role("doctor"))

def clean(doc):
    doc.pop("_id", None)
    return doc

def schedule_for(date_value: str):
    try:
        day = datetime.strptime(date_value, "%Y-%m-%d").weekday()
    except ValueError:
        raise HTTPException(400, "Invalid date")
    if day == 6:
        return {"closed": True, "notice": "रविवार बंद (Emergency cases only via 9309748137)", "batches": {}}
    batches = {"morning": SLOTS["morning"], "evening": SLOTS["evening"]}
    if day == 3:
        batches.pop("morning")
    return {"closed": False, "notice": "गुरुवारी सकाळची वेळ उपलब्ध नाही" if day == 3 else "", "batches": batches}

@api.get("/")
async def root(): return {"service": "Sadhana Clinic Queue", "status": "ok"}

@api.get("/schedule/{date_value}")
async def get_schedule(date_value: str):
    info = schedule_for(date_value)
    delay = await db.schedule_overrides.find_one({"slot_date": date_value}, {"_id": 0})
    info["delay_minutes"] = (delay or {}).get("delay_minutes", 0)
    booked = await db.appointments.find({"appointment_date": date_value, "status": {"$ne": "no_show"}}, {"_id": 0, "time_slot": 1}).to_list(1000)
    info["booked_slots"] = [x["time_slot"] for x in booked]
    return info

@api.post("/appointments")
async def create_appointment(payload: Booking):
    schedule = schedule_for(payload.appointment_date)
    if schedule["closed"] or payload.batch not in schedule["batches"] or payload.time_slot not in schedule["batches"][payload.batch]:
        raise HTTPException(400, schedule.get("notice") or "This slot is unavailable")
    if await db.appointments.find_one({"appointment_date": payload.appointment_date, "time_slot": payload.time_slot, "status": {"$ne": "no_show"}}, {"_id": 0}):
        raise HTTPException(409, "This slot has just been booked")
    total, advance, balance = FEES.get(payload.visit_type, (0, 0, 0))
    if not total: raise HTTPException(400, "Invalid visit type")
    appointment = {"id": str(uuid.uuid4()), "token_number": "", **payload.model_dump(), "total_fee": total, "advance_paid": 0, "advance_due": advance, "balance_due": balance, "balance_collected": False, "status": "payment_pending", "created_at": datetime.now(timezone.utc).isoformat()}
    await db.appointments.insert_one(appointment.copy())
    return clean(appointment)

@api.post("/appointments/{appointment_id}/verify-payment")
async def verify_payment(appointment_id: str):
    appt = await db.appointments.find_one({"id": appointment_id}, {"_id": 0})
    if not appt: raise HTTPException(404, "Appointment not found")
    count = await db.appointments.count_documents({"appointment_date": appt["appointment_date"], "status": {"$nin": ["no_show", "payment_pending"]}})
    token = f"T-{count + 1:02d}"
    await db.appointments.update_one({"id": appointment_id}, {"$set": {"token_number": token, "advance_paid": appt["advance_due"], "status": "booked"}})
    appt.update({"token_number": token, "advance_paid": appt["advance_due"], "status": "booked"})
    return clean(appt)

@api.get("/appointments/{appointment_id}")
async def get_appointment(appointment_id: str):
    appt = await db.appointments.find_one({"id": appointment_id}, {"_id": 0})
    if not appt: raise HTTPException(404, "Appointment not found")
    prescription = await db.prescriptions.find_one({"appointment_id": appointment_id}, {"_id": 0})
    appt["prescription"] = prescription
    delay = await db.schedule_overrides.find_one({"slot_date": appt["appointment_date"]}, {"_id": 0})
    waiting = await db.appointments.count_documents({"appointment_date": appt["appointment_date"], "status": {"$in": ["booked", "arrived"]}, "id": {"$ne": appointment_id}})
    appt["estimated_wait_minutes"] = waiting * 15 + (delay or {}).get("delay_minutes", 0)
    return appt

@api.post("/staff/verify")
async def verify_staff(payload: PinCheck):
    if not hmac.compare_digest(payload.pin, os.environ["STAFF_PIN"]): raise HTTPException(401, "Incorrect desk PIN")
    return {"ok": True, "access_token": issue_token("staff"), "token_type": "bearer"}

@api.post("/doctor/verify")
async def verify_doctor(payload: PinCheck):
    if not hmac.compare_digest(payload.pin, os.environ["DOCTOR_PIN"]): raise HTTPException(401, "Incorrect doctor PIN")
    return {"ok": True, "access_token": issue_token("doctor"), "token_type": "bearer"}

@api.get("/queue/{date_value}")
async def get_queue(date_value: str, _auth=StaffAuth):
    items = await db.appointments.find({"appointment_date": date_value, "status": {"$ne": "payment_pending"}}, {"_id": 0}).sort("created_at", 1).to_list(500)
    return {"appointments": items, "delay_minutes": (await db.schedule_overrides.find_one({"slot_date": date_value}, {"_id": 0}) or {}).get("delay_minutes", 0)}

@api.patch("/appointments/{appointment_id}/status")
async def update_status(appointment_id: str, payload: StatusUpdate, _auth=StaffAuth):
    if payload.status not in {"booked", "arrived", "in_consultation", "completed", "no_show"}: raise HTTPException(400, "Invalid status")
    result = await db.appointments.update_one({"id": appointment_id}, {"$set": {"status": payload.status}})
    if not result.matched_count: raise HTTPException(404, "Appointment not found")
    return await get_appointment(appointment_id)

@api.patch("/appointments/{appointment_id}/balance")
async def collect_balance(appointment_id: str, _auth=StaffAuth):
    result = await db.appointments.update_one({"id": appointment_id}, {"$set": {"balance_collected": True, "balance_due": 0}})
    if not result.matched_count: raise HTTPException(404, "Appointment not found")
    return await get_appointment(appointment_id)

@api.post("/walk-ins")
async def create_walkin(payload: WalkIn, _auth=StaffAuth):
    total, advance, balance = FEES[payload.visit_type]
    count = await db.appointments.count_documents({"appointment_date": payload.appointment_date, "status": {"$nin": ["no_show", "payment_pending"]}})
    doc = {"id": str(uuid.uuid4()), "token_number": f"T-{count+1:02d}", "patient_name": payload.patient_name, "phone": payload.phone, "appointment_date": payload.appointment_date, "time_slot": "Walk-in", "batch": "walk-in", "visit_type": payload.visit_type, "total_fee": total, "advance_paid": advance, "advance_due": advance, "balance_due": balance, "balance_collected": False, "status": "arrived", "created_at": datetime.now(timezone.utc).isoformat()}
    await db.appointments.insert_one(doc.copy())
    return clean(doc)

@api.post("/delay/{date_value}")
async def update_delay(date_value: str, payload: DelayIn, _auth=StaffAuth):
    delay = max(0, payload.minutes)
    await db.schedule_overrides.update_one({"slot_date": date_value}, {"$set": {"slot_date": date_value, "delay_minutes": delay, "updated_at": datetime.now(timezone.utc).isoformat()}}, upsert=True)
    return {"delay_minutes": delay}

@api.get("/active-consultation")
async def active_consultation(_auth=DoctorAuth):
    return await db.appointments.find_one({"status": "in_consultation"}, {"_id": 0}) or {}

@api.post("/prescriptions")
async def save_prescription(payload: PrescriptionIn, _auth=DoctorAuth):
    doc = {"id": str(uuid.uuid4()), **payload.model_dump(), "created_at": datetime.now(timezone.utc).isoformat()}
    await db.prescriptions.update_one({"appointment_id": payload.appointment_id}, {"$set": doc}, upsert=True)
    await db.appointments.update_one({"id": payload.appointment_id}, {"$set": {"status": "completed"}})
    return clean(doc)

@api.get("/records/{phone}")
async def get_records(phone: str):
    appointments = await db.appointments.find({"phone": phone}, {"_id": 0}).sort("appointment_date", -1).to_list(100)
    for item in appointments: item["prescription"] = await db.prescriptions.find_one({"appointment_id": item["id"]}, {"_id": 0})
    return appointments

app.include_router(api)
app.add_middleware(CORSMiddleware, allow_credentials=True, allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","), allow_methods=["*"], allow_headers=["*"])

@app.on_event("shutdown")
async def shutdown_db_client(): client.close()