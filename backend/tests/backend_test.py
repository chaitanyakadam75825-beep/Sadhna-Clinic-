import os
import uuid
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://sadhana-clinic-queue.preview.emergentagent.com").rstrip("/")


def test_schedule_rules():
    sunday = requests.get(f"{BASE_URL}/api/schedule/2026-03-08")
    assert sunday.status_code == 200
    assert sunday.json()["closed"] is True
    thursday = requests.get(f"{BASE_URL}/api/schedule/2026-03-05")
    assert thursday.status_code == 200
    assert "morning" not in thursday.json()["batches"]
    normal = requests.get(f"{BASE_URL}/api/schedule/2026-03-04")
    assert normal.status_code == 200
    assert len(normal.json()["batches"]["morning"]) == 4


def test_booking_payment_queue_and_records_flow():
    phone = "9" + uuid.uuid4().hex[:9]
    payload = {"patient_name": "TEST Patient", "phone": phone, "appointment_date": "2026-03-04", "batch": "morning", "time_slot": "11:00 AM", "visit_type": "first"}
    create = requests.post(f"{BASE_URL}/api/appointments", json=payload)
    assert create.status_code == 200
    appt = create.json()
    assert appt["total_fee"] == 500 and appt["advance_due"] == 50 and appt["balance_due"] == 450
    paid = requests.post(f"{BASE_URL}/api/appointments/{appt['id']}/verify-payment")
    assert paid.status_code == 200
    assert paid.json()["status"] == "booked" and paid.json()["token_number"].startswith("T-")
    queue = requests.get(f"{BASE_URL}/api/queue/2026-03-04")
    assert queue.status_code == 200
    assert any(x["id"] == appt["id"] for x in queue.json()["appointments"])
    for status in ("arrived", "in_consultation"):
        response = requests.patch(f"{BASE_URL}/api/appointments/{appt['id']}/status", json={"status": status})
        assert response.status_code == 200 and response.json()["status"] == status
    prescription = requests.post(f"{BASE_URL}/api/prescriptions", json={"appointment_id": appt["id"], "diagnosis": "TEST diagnosis", "medications": "TEST medicine", "diet_advice": "Rest"})
    assert prescription.status_code == 200
    assert requests.get(f"{BASE_URL}/api/appointments/{appt['id']}").json()["status"] == "completed"
    records = requests.get(f"{BASE_URL}/api/records/{phone}")
    assert records.status_code == 200
    assert records.json()[0]["prescription"]["diagnosis"] == "TEST diagnosis"


def test_pin_verification_and_error_json():
    assert requests.post(f"{BASE_URL}/api/staff/verify", json={"pin": "1122"}).json() == {"ok": True}
    assert requests.post(f"{BASE_URL}/api/doctor/verify", json={"pin": "3344"}).json() == {"ok": True}
    bad = requests.post(f"{BASE_URL}/api/staff/verify", json={"pin": "0000"})
    assert bad.status_code == 401 and "detail" in bad.json()