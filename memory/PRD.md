# Sadhana Clinic OPD Queue — Product Record

## Original problem statement
Build a production-ready, full-stack Clinic Queue & Digital OPD Management Web App customized for साधना क्लिनिक (Sadhana Clinic), with patient booking, UPI advance flow, live tracking, staff desk, doctor prescriptions, and patient records.

## Architecture decisions
- React frontend with route-based patient, tracker, desk, doctor, and records experiences.
- FastAPI backend with MongoDB equivalent collections (`appointments`, `prescriptions`, `schedule_overrides`) and UUID-style IDs.
- Polling every five seconds for live status synchronization.
- PINs are configurable through backend environment variables and initialized to 1122 / 3344.
- Frontend uses the protected `REACT_APP_BACKEND_URL`; backend uses protected MongoDB variables.

## User personas
- Patient or family member booking and tracking an OPD token.
- Receptionist managing the day’s queue, walk-ins, payments, and delays.
- Doctor documenting consultations and issuing a digital prescription.

## Core requirements
- Sadhana Clinic branding, doctor profile, Marathi notices, mobile-first medical aesthetic.
- Weekday schedule rules, Sunday emergency-only notice, Thursday morning closure.
- First/follow-up pricing, UPI intent and dynamic QR, payment verification.
- Token confirmation, WhatsApp sharing, live tracker, queue actions, walk-ins, delay controls.
- Prescription creation/printing and phone-based historical records.

## Implemented (2026-10-04)
- Replaced starter demo with complete booking-to-payment-to-tracker flow.
- Added schedule validation, sequential tokens, UPI QR/intent, WhatsApp share, and polling tracker.
- Added protected desk and doctor portals, queue actions, walk-ins, balance collection, delay controls.
- Added prescription pad with print/save flow and phone-based records.
- Added responsive clinic visual system and required test IDs on user-facing controls.
- Added short-lived signed role tokens so staff and doctor mutations are protected server-side, not only in the UI.

## Prioritized backlog
- P0: Verify all core patient, desk, doctor, and records journeys in browser and API tests.
- P1: Replace payment self-verification with a payment-provider webhook when the clinic enables live payments.
- P1: Add authenticated staff sessions and audit history for status/payment changes.
- P2: Add printable PDF generation and prescription language templates.

## Remaining next tasks
- Validate production database indexes and duplicate-slot behavior under concurrent booking.
- Add clinic-configurable hours, holidays, and notification templates.