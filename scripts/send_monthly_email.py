"""
send_monthly_email.py — Ragu Bodegón
=====================================

Genera un resumen del mes anterior (KPIs principales) y lo manda por Gmail.

Uso:
    python send_monthly_email.py            # mes anterior al actual
    python send_monthly_email.py --anio 2026 --mes 4

Variables de entorno requeridas:
    SUPABASE_URL, SUPABASE_SERVICE_KEY
    GMAIL_USER, GMAIL_APP_PASSWORD
    EMAIL_TO        (uno o varios separados por coma)
    TIENDA          default: ragu
"""

from __future__ import annotations

import argparse
import os
import smtplib
import ssl
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
GMAIL_USER   = os.environ["GMAIL_USER"]
GMAIL_PASS   = os.environ["GMAIL_APP_PASSWORD"]
EMAIL_TO     = [e.strip() for e in os.environ["EMAIL_TO"].split(",") if e.strip()]
TIENDA       = os.environ.get("TIENDA", "ragu")

NOMBRE_TIENDA = "Ragu Bodegón"
MESES_ES = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
            "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]


def _sb_get(path: str, params: dict) -> list[dict[str, Any]]:
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Accept": "application/json",
    }
    r = requests.get(url, headers=headers, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def fmt_ars(v: float | int | None) -> str:
    if v is None:
        return "—"
    return f"$ {v:,.0f}".replace(",", ".")


def resumen_mes(anio: int, mes: int) -> dict[str, Any]:
    """Tira de las views v_*_mensual y devuelve un dict con los KPIs."""
    eq = {"anio": f"eq.{anio}", "mes": f"eq.{mes}"}

    ventas   = _sb_get("v_ventas_mensual",  {**eq, "select": "monto,descuento,unidades,transacciones,rubro"})
    cobros   = _sb_get("v_cobros_mensual",  {**eq, "select": "monto,medio_pago,transacciones"})
    tickets  = _sb_get("v_tickets_mensual", {**eq, "select": "monto,sector,comensales,proformas"})

    total_ventas    = sum((r["monto"] or 0)     for r in ventas)
    total_descuento = sum((r["descuento"] or 0) for r in ventas)
    total_unidades  = sum((r["unidades"] or 0)  for r in ventas)

    total_cobros    = sum((r["monto"] or 0) for r in cobros)
    cobros_por_medio = {}
    for r in cobros:
        cobros_por_medio[r["medio_pago"] or "Sin clasificar"] = cobros_por_medio.get(r["medio_pago"] or "Sin clasificar", 0) + (r["monto"] or 0)

    total_comensales = sum((r["comensales"] or 0) for r in tickets)
    total_proformas  = sum((r["proformas"]  or 0) for r in tickets)
    ticket_promedio  = (total_ventas / total_proformas) if total_proformas else None
    monto_por_comensal = (total_ventas / total_comensales) if total_comensales else None

    # Top 5 rubros por monto
    rubros = {}
    for r in ventas:
        k = r["rubro"] or "Sin rubro"
        rubros[k] = rubros.get(k, 0) + (r["monto"] or 0)
    top_rubros = sorted(rubros.items(), key=lambda x: -x[1])[:5]

    return {
        "total_ventas":       total_ventas,
        "total_descuento":    total_descuento,
        "total_unidades":     total_unidades,
        "total_cobros":       total_cobros,
        "cobros_por_medio":   cobros_por_medio,
        "total_comensales":   total_comensales,
        "total_proformas":    total_proformas,
        "ticket_promedio":    ticket_promedio,
        "monto_por_comensal": monto_por_comensal,
        "top_rubros":         top_rubros,
    }


def build_html(anio: int, mes: int, r: dict[str, Any]) -> str:
    nombre_mes = MESES_ES[mes]
    rows_medios = "".join(
        f"<tr><td style='padding:6px 12px;border-bottom:1px solid #eee'>{k}</td>"
        f"<td style='padding:6px 12px;border-bottom:1px solid #eee;text-align:right'>{fmt_ars(v)}</td></tr>"
        for k, v in sorted(r["cobros_por_medio"].items(), key=lambda x: -x[1])
    )
    rows_rubros = "".join(
        f"<tr><td style='padding:6px 12px;border-bottom:1px solid #eee'>{k}</td>"
        f"<td style='padding:6px 12px;border-bottom:1px solid #eee;text-align:right'>{fmt_ars(v)}</td></tr>"
        for k, v in r["top_rubros"]
    )

    return f"""<!DOCTYPE html>
<html><body style="font-family:Inter,Arial,sans-serif;background:#f7f5f0;padding:20px;color:#1a1814">
  <div style="max-width:640px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;border:1px solid #ddd8cc">
    <div style="background:#F5C800;padding:18px 24px;border-bottom:3px solid #C9A400">
      <h1 style="margin:0;font-size:22px;letter-spacing:1px">{NOMBRE_TIENDA}</h1>
      <p style="margin:4px 0 0;font-size:12px;letter-spacing:2px;text-transform:uppercase;opacity:0.75">Resumen mensual — {nombre_mes} {anio}</p>
    </div>
    <div style="padding:24px">
      <h2 style="font-size:14px;letter-spacing:2px;text-transform:uppercase;color:#C9A400;border-bottom:1px solid #eee;padding-bottom:8px">KPIs</h2>
      <table style="width:100%;border-collapse:collapse;margin-bottom:24px">
        <tr><td style="padding:6px 0">Ventas totales</td><td style="text-align:right;font-weight:700">{fmt_ars(r['total_ventas'])}</td></tr>
        <tr><td style="padding:6px 0">Descuentos aplicados</td><td style="text-align:right">{fmt_ars(r['total_descuento'])}</td></tr>
        <tr><td style="padding:6px 0">Unidades vendidas</td><td style="text-align:right">{r['total_unidades']:,}</td></tr>
        <tr><td style="padding:6px 0">Cobros totales</td><td style="text-align:right;font-weight:700">{fmt_ars(r['total_cobros'])}</td></tr>
        <tr><td style="padding:6px 0">Comensales</td><td style="text-align:right">{r['total_comensales']:,}</td></tr>
        <tr><td style="padding:6px 0">Proformas (tickets)</td><td style="text-align:right">{r['total_proformas']:,}</td></tr>
        <tr><td style="padding:6px 0">Ticket promedio</td><td style="text-align:right">{fmt_ars(r['ticket_promedio'])}</td></tr>
        <tr><td style="padding:6px 0">Monto por comensal</td><td style="text-align:right">{fmt_ars(r['monto_por_comensal'])}</td></tr>
      </table>

      <h2 style="font-size:14px;letter-spacing:2px;text-transform:uppercase;color:#C9A400;border-bottom:1px solid #eee;padding-bottom:8px">Top 5 rubros por monto</h2>
      <table style="width:100%;border-collapse:collapse;margin-bottom:24px">{rows_rubros or '<tr><td>—</td></tr>'}</table>

      <h2 style="font-size:14px;letter-spacing:2px;text-transform:uppercase;color:#C9A400;border-bottom:1px solid #eee;padding-bottom:8px">Cobros por medio de pago</h2>
      <table style="width:100%;border-collapse:collapse">{rows_medios or '<tr><td>—</td></tr>'}</table>

      <p style="margin-top:24px;font-size:12px;color:#9e9688">Generado automáticamente desde Supabase — Ragu Bodegón.</p>
    </div>
  </div>
</body></html>
"""


def send_email(subject: str, html: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = ", ".join(EMAIL_TO)
    msg.attach(MIMEText(html, "html", "utf-8"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
        s.login(GMAIL_USER, GMAIL_PASS)
        s.sendmail(GMAIL_USER, EMAIL_TO, msg.as_string())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--anio", type=int)
    parser.add_argument("--mes",  type=int)
    args = parser.parse_args()

    hoy = date.today()
    if args.anio and args.mes:
        anio, mes = args.anio, args.mes
    else:
        # Mes anterior al actual
        if hoy.month == 1:
            anio, mes = hoy.year - 1, 12
        else:
            anio, mes = hoy.year, hoy.month - 1

    r = resumen_mes(anio, mes)
    html = build_html(anio, mes, r)
    subject = f"Ragu Bodegón — Resumen {MESES_ES[mes]} {anio}"
    send_email(subject, html)
    print(f"✓ Email enviado a {', '.join(EMAIL_TO)}: {subject}")


if __name__ == "__main__":
    main()
