"""Gold register: bars held by a custodian, movements, sales and AUD valuation.

The register records gold that a custodian actually holds. Every bar needs a custodian and a certificate or
bar-list reference. Quantity is never created by the app, only recorded.
"""
import time

import requests

from . import db, security
from .money import fmt

OZ_G = 31.1034768  # grams per troy ounce


def add_bar(serial, weight_g, purity, location, custodian, cert_ref, actor):
    if not custodian.strip() or not cert_ref.strip():
        raise ValueError("Custodian and certificate/bar-list reference are required for every bar.")
    if weight_g <= 0 or not 0 < purity <= 1:
        raise ValueError("Weight must be positive and purity between 0 and 1 (e.g. 0.9999).")
    db.x("INSERT INTO gold_bars(serial,weight_g,purity,location,custodian,status,cert_ref,added_ts) VALUES(?,?,?,?,?,?,?,?)",
         (serial.strip(), weight_g, purity, location, custodian, "HELD", cert_ref, time.time()))
    security.audit(actor, "GOLD_BAR_ADDED", f"{serial} {weight_g}g purity={purity} custodian={custodian} cert={cert_ref}")


def bars(status=None):
    if status:
        return db.q("SELECT * FROM gold_bars WHERE status=? ORDER BY id", (status,))
    return db.q("SELECT * FROM gold_bars ORDER BY id")


def held_fine_grams() -> float:
    return sum(b["weight_g"] * b["purity"] for b in bars("HELD"))


def held_gross_kg() -> float:
    return sum(b["weight_g"] for b in bars("HELD")) / 1000


def move_bar(bar_id, to_location, note, actor):
    b = db.q1("SELECT * FROM gold_bars WHERE id=? AND status='HELD'", (bar_id,))
    if not b:
        raise ValueError("Bar not found or not held.")
    db.x("UPDATE gold_bars SET location=? WHERE id=?", (to_location, bar_id))
    db.x("INSERT INTO gold_moves(ts,bar_id,kind,to_location,note) VALUES(?,?,?,?,?)", (time.time(), bar_id, "MOVE", to_location, note))
    security.audit(actor, "GOLD_MOVED", f"{b['serial']} {b['location']} -> {to_location}")


def sell_bar(bar_id, buyer, price_aud_cents, note, actor):
    b = db.q1("SELECT * FROM gold_bars WHERE id=? AND status='HELD'", (bar_id,))
    if not b:
        raise ValueError("Bar not found or not held.")
    if not buyer.strip() or price_aud_cents <= 0:
        raise ValueError("Buyer and a positive sale price are required.")
    db.x("UPDATE gold_bars SET status='SOLD' WHERE id=?", (bar_id,))
    db.x("INSERT INTO gold_moves(ts,bar_id,kind,buyer,price_aud_cents,note) VALUES(?,?,?,?,?,?)",
         (time.time(), bar_id, "SALE", buyer, price_aud_cents, note))
    security.audit(actor, "GOLD_SOLD", f"{b['serial']} to {buyer} for {fmt(price_aud_cents)}")


# ---------- prices ----------
def fetch_live_prices():
    """Gold USD/oz from gold-api.com and USD->AUD from frankfurter.app (both free, no key)."""
    g = requests.get("https://api.gold-api.com/price/XAU", timeout=10).json()
    usd_oz = float(g["price"])
    fx = requests.get("https://api.frankfurter.app/latest?from=USD&to=AUD", timeout=10).json()
    usd_aud = float(fx["rates"]["AUD"])
    save_price(usd_oz, usd_aud, "live: gold-api.com + frankfurter.app")
    return usd_oz, usd_aud


def save_price(usd_oz, usd_aud, source):
    if usd_oz <= 0 or usd_aud <= 0:
        raise ValueError("Prices must be positive.")
    db.x("INSERT INTO gold_prices(ts,usd_per_oz,usd_aud,source) VALUES(?,?,?,?)", (time.time(), usd_oz, usd_aud, source))


def latest_price():
    return db.q1("SELECT * FROM gold_prices ORDER BY id DESC LIMIT 1")


def valuation():
    """Value of held gold: fine oz x USD/oz = USD value, then x USD->AUD. None if no price saved yet."""
    p = latest_price()
    fine_oz = held_fine_grams() / OZ_G
    if not p and fine_oz == 0:
        return {"fine_oz": 0.0, "usd": 0.0, "aud_cents": 0, "price": None}
    if not p:
        return {"fine_oz": fine_oz, "usd": None, "aud_cents": None, "price": None}
    usd = fine_oz * p["usd_per_oz"]
    return {"fine_oz": fine_oz, "usd": usd, "aud_cents": int(round(usd * p["usd_aud"] * 100)), "price": p}
