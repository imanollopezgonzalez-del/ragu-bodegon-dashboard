-- ============================================================
--  RAGU BODEGÓN — Schema para Supabase (Postgres)
-- ============================================================
--  Cómo correrlo: Supabase Dashboard → SQL Editor → New query →
--                pegar este archivo entero → Run.
--
--  Pensado para ser idempotente: se puede correr de nuevo y no
--  rompe lo que ya existe (usa IF NOT EXISTS / ON CONFLICT).
-- ============================================================

-- ─────────────────────────────────────────────────────────────
-- 1) TABLAS DE DATOS CRUDOS DESDE LA API
-- ─────────────────────────────────────────────────────────────
--  Estrategia: una tabla por "tabla" que devuelve la API
--  (ventas, cobros, tickets). Cada fila guarda los campos
--  conocidos en columnas + el JSON completo de la API en
--  raw_data, así si Nicolás agrega campos no perdemos info.

-- Tabla VENTAS (incluye descuentos según API de Nicolás)
CREATE TABLE IF NOT EXISTS ventas (
  -- ID único de la transacción (lo da la API). PK para upsert.
  transaction_id   TEXT PRIMARY KEY,
  fecha            DATE        NOT NULL,
  tienda           TEXT        NOT NULL DEFAULT 'ragu',
  rubro            TEXT,
  producto         TEXT,
  unidades         NUMERIC(12,2),
  monto            NUMERIC(14,2),
  descuento        NUMERIC(14,2),
  dolar            NUMERIC(14,4),
  -- raw_data: respuesta original de la API por si hay campos extra
  raw_data         JSONB,
  -- auditoría
  inserted_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ventas_fecha  ON ventas (fecha);
CREATE INDEX IF NOT EXISTS idx_ventas_tienda ON ventas (tienda);
CREATE INDEX IF NOT EXISTS idx_ventas_rubro  ON ventas (rubro);


-- Tabla COBROS
CREATE TABLE IF NOT EXISTS cobros (
  transaction_id   TEXT PRIMARY KEY,
  fecha            DATE        NOT NULL,
  tienda           TEXT        NOT NULL DEFAULT 'ragu',
  medio_pago       TEXT,
  moneda           TEXT,
  monto            NUMERIC(14,2),
  dolar            NUMERIC(14,4),
  raw_data         JSONB,
  inserted_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cobros_fecha      ON cobros (fecha);
CREATE INDEX IF NOT EXISTS idx_cobros_medio_pago ON cobros (medio_pago);


-- Tabla TICKETS (Nicolás la está preparando — fusiona comensales/proformas)
CREATE TABLE IF NOT EXISTS tickets (
  transaction_id   TEXT PRIMARY KEY,
  fecha            DATE        NOT NULL,
  tienda           TEXT        NOT NULL DEFAULT 'ragu',
  sector           TEXT,                   -- mostrador / salon / web
  comensales       INTEGER,
  proformas        INTEGER,
  monto            NUMERIC(14,2),
  cobranzas        NUMERIC(14,2),
  dolar            NUMERIC(14,4),
  raw_data         JSONB,
  inserted_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tickets_fecha  ON tickets (fecha);
CREATE INDEX IF NOT EXISTS idx_tickets_sector ON tickets (sector);


-- ─────────────────────────────────────────────────────────────
-- 2) TABLA DE GASTOS (carga manual desde XLSX por ahora)
-- ─────────────────────────────────────────────────────────────
--  La API no devuelve gastos. Esto se mantiene como carga manual
--  hasta que haya un origen automatizado.

CREATE TABLE IF NOT EXISTS gastos_mensuales (
  id               BIGSERIAL PRIMARY KEY,
  anio             INTEGER     NOT NULL,
  mes              INTEGER     NOT NULL CHECK (mes BETWEEN 1 AND 12),
  rubro            TEXT        NOT NULL,
  -- rubro: salarios | mat_alimentos | mat_bebidas | gastos_varios | alquiler_servicios
  total            NUMERIC(14,2) NOT NULL,
  items            JSONB,
  inserted_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (anio, mes, rubro)
);


-- ─────────────────────────────────────────────────────────────
-- 3) TABLA DE LOG DE SINCRONIZACIÓN
-- ─────────────────────────────────────────────────────────────
--  Cada corrida del cron deja una fila acá. Permite ver desde
--  el dashboard "última sincronización: hace X min" y debuggear.

CREATE TABLE IF NOT EXISTS sync_log (
  id               BIGSERIAL PRIMARY KEY,
  started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at      TIMESTAMPTZ,
  tabla            TEXT        NOT NULL,   -- ventas | cobros | tickets
  fecha_desde      DATE,
  fecha_hasta      DATE,
  rows_fetched     INTEGER,
  rows_inserted    INTEGER,
  rows_updated     INTEGER,
  status           TEXT        NOT NULL,   -- ok | error
  error_message    TEXT
);

CREATE INDEX IF NOT EXISTS idx_sync_log_started ON sync_log (started_at DESC);


-- ─────────────────────────────────────────────────────────────
-- 4) TRIGGER PARA MANTENER updated_at
-- ─────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_ventas_touch') THEN
    CREATE TRIGGER trg_ventas_touch BEFORE UPDATE ON ventas
      FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_cobros_touch') THEN
    CREATE TRIGGER trg_cobros_touch BEFORE UPDATE ON cobros
      FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_tickets_touch') THEN
    CREATE TRIGGER trg_tickets_touch BEFORE UPDATE ON tickets
      FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
  END IF;
END $$;


-- ─────────────────────────────────────────────────────────────
-- 5) ROW LEVEL SECURITY — el anon key del front solo puede LEER
-- ─────────────────────────────────────────────────────────────
ALTER TABLE ventas             ENABLE ROW LEVEL SECURITY;
ALTER TABLE cobros             ENABLE ROW LEVEL SECURITY;
ALTER TABLE tickets            ENABLE ROW LEVEL SECURITY;
ALTER TABLE gastos_mensuales   ENABLE ROW LEVEL SECURITY;
ALTER TABLE sync_log           ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname='read_ventas')
    THEN CREATE POLICY read_ventas             ON ventas             FOR SELECT USING (true); END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname='read_cobros')
    THEN CREATE POLICY read_cobros             ON cobros             FOR SELECT USING (true); END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname='read_tickets')
    THEN CREATE POLICY read_tickets            ON tickets            FOR SELECT USING (true); END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname='read_gastos')
    THEN CREATE POLICY read_gastos             ON gastos_mensuales   FOR SELECT USING (true); END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname='read_sync_log')
    THEN CREATE POLICY read_sync_log           ON sync_log           FOR SELECT USING (true); END IF;
END $$;

-- Las escrituras solo las hace el service_role (que bypassea RLS).
-- El anon key del dashboard solo puede SELECT.


-- ─────────────────────────────────────────────────────────────
-- 6) VISTAS DE AYUDA PARA EL DASHBOARD
-- ─────────────────────────────────────────────────────────────
-- Ventas agregadas por año/mes (lo que el dashboard usa más)
CREATE OR REPLACE VIEW v_ventas_mensual AS
SELECT
  EXTRACT(YEAR  FROM fecha)::INT AS anio,
  EXTRACT(MONTH FROM fecha)::INT AS mes,
  rubro,
  producto,
  SUM(unidades)  AS unidades,
  SUM(monto)     AS monto,
  SUM(descuento) AS descuento,
  AVG(dolar)     AS dolar_promedio,
  COUNT(*)       AS transacciones
FROM ventas
GROUP BY 1,2,3,4;

-- Cobros agregados por mes y medio de pago
CREATE OR REPLACE VIEW v_cobros_mensual AS
SELECT
  EXTRACT(YEAR  FROM fecha)::INT AS anio,
  EXTRACT(MONTH FROM fecha)::INT AS mes,
  medio_pago,
  SUM(monto) AS monto,
  AVG(dolar) AS dolar_promedio,
  COUNT(*)   AS transacciones
FROM cobros
GROUP BY 1,2,3;

-- Tickets agregados por mes y sector
CREATE OR REPLACE VIEW v_tickets_mensual AS
SELECT
  EXTRACT(YEAR  FROM fecha)::INT AS anio,
  EXTRACT(MONTH FROM fecha)::INT AS mes,
  sector,
  SUM(comensales) AS comensales,
  SUM(proformas)  AS proformas,
  SUM(monto)      AS monto,
  AVG(dolar)      AS dolar_promedio
FROM tickets
GROUP BY 1,2,3;
