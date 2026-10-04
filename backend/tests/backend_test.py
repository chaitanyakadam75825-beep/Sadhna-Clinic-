import os
import uuid
from datetime import date, timedelta

import pytest
import requests
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")
BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"


# ---------- Helpers ----------
def _next_weekday(target):
    d = date.today() + timedelta(days=1)
    while d.weekday() != target:
        d += timedelta(days=1)
    return d.isoformat()


def _pick_free_slot(date_str, batch="morning"):
    sched = requests.get(f"{API}/schedule/{date_str}").json()
    booked = set(sched.get("booked_slots", []))
    for s in sched["batches"].get(batch, []):
        if s not in booked:
            return s
    return None


@pytest.fixture(scope="module")
def staff_token():
    r = requests.post(f"{API}/staff/verify", json={"pin": "1122"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True and data["token_type"] == "bearer"
    return data["access_token"]


@pytest.fixture(scope="module")
def doctor_token():
    r = requests.post(f"{API}/doctor/verify", json={"pin": "3344"})
    assert r.status_code == 200
    return r.json()["access_token"]


# ---------- Schedule rules ----------
def test_schedule_sunday_closed():
    # Monday=0 ... Sunday=6
    sunday = _next_weekday(6)
    r = requests.get(f"{API}/schedule/{sunday}")
    assert r.status_code == 200
    assert r.json()["closed"] is True


def test_schedule_thursday_no_morning():
    thursday = _next_weekday(3)
    r = requests.get(f"{API}/schedule/{thursday}")
    assert r.status_code == 200
    body = r.json()
    assert body["closed"] is False
    assert "morning" not in body["batches"]
    assert "evening" in body["batches"]


def test_schedule_normal_day_has_both_batches():
    tuesday = _next_weekday(1)
    r = requests.get(f"{API}/schedule/{tuesday}")
    assert r.status_code == 200
    body = r.json()
    assert body["closed"] is False
    assert len(body["batches"]["morning"]) == 4
    assert len(body["batches"]["evening"]) == 4


# ---------- Booking flow + fees ----------
def test_booking_first_visit_fees_and_token():
    appt_date = _next_weekday(1)  # Tuesday
    slot = _pick_free_slot(appt_date, "morning")
    assert slot, "No free morning slot available"
    payload = {
        "patient_name": "TEST First Visit",
        "phone": "9" + uuid.uuid4().hex[:9],
        "appointment_date": appt_date,
        "batch": "morning",
        "time_slot": slot,
        "visit_type": "first",
    }
    r = requests.post(f"{API}/appointments", json=payload)
    assert r.status_code == 200, r.text
    appt = r.json()
    assert appt["total_fee"] == 500
    assert appt["advance_due"] == 50
    assert appt["balance_due"] == 450
    assert appt["status"] == "payment_pending"

    paid = requests.post(f"{API}/appointments/{appt['id']}/verify-payment")
    assert paid.status_code == 200
    body = paid.json()
    assert body["status"] == "booked"
    assert body["token_number"].startswith("T-")
    assert body["advance_paid"] == 50

    get_r = requests.get(f"{API}/appointments/{appt['id']}")
    assert get_r.status_code == 200
    assert get_r.json()["status"] == "booked"


def test_booking_followup_fees():
    appt_date = _next_weekday(1)
    slot = _pick_free_slot(appt_date, "evening")
    assert slot
    payload = {
        "patient_name": "TEST Followup",
        "phone": "9" + uuid.uuid4().hex[:9],
        "appointment_date": appt_date,
        "batch": "evening",
        "time_slot": slot,
        "visit_type": "followup",
    }
    r = requests.post(f"{API}/appointments", json=payload)
    assert r.status_code == 200
    appt = r.json()
    assert appt["total_fee"] == 400
    assert appt["advance_due"] == 40
    assert appt["balance_due"] == 360


def test_duplicate_slot_rejected():
    appt_date = _next_weekday(1)
    slot = _pick_free_slot(appt_date, "morning")
    assert slot
    payload = {
        "patient_name": "TEST Dup A",
        "phone": "9" + uuid.uuid4().hex[:9],
        "appointment_date": appt_date,
        "batch": "morning",
        "time_slot": slot,
        "visit_type": "first",
    }
    r1 = requests.post(f"{API}/appointments", json=payload)
    assert r1.status_code == 200
    payload2 = {**payload, "patient_name": "TEST Dup B", "phone": "9" + uuid.uuid4().hex[:9]}
    r2 = requests.post(f"{API}/appointments", json=payload2)
    assert r2.status_code == 409


def test_sunday_booking_rejected():
    sunday = _next_weekday(6)
    payload = {
        "patient_name": "TEST Sun",
        "phone": "9" + uuid.uuid4().hex[:9],
        "appointment_date": sunday,
        "batch": "morning",
        "time_slot": "11:00 AM",
        "visit_type": "first",
    }
    r = requests.post(f"{API}/appointments", json=payload)
    assert r.status_code == 400


# ---------- JWT protection ----------
def test_queue_requires_auth():
    r = requests.get(f"{API}/queue/{_next_weekday(1)}")
    assert r.status_code == 401


def test_queue_rejects_doctor_token(doctor_token):
    r = requests.get(
        f"{API}/queue/{_next_weekday(1)}",
        headers={"Authorization": f"Bearer {doctor_token}"},
    )
    assert r.status_code == 403


def test_active_consultation_requires_doctor(staff_token):
    r = requests.get(
        f"{API}/active-consultation",
        headers={"Authorization": f"Bearer {staff_token}"},
    )
    assert r.status_code == 403


def test_walkin_requires_staff(doctor_token):
    r = requests.post(
        f"{API}/walk-ins",
        headers={"Authorization": f"Bearer {doctor_token}"},
        json={"patient_name": "x", "phone": "1234567890", "appointment_date": _next_weekday(1)},
    )
    assert r.status_code == 403


def test_bad_pin():
    r = requests.post(f"{API}/staff/verify", json={"pin": "0000"})
    assert r.status_code == 401


# ---------- Full staff + doctor workflow ----------
def test_full_e2e_workflow(staff_token, doctor_token):
    appt_date = _next_weekday(1)
    slot = _pick_free_slot(appt_date, "evening")
    assert slot
    phone = "9" + uuid.uuid4().hex[:9]
    create = requests.post(
        f"{API}/appointments",
        json={
            "patient_name": "TEST E2E",
            "phone": phone,
            "appointment_date": appt_date,
            "batch": "evening",
            "time_slot": slot,
            "visit_type": "first",
        },
    )
    assert create.status_code == 200
    appt_id = create.json()["id"]
    requests.post(f"{API}/appointments/{appt_id}/verify-payment").raise_for_status()

    staff_headers = {"Authorization": f"Bearer {staff_token}"}
    doc_headers = {"Authorization": f"Bearer {doctor_token}"}

    q = requests.get(f"{API}/queue/{appt_date}", headers=staff_headers)
    assert q.status_code == 200
    assert any(a["id"] == appt_id for a in q.json()["appointments"])

    for status in ("arrived", "in_consultation"):
        r = requests.patch(
            f"{API}/appointments/{appt_id}/status",
            headers=staff_headers,
            json={"status": status},
        )
        assert r.status_code == 200

    active = requests.get(f"{API}/active-consultation", headers=doc_headers)
    assert active.status_code == 200
    assert active.json().get("id") == appt_id

    bal = requests.patch(f"{API}/appointments/{appt_id}/balance", headers=staff_headers)
    assert bal.status_code == 200
    assert bal.json()["balance_collected"] is True

    rx = requests.post(
        f"{API}/prescriptions",
        headers=doc_headers,
        json={
            "appointment_id": appt_id,
            "diagnosis": "TEST diag",
            "medications": "TEST meds",
            "diet_advice": "rest",
        },
    )
    assert rx.status_code == 200

    final = requests.get(f"{API}/appointments/{appt_id}")
    assert final.json()["status"] == "completed"
    assert final.json()["prescription"]["diagnosis"] == "TEST diag"

    rec = requests.get(f"{API}/records/{phone}")
    assert rec.status_code == 200
    assert rec.json()[0]["prescription"]["medications"] == "TEST meds"


# ---------- Walk-in + delay ----------
def test_walkin_and_delay(staff_token):
    appt_date = _next_weekday(1)
    staff_headers = {"Authorization": f"Bearer {staff_token}"}
    walkin = requests.post(
        f"{API}/walk-ins",
        headers=staff_headers,
        json={
            "patient_name": "TEST Walkin",
            "phone": "9" + uuid.uuid4().hex[:9],
            "visit_type": "first",
            "appointment_date": appt_date,
        },
    )
    assert walkin.status_code == 200
    assert walkin.json()["token_number"].startswith("T-")
    assert walkin.json()["status"] == "arrived"

    delay = requests.post(
        f"{API}/delay/{appt_date}",
        headers=staff_headers,
        json={"minutes": 15},
    )
    assert delay.status_code == 200
    assert delay.json()["delay_minutes"] == 15
