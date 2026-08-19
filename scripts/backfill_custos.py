#!/usr/bin/env python
"""
Preenche `cost_usd` das linhas de `usage_logs` que nasceram sem preço.

Até 2026-08-19 `save_usage_log` gravava `cost_usd=0.0` em TODA linha, porque o
parâmetro existia e nenhum chamador passava valor. Essas linhas ficam para trás:
a migration 036 não as toca de propósito (migration que falha derruba o boot, e
calcular preço exige a tabela de preços dentro dela).

**Limite conhecido, e é o motivo de isso não rodar sozinho:** as linhas antigas
não têm os tokens de cache — as colunas não existiam. O custo reconstruído aqui
soma só entrada não-cacheada + saída, então é um PISO, não o valor exato. Em
conversa com prefixo cacheado o real é maior. Por isso o script marca o que fez
no log e exige `--aplicar` para escrever.

    python scripts/backfill_custos.py                  # simula, não grava
    python scripts/backfill_custos.py --aplicar
    python scripts/backfill_custos.py --aplicar --location jVxHh2Elz8MxMurLzwzz
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from data.database import SessionLocal          # noqa: E402
from data.models import UsageLog                # noqa: E402
from services.precos import custo               # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Recalcula cost_usd de linhas antigas.")
    ap.add_argument("--aplicar", action="store_true", help="grava (sem isto, só simula)")
    ap.add_argument("--location", help="limita a um tenant")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        q = db.query(UsageLog).filter(UsageLog.cost_usd.is_(None) | (UsageLog.cost_usd == 0.0))
        if args.location:
            q = q.filter(UsageLog.location_id == args.location)
        linhas = q.all()

        precificadas = 0
        sem_preco = 0
        total = 0.0
        for log in linhas:
            valor = custo(
                service=log.service,
                model=log.model,
                input_tokens=log.input_tokens or 0,
                output_tokens=log.output_tokens or 0,
                cache_read_tokens=getattr(log, "cache_read_tokens", 0) or 0,
                cache_write_tokens=getattr(log, "cache_write_tokens", 0) or 0,
                characters=log.characters or 0,
                buscas_web=getattr(log, "buscas_web", 0) or 0,
                # A tarifa é a da ÉPOCA da chamada: usar a de hoje faria a promoção
                # do Sonnet 5 sumir do histórico assim que ela vencesse.
                quando=log.created_at.date() if log.created_at else None,
            )
            if valor is None:
                sem_preco += 1
                continue
            precificadas += 1
            total += valor
            if args.aplicar:
                log.cost_usd = valor

        if args.aplicar:
            db.commit()

        modo = "GRAVADO" if args.aplicar else "SIMULACAO (use --aplicar para gravar)"
        print(f"{modo}")
        print(f"  linhas examinadas : {len(linhas)}")
        print(f"  precificadas      : {precificadas}  →  ${total:.6f}")
        print(f"  sem preco tabelado: {sem_preco}  (ficam NULL; adicione o modelo em services/precos.py)")
        if precificadas:
            print("  atencao: linhas anteriores a 2026-08-19 nao tem tokens de cache;")
            print("           o valor acima e um PISO do que foi realmente cobrado.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
