"use client";

export default function BotaoImprimir() {
  return (
    <button type="button" className="btn small no-print" onClick={() => window.print()}>
      Baixar PDF
    </button>
  );
}
