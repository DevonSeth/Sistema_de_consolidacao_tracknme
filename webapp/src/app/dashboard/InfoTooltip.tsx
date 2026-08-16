"use client";

import { useEffect, useId, useRef, useState } from "react";

/** Ícone "?" com explicação da métrica — ancorado ao ícone (não segue o
 * mouse, diferente do `.viz-tooltip` de gráfico). Cobre mouse (hover),
 * teclado (foco) e touch (tap) sem conflito: `onClick` só abre, nunca
 * alterna — fechar é sempre via mouseleave/blur/clique-fora/Escape. */
export default function InfoTooltip({ texto, label }: { texto: string; label: string }) {
  const [aberto, setAberto] = useState(false);
  const id = useId();
  const ref = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!aberto) return;
    function aoClicarFora(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setAberto(false);
    }
    function aoTeclar(e: KeyboardEvent) {
      if (e.key === "Escape") setAberto(false);
    }
    document.addEventListener("click", aoClicarFora);
    document.addEventListener("keydown", aoTeclar);
    return () => {
      document.removeEventListener("click", aoClicarFora);
      document.removeEventListener("keydown", aoTeclar);
    };
  }, [aberto]);

  return (
    <span className="info-tooltip no-print" ref={ref}>
      <button
        type="button"
        className="info-tooltip-botao"
        aria-label={`Mais informações sobre ${label}`}
        aria-expanded={aberto}
        aria-describedby={aberto ? id : undefined}
        onMouseEnter={() => setAberto(true)}
        onMouseLeave={() => setAberto(false)}
        onFocus={() => setAberto(true)}
        onBlur={() => setAberto(false)}
        onClick={(e) => {
          e.stopPropagation();
          setAberto(true);
        }}
      >
        ?
      </button>
      {aberto && (
        <span role="tooltip" id={id} className="info-tooltip-balao">
          {texto}
        </span>
      )}
    </span>
  );
}
