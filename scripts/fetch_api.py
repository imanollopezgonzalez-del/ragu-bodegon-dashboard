"""
fetch_api.py — Ragu Bodegón
============================

Trae datos de la API de Nicolás (Cloud Function) y los upsertea en Supabase.

Uso normal (cron 2x/día):
    python fetch_api.py

Backfill puntual (desde la terminal o desde GitHub Actions workflow_dispatch):
    python fetch_api.py --desde 2026-01-01 --hasta 2026-01-31

Dry-run (no escribe a Supabase, solo imprime lo que traería):
    python fetch_api.py --dry-run --desde 2026-05-20 --hasta 2026-05-20

Variables de entorno requeridas (en .env local o GitHub Secrets):
    API_BASE_URL          ej: https://function-gethisto-er2eapi66q-rj.a.run.app
    API_AUTH_SECRET       el valor del header x-api-secret
    SUPABASE_URL          ej: https://abcd.supabase.co
    SUPABASE_SERVICE_KEY  service_role key (NO el anon)
    TIENDA                default: ragu
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()  # lee .env si existe (en GitHub Actions no hay .env, lee env vars directo)


# ─────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────
API_BASE_URL   = os.environ["API_BASE_URL"]
API_AUTH_SECRET = os.environ["API_AUTH_SECRET"]
SUPABASE_URL   = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY   = os.environ["SUPABASE_SERVICE_KEY"]
TIENDA         = os.environ.get("TIENDA", "ragu")

# Ventana por defecto: ayer y hoy (cubre las cargas que pueden llegar con delay)
DEFAULT_LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "2"))

# Las 3 tablas que devuelve la API (tickets es la última que sumó Nicolás)
TABLAS = ("ventas", "cobros", "tickets")


# ─────────────────────────────────────────────────────────────
# HTTP helpers
# ─────────────────────────────────────────────────────────────
# Delay entre requests para no superar el rate limit de la Cloud Function
API_DELAY_SECONDS = float(os.environ.get("API_DELAY_SECONDS", "3"))


RATE_LIMIT_RETRY_WAIT = float(os.environ.get("RATE_LIMIT_RETRY_WAIT", "75"))  # segundos
RATE_LIMIT_MAX_RETRIES = int(os.environ.get("RATE_LIMIT_MAX_RETRIES", "3"))


def call_api(fecha: date, tabla: str, timeout: int = 60) -> list[dict[str, Any]]:
    """POST a la Cloud Function y devuelve la lista de transacciones del día.
    Reintenta automáticamente si la API responde con rate limit."""
    payload = {"fecha": fecha.isoformat(), "tienda": TIENDA, "tabla": tabla}
    headers = {"Content-Type": "application/json", "x-api-secret": API_AUTH_SECRET}

    for attempt in range(1, RATE_LIMIT_MAX_RETRIES + 1):
        r = requests.post(API_BASE_URL, json=payload, headers=headers, timeout=timeout)

        # Rate limit: 400 o 429 con msg "Ratelimit superado"
        is_ratelimit = (not r.ok and r.status_code in (400, 429) and "Ratelimit" in r.text)
        if is_ratelimit:
            wait = RATE_LIMIT_RETRY_WAIT * attempt
            print(f"[rate limit] {tabla} {fecha}: intento {attempt}/{RATE_LIMIT_MAX_RETRIES} — esperando {wait:.0f}s...")
            time.sleep(wait)
            continue

        if not r.ok:
            print(f"[API error] {tabla} {fecha}: HTTP {r.status_code} — {r.text[:500]}")
            r.raise_for_status()

        data = r.json()

        # Error embebido en 200 OK
        if isinstance(data, dict) and data.get("status") == "error":
            msg = data.get("msg", "")
            if "Ratelimit" in msg and attempt < RATE_LIMIT_MAX_RETRIES:
                wait = RATE_LIMIT_RETRY_WAIT * attempt
                print(f"[rate limit 200] {tabla} {fecha}: intento {attempt}/{RATE_LIMIT_MAX_RETRIES} — esperando {wait:.0f}s...")
                time.sleep(wait)
                continue
            raise ValueError(f"API error en {tabla} {fecha}: {msg or data}")

        if isinstance(data, list):
            time.sleep(API_DELAY_SECONDS)
            return data

        for key in ("data", "results", "rows", "items", "registros", "ventas", "cobros", "tickets"):
            if isinstance(data.get(key), list):
                time.sleep(API_DELAY_SECONDS)
                return data[key]

        # No pudimos parsear — imprimimos la estructura completa para diagnosticar
        print(f"[diagnóstico] respuesta cruda de {tabla} {fecha}:")
        print(json.dumps(data, indent=2, ensure_ascii=False, default=str)[:2000])
        raise ValueError(f"Respuesta API inesperada — keys: {list(data.keys()) if isinstance(data, dict) else type(data).__name__}")

    raise ValueError(f"Rate limit no se levantó después de {RATE_LIMIT_MAX_RETRIES} intentos ({tabla} {fecha})")


def supabase_upsert(table: str, rows: list[dict[str, Any]]) -> tuple[int, int]:
    """
    Upsert por PK transaction_id.
    Devuelve (inserted_count, updated_count). Postgres no distingue easy entre los dos
    desde el cliente, así que devolvemos (total, 0) y dejamos los detalles al sync_log.
    """
    if not rows:
        return 0, 0
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    # Postgrest upsertea automáticamente cuando hay conflicto de PK y mandás merge-duplicates.
    r = requests.post(url, headers=headers, json=rows, timeout=60)
    if not r.ok:
        raise RuntimeError(f"Supabase upsert {table} falló {r.status_code}: {r.text[:300]}")
    return len(rows), 0


def supabase_insert_sync_log(entry: dict[str, Any]) -> None:
    url = f"{SUPABASE_URL}/rest/v1/sync_log"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    requests.post(url, headers=headers, json=entry, timeout=30)


# ─────────────────────────────────────────────────────────────
# Mapeo API → schema Supabase
# ─────────────────────────────────────────────────────────────
# IMPORTANTE: Estos mappers son una primera versión basada en lo que
# Nicolás describió. Cuando corramos el primer dry-run y veamos la
# respuesta real, ajustamos los nombres de campos exactos.
#
# Para no perder data si la respuesta tiene campos extra, guardamos
# el dict entero en raw_data.

def _get(row: dict, *keys, default=None):
    """Busca el primer key que exista en la fila (tolerante a snake_case / camelCase / mayúsculas)."""
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k]
        # case-insensitive fallback
        for rk in row:
            if rk.lower() == k.lower() and row[rk] not in (None, ""):
                return row[rk]
    return default


def _to_id(row: dict, fecha: date, tabla: str, idx: int) -> str:
    """ID único: intenta construir uno estable desde la API; fallback a índice."""
    # ID explícito de la API (si existe)
    rid = _get(row, "id", "transaction_id", "trans_id", "nro", "nro_comprobante", "numero", "ticket")
    if rid:
        return f"{tabla}_{rid}"
    # Para ventas: productocode + rubroname + fecha → estable entre corridas
    pcode   = _get(row, "productocode", "productcode", "cod_producto")
    rname   = _get(row, "rubroname", "rubro", "category")
    if pcode is not None:
        safe_rname = str(rname or "").replace(" ", "_")[:30]
        return f"{tabla}_{fecha.isoformat()}_{safe_rname}_{pcode}"
    return f"{tabla}_{fecha.isoformat()}_{idx:06d}"


def map_venta(row: dict, fecha: date, idx: int) -> dict:
    # La API devuelve 'rubroname' y 'productoname' (confirmado en dry-run 2026-05-24)
    return {
        "transaction_id": _to_id(row, fecha, "ventas", idx),
        "fecha":          fecha.isoformat(),
        "tienda":         TIENDA,
        "rubro":          _get(row, "rubroname", "rubro", "category"),
        "producto":       _get(row, "productoname", "producto", "productoDesc", "descripcion"),
        "unidades":       _get(row, "unidades", "cantidad", "qty"),
        "monto":          _get(row, "monto", "importe", "total", "amount"),
        "descuento":      _get(row, "descuento", "discount", "desc"),
        "dolar":          _get(row, "dolar", "dolar_oficial", "cotizacion", "usd"),
        "raw_data":       row,
    }


def map_cobro(row: dict, fecha: date, idx: int) -> dict:
    return {
        "transaction_id": _to_id(row, fecha, "cobros", idx),
        "fecha":          fecha.isoformat(),
        "tienda":         TIENDA,
        "medio_pago":     _get(row, "medio_pago", "medio", "forma_pago"),
        "moneda":         _get(row, "moneda"),
        "monto":          _get(row, "monto", "importe", "total"),
        "dolar":          _get(row, "dolar", "dolar_oficial", "cotizacion"),
        "raw_data":       row,
    }


def map_ticket(row: dict, fecha: date, idx: int) -> dict:
    return {
        "transaction_id": _to_id(row, fecha, "tickets", idx),
        "fecha":          fecha.isoformat(),
        "tienda":         TIENDA,
        "sector":         _get(row, "sector"),
        "comensales":     _get(row, "comensales"),
        "proformas":      _get(row, "proformas"),
        "monto":          _get(row, "monto", "importe", "total"),
        "cobranzas":      _get(row, "cobranzas"),
        "dolar":          _get(row, "dolar", "dolar_oficial", "cotizacion"),
        "raw_data":       row,
    }


MAPPERS = {
    "ventas":  map_venta,
    "cobros":  map_cobro,
    "tickets": map_ticket,
}


# ─────────────────────────────────────────────────────────────
# Orquestador
# ─────────────────────────────────────────────────────────────
@dataclass
class SyncResult:
    tabla: str
    fecha_desde: date
    fecha_hasta: date
    rows_fetched: int
    rows_upserted: int
    status: str
    error: str | None = None


def daterange(start: date, end: date):
    n = (end - start).days
    for i in range(n + 1):
        yield start + timedelta(days=i)


def sync_tabla(tabla: str, desde: date, hasta: date, dry_run: bool) -> SyncResult:
    started = datetime.now(timezone.utc)
    fetched: list[dict] = []
    try:
        mapper = MAPPERS[tabla]
        for f in daterange(desde, hasta):
            data = call_api(f, tabla)
            for i, row in enumerate(data):
                fetched.append(mapper(row, f, i))
        upserted = 0
        if dry_run:
            print(f"[dry-run] {tabla}: traería {len(fetched)} filas")
            if fetched:
                print(f"[dry-run] muestra primera fila:\n{json.dumps(fetched[0], indent=2, default=str)}")
        else:
            upserted, _ = supabase_upsert(tabla, fetched)

        entry = {
            "started_at":   started.isoformat(),
            "finished_at":  datetime.now(timezone.utc).isoformat(),
            "tabla":        tabla,
            "fecha_desde":  desde.isoformat(),
            "fecha_hasta":  hasta.isoformat(),
            "rows_fetched": len(fetched),
            "rows_inserted": upserted,
            "rows_updated":  0,
            "status":       "ok",
        }
        if not dry_run:
            supabase_insert_sync_log(entry)
        return SyncResult(tabla, desde, hasta, len(fetched), upserted, "ok")

    except Exception as e:
        entry = {
            "started_at":   started.isoformat(),
            "finished_at":  datetime.now(timezone.utc).isoformat(),
            "tabla":        tabla,
            "fecha_desde":  desde.isoformat(),
            "fecha_hasta":  hasta.isoformat(),
            "rows_fetched": len(fetched),
            "rows_inserted": 0,
            "rows_updated":  0,
            "status":       "error",
            "error_message": str(e)[:1000],
        }
        if not dry_run:
            supabase_insert_sync_log(entry)
        return SyncResult(tabla, desde, hasta, len(fetched), 0, "error", str(e))


def main():
    parser = argparse.ArgumentParser(description="Fetch + upsert datos de Ragu Bodegón")
    parser.add_argument("--desde", help="YYYY-MM-DD (default: hoy - LOOKBACK_DAYS)")
    parser.add_argument("--hasta", help="YYYY-MM-DD (default: hoy)")
    parser.add_argument("--tabla", choices=TABLAS, help="Solo una tabla (default: todas)")
    parser.add_argument("--dry-run", action="store_true", help="No escribe en Supabase, solo imprime")
    args = parser.parse_args()

    hoy = date.today()
    hasta = date.fromisoformat(args.hasta) if args.hasta else hoy
    desde = date.fromisoformat(args.desde) if args.desde else hasta - timedelta(days=DEFAULT_LOOKBACK_DAYS - 1)

    tablas = (args.tabla,) if args.tabla else TABLAS

    print(f"Ragu Bodegón sync — desde {desde} hasta {hasta} — tablas: {','.join(tablas)} — dry_run={args.dry_run}")

    # Delay entre tablas para no superar el rate limit de la Cloud Function.
    # El primer sync no necesita espera — el delay se aplica ENTRE tablas.
    INTER_TABLE_DELAY = float(os.environ.get("INTER_TABLE_DELAY", "45"))

    had_error = False
    for i, t in enumerate(tablas):
        if i > 0:
            print(f"  ⏳ esperando {INTER_TABLE_DELAY:.0f}s (rate limit)...")
            time.sleep(INTER_TABLE_DELAY)
        r = sync_tabla(t, desde, hasta, args.dry_run)
        if r.status == "ok":
            print(f"  ✓ {t}: {r.rows_fetched} filas fetched, {r.rows_upserted} upserted")
        else:
            had_error = True
            print(f"  ✗ {t}: ERROR — {r.error}", file=sys.stderr)

    sys.exit(1 if had_error else 0)


if __name__ == "__main__":
    main()
