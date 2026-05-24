# Ragu Bodegón — Dashboard automatizado

Sistema que trae datos de la API de Nicolás **2 veces al día**, los guarda en Supabase (Postgres) y los muestra en un dashboard HTML (adaptado del modelo Padella). El día 1 de cada mes envía un email con el resumen del mes anterior.

---

## Arquitectura

```
   API de Nicolás (Cloud Function en GCP)
              │
              │  POST diario 2x (08:00 y 12:00 ART)
              ▼
   GitHub Actions  ──►  Supabase (Postgres)
                              │
                              ▼
                       GitHub Pages
                       dashboards/ragu.html
                              ▲
                              │ lee vía REST
                              │
                       Vos en el navegador
```

---

## Por qué Supabase (Postgres) y no Firebase

Nicolás recomendó base SQL. Las razones prácticas:

1. **Agregaciones**: el dashboard hace `SUM(monto) GROUP BY mes`, comparativas año-contra-año, top-N rubros. Postgres las hace en una query; Firestore exige mantener contadores o leer miles de docs en el front.
2. **Costo**: Firestore cobra por documento leído. 30.000 tickets × cada vez que abrís el dashboard = caro. Supabase free tier = 500 MB + queries ilimitadas.
3. **Upsert/dedupe**: `INSERT … ON CONFLICT (id) DO UPDATE` en una línea. En Firestore = leer-comparar-escribir.
4. **El dato es tabular** (ventas/cobros/tickets con campos fijos) — encaja nativo en SQL.

---

## Estructura del proyecto

```
ragu-bodegon-dashboard/
├── README.md                       ← este archivo
├── .env.example                    ← plantilla de variables para correr local
├── .gitignore
├── db/
│   └── schema.sql                  ← schema de Supabase (correr 1 vez)
├── scripts/
│   ├── fetch_api.py                ← cron 2x/día: pull + upsert
│   ├── send_monthly_email.py       ← cron mensual: resumen + email
│   └── requirements.txt
├── dashboards/
│   ├── _config.js                  ← URL y anon key de tu Supabase
│   └── ragu.html                   ← dashboard (Padella adaptado)
├── data/                           ← para artefactos locales (vacío en git)
└── .github/workflows/
    ├── fetch-data.yml              ← cron 2x/día (workflow_dispatch para backfill)
    └── monthly-email.yml           ← cron día 1 a las 08:00 ART
```

---

## Pasos para poner esto en producción

### 1. Crear el proyecto en Supabase

> Indicaste que vas a crear un proyecto Supabase nuevo dedicado a Ragu.

1. Entrar a https://supabase.com → tu cuenta → **New project**.
2. Nombre sugerido: `ragu-bodegon`. Región: la más cercana a Argentina (São Paulo / `sa-east-1`). Generá una password fuerte para la DB (Supabase la guarda, no la vas a usar mucho).
3. Una vez creado (tarda ~2 min), ir a **Project Settings → API** y copiar:
   - `Project URL`
   - `anon public key` (esta va al dashboard — público, solo lectura)
   - `service_role key` (esta va a GitHub Secrets — escribe en la DB; nunca al repo)
4. Ir a **SQL Editor → New query**, pegar el contenido de `db/schema.sql`, **Run**. Esto crea las tablas, las vistas, las RLS y los triggers.

### 2. Crear el repo de GitHub

> Indicaste crear un repo nuevo dedicado.

Desde tu carpeta local (`C:\Users\Imalo\Desktop\ragu-bodegon-dashboard`):

```powershell
cd C:\Users\Imalo\Desktop\ragu-bodegon-dashboard
git init -b main
git add .
git commit -m "Scaffold inicial — Ragu Bodegón dashboard"
# Si tenés gh CLI:
gh repo create ragu-bodegon-dashboard --private --source=. --push
# Si no, creá el repo desde la web y después:
#   git remote add origin git@github.com:TU_USUARIO/ragu-bodegon-dashboard.git
#   git push -u origin main
```

### 3. Configurar Secrets en GitHub

En el repo: **Settings → Secrets and variables → Actions → New repository secret**.

| Secret name             | Valor                                                       |
|-------------------------|-------------------------------------------------------------|
| `API_BASE_URL`          | `https://function-gethisto-er2eapi66q-rj.a.run.app`         |
| `API_AUTH_SECRET`       | el `x-api-secret` que mandó Nicolás                         |
| `SUPABASE_URL`          | Project URL del paso 1                                      |
| `SUPABASE_SERVICE_KEY`  | service_role key del paso 1                                 |
| `GMAIL_USER`            | `imanollopezgonzalez@gmail.com`                             |
| `GMAIL_APP_PASSWORD`    | App Password de Gmail (ver paso 4)                          |
| `EMAIL_TO`              | destinatarios separados por coma                            |

### 4. Generar la App Password de Gmail

Gmail no te deja loguearte con la contraseña normal desde scripts. Hay que generar un **App Password**:

