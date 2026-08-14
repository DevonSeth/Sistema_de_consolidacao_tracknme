// Indicador de frescor do dado (2026-08-14, achado da revisão de lógica:
// nenhuma tela dizia se o número na tela era de agora ou de horas atrás).
// Server Component puro — `iso` já vem resolvido de `buscarUltimaAtualizacao`
// (webapp/src/lib/dashboard-metrics.ts), lendo `log_execucoes` da
// Observabilidade fatia 1. Sem nada renderizado se `iso` for `null`
// (nenhuma execução registrada ainda).
const FORMATADOR = new Intl.DateTimeFormat("pt-BR", {
  timeZone: "America/Recife",
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

export default function UltimaAtualizacao({ iso }: { iso: string | null }) {
  if (!iso) return null;

  const partes = Object.fromEntries(
    FORMATADOR.formatToParts(new Date(iso)).map((p) => [p.type, p.value])
  );
  const data = `${partes.day}/${partes.month}/${partes.year}`;
  const hora = `${partes.hour}:${partes.minute}`;

  return (
    <div className="atualizado-em">
      Dados atualizados em {data} às {hora}
    </div>
  );
}
