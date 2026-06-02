#!/usr/bin/env python3
"""Create a Supabase Auth user (admin API if service role set, else public signup)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
load_dotenv(BASE_DIR / "frontend" / ".env.local")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
SERVICE_ROLE = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
ANON_KEY = os.environ.get("VITE_SUPABASE_ANON_KEY", "").strip() or os.environ.get(
    "SUPABASE_ANON_KEY", ""
).strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a Supabase Auth user.")
    parser.add_argument("--email", default="accounting@bigbattery.com")
    parser.add_argument("--password", default="BB-Commissions-2026!")
    return parser.parse_args()


def create_user(email: str, password: str) -> dict:
    if not SUPABASE_URL:
        raise SystemExit("SUPABASE_URL is not set in .env")

    payload = {
        "email": email,
        "password": password,
        "email_confirm": True,
    }

    if SERVICE_ROLE:
        url = f"{SUPABASE_URL}/auth/v1/admin/users"
        headers = {
            "Authorization": f"Bearer {SERVICE_ROLE}",
            "apikey": SERVICE_ROLE,
            "Content-Type": "application/json",
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
    else:
        if not ANON_KEY:
            raise SystemExit(
                "Set SUPABASE_SERVICE_ROLE_KEY or VITE_SUPABASE_ANON_KEY in .env"
            )
        url = f"{SUPABASE_URL}/auth/v1/signup"
        headers = {
            "Authorization": f"Bearer {ANON_KEY}",
            "apikey": ANON_KEY,
            "Content-Type": "application/json",
        }
        resp = requests.post(
            url,
            headers=headers,
            json={"email": email, "password": password},
            timeout=30,
        )

    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}

    if resp.status_code >= 400:
        raise SystemExit(f"Failed ({resp.status_code}): {data}")

    return data


def main() -> None:
    args = parse_args()
    data = create_user(args.email, args.password)
    print("User created successfully.")
    print(f"  Email: {args.email}")
    if "id" in data:
        print(f"  User id: {data['id']}")
    elif isinstance(data.get("user"), dict) and data["user"].get("id"):
        print(f"  User id: {data['user']['id']}")
    print("\nShare the password securely with the user and ask them to change it after first login.")


if __name__ == "__main__":
    main()
