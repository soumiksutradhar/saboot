# Saboot — Python + Flask Micro-Contract & Wage Escrow API

A voice-first, low-literacy micro-contract platform with separate **Employer** and **Employee (Worker)** phone/OTP logins, SHA-256 tamper detection, append-only event chains, 24-hour escrow auto-release, public QR verification, and server-side PDF export.

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000` in your browser.

## Demo Accounts (OTP shown on screen)

- **Employer 1:** `9988776655` (Kalinga Builders)
- **Employer 2:** `9001122334` (Meera Sharma)
- **Employee 1:** `9876543210` (Ramesh Nayak)
- **Employee 2:** `9123456789` (Sunita Devi)

## Key API Endpoints

- `POST /api/auth/request-otp` & `POST /api/auth/verify-otp` — Role-separated phone OTP login
- `GET/POST /api/contracts` — List contracts for current user / Create employer offer
- `POST /api/contracts/<id>/sign` — Worker dual-consent signature (requires voice summary heard)
- `POST /api/contracts/<id>/withdraw` — 24-hour private worker cancellation
- `POST /api/contracts/<id>/escrow/fund` & `checkin` & `approve` — Wage escrow lifecycle
- `GET /verify/<id>` & `GET /api/verify/<id>` — Public no-login QR verification endpoint
- `GET /api/contracts/<id>/pdf` — Downloadable ReportLab PDF contract & dispute pack
- `GET /api/trust` & `POST /api/trust/share` — Portable trust tier & consent-logged sharing