1. Tu cuenta de Google necesita 2FA activado (https://myaccount.google.com/security).
2. Ir a https://myaccount.google.com/apppasswords.
3. App: "Otra (nombre personalizado)", escribir `Ragu Dashboard`, Generar.
4. Copiar los 16 caracteres (formato `xxxx xxxx xxxx xxxx`, sin espacios).
5. Pegar ese valor en el secret `GMAIL_APP_PASSWORD`.

### 5. Configurar el dashboard

Editar `dashboards/_config.js` y completar:

```js
window.RAGU_CONFIG = {
  SUPABASE_URL:  "https://abcd1234.supabase.co",     // ← tu URL
  SUPABASE_ANON: "eyJhbGciOi...",                    // ← el anon public key
  TIENDA:        "ragu",
  MESES_HISTORIA: 18
};
```

Commit + push.

### 6. Habilitar GitHub Pages

**Settings → Pages**:

- **Source**: `Deploy from a branch`
- **Branch**: `main` / **folder**: `/dashboards`

GitHub te asigna una URL tipo `https://TU_USUARIO.github.io/ragu-bodegon-dashboard/ragu.html`. Esa es la URL del dashboard.

### 7. Primer test (recomendado)

Antes de prender los cron, hacé un dry-run manual desde la UI de Actions:

1. **Actions → Fetch data — Ragu Bodegón → Run workflow**.
2. Inputs: `desde: 2026-05-20`, `hasta: 2026-05-20`, `dry_run: true`.
3. Mirá los logs: vas a ver cuántas filas trae y la estructura de la primera fila de cada tabla.
4. Si los campos no coinciden con lo que asumimos en `fetch_api.py` (sección `map_venta` / `map_cobro` / `map_ticket`), ajustamos los mappers y volvemos a probar.
5. Una vez OK, correr de nuevo sin `dry_run` y verificar en Supabase Table Editor que aparecieron las filas.

Después de eso, los crons (08:00 y 12:00 ART) se van a disparar solos.

---

## Cómo funciona la deduplicación

Cada corrida del cron pide solo los **últimos 2 días** (configurable con `LOOKBACK_DAYS`). Es decir: cubre el día de hoy + un día de margen por si la API tarda en consolidar transacciones del cierre.

Los datos se mandan a Supabase con `INSERT ... ON CONFLICT (transaction_id) DO UPDATE`. Resultado:

- Si la transacción YA está en la DB → se **actualiza** (por si Nicolás corrigió algún monto).
- Si es nueva → se **inserta**.
- Nunca duplica.

Si en algún momento querés rellenar un mes viejo (backfill), corrés el workflow manualmente con `desde 2026-01-01 hasta 2026-01-31`. No hay que hacer nada especial — el upsert es idempotente.

---

## Diferencias importantes vs el dashboard original de Padella

| Punto                  | Padella original                       | Ragu (este sistema)                              |
|------------------------|----------------------------------------|--------------------------------------------------|
| Carga de ventas        | Upload de XLSX manual                  | Pull automático desde API 2x/día                 |
| Carga de gastos        | Upload XLSX + Claude AI parsea         | **Sigue siendo manual** (la API no devuelve gastos) — cargás `gastos_mensuales` desde Supabase Table Editor o vía SQL |
| Campo `camarero`       | Disponible (lo trae el XLSX)           | **No disponible** — la API ventas no incluye usuario. El filtro de camarero queda vacío. |
| Descuentos             | Excluidos (reporte manual los omitía)  | **Incluidos** en `monto` (la API los suma). Si querés el monto neto restá `descuento`. |
| Multi-restaurante      | —                                      | Solo Ragu por ahora. Para sumar otras tiendas: agregar la columna `tienda` ya está en el schema, basta con cambiar `TIENDA=padella` en el cron y duplicar el HTML. |

---

## Costos esperados

- **Supabase**: free tier (500 MB DB, 2 GB ancho de banda, 50.000 lecturas/mes). Ragu va a usar < 5% de esto en el primer año.
- **GitHub Actions**: gratis hasta 2.000 min/mes en repos privados. Cada corrida toma ~30 s → 60 corridas/mes × 30 s = 30 min. Sobra muchísimo.
- **GitHub Pages**: gratis.
- **Gmail**: gratis.

Total esperado: **$0/mes**.

---

## Pendiente para coordinar con Nicolás

1. **Tabla `tickets`**: él dijo que la estaba preparando (fusiona los puntos 3 y 4). Cuando esté lista, probamos.
2. **Forma exacta de la respuesta JSON**: el script asume nombres de campos comunes (`monto`, `fecha`, `rubro`, etc.) y guarda el JSON completo en `raw_data` por las dudas. Después del primer dry-run vemos los nombres reales y ajustamos los mappers si hace falta.
3. **Identificador único por transacción**: si la API trae un `id` o `nro_comprobante`, el dedupe es perfecto. Si no, fabricamos un ID determinístico `<tabla>_<fecha>_<idx>` — funciona pero es menos robusto si la API reordena filas entre corridas.

---

## Comandos útiles

```powershell
# Test local del fetch (necesita .env con los valores reales)
cd C:\Users\Imalo\Desktop\ragu-bodegon-dashboard
pip install -r scripts\requirements.txt
python scripts\fetch_api.py --dry-run --desde 2026-05-20 --hasta 2026-05-20

# Test local del email
python scripts\send_monthly_email.py --anio 2026 --mes 4
```
