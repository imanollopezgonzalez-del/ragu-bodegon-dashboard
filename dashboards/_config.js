// Configuración del dashboard de Ragu Bodegón.
// Este archivo SÍ se commitea al repo — el anon key es público por diseño
// (solo permite SELECT por RLS, no escribe nada).
//
// Después de crear el proyecto en Supabase, completá estos dos valores:
//   1) SUPABASE_URL  → Project Settings → API → Project URL
//   2) SUPABASE_ANON → Project Settings → API → anon public key

window.RAGU_CONFIG = {
  SUPABASE_URL:  "https://tngnzltiamlfepkhdxgj.supabase.co",
  SUPABASE_ANON: "sb_publishable_4yz_YmexwaSYhRzBg5Ts4g_3kqtgL62",
  TIENDA:        "ragu",
  // Cuántos meses hacia atrás traer al cargar. El dashboard pinta histórico
  // por año/mes, no tiene sentido bajar 5 años a cada carga.
  MESES_HISTORIA: 18
};
