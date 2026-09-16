#!/usr/bin/env python3
"""
Coleta diária dos rendimentos dos títulos do Tesouro Direto.

Busca o JSON público que o próprio site do Tesouro Direto usa para montar a
página "Rendimento dos Títulos" e acrescenta um novo retrato (snapshot) do
dia ao histórico acumulado em data/historico.json (e data/historico.csv).

Uso:
    python scripts/coletar.py
"""

from __future__ import annotations

import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

URL_API = (
    "https://www.tesourodireto.com.br/json/br/com/b3/tesourodireto/"
    "service/api/treasurybondsinfo.json"
)
FUSO_BR = ZoneInfo("America/Sao_Paulo")

RAIZ = Path(__file__).resolve().parent.parent
ARQ_JSON = RAIZ / "data" / "historico.json"
ARQ_CSV = RAIZ / "data" / "historico.csv"

# Um User-Agent de navegador real reduz a chance de o Cloudflare do site
# bloquear a chamada por parecer tráfego de robô.
CABECALHOS_HTTP = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Referer": (
        "https://www.tesourodireto.com.br/produtos/dados-sobre-titulos/"
        "rendimento-dos-titulos"
    ),
}


def buscar_dados(tentativas: int = 4, espera_seg: float = 6.0) -> dict:
    """Busca o JSON de rendimentos, tentando de novo em caso de bloqueio."""
    ultimo_erro: Exception | None = None
    for tentativa in range(1, tentativas + 1):
        try:
            resp = requests.get(URL_API, headers=CABECALHOS_HTTP, timeout=20)
            resp.raise_for_status()
            return resp.json()
        except Exception as erro:  # noqa: BLE001 - queremos tentar de novo
            ultimo_erro = erro
            print(f"[tentativa {tentativa}/{tentativas}] falhou: {erro}")
            if tentativa < tentativas:
                time.sleep(espera_seg)
    raise RuntimeError(
        "Não foi possível obter os dados do Tesouro Direto após várias "
        f"tentativas (último erro: {ultimo_erro}). O site costuma bloquear "
        "chamadas automatizadas esporadicamente (proteção Cloudflare); "
        "tente novamente mais tarde ou rode o workflow manualmente."
    )


def extrair_titulos(dados: dict) -> list[dict]:
    """Converte o JSON bruto da API numa lista simples de títulos/rendimentos."""
    lista_bruta = dados.get("response", {}).get("TrsrBdTradgList", [])
    titulos = []
    for item in lista_bruta:
        bd = item.get("TrsrBd", {})
        indexador = (bd.get("FinIndxs") or {}).get("nm")
        titulos.append(
            {
                "nome": bd.get("nm"),
                "isin": bd.get("isinCd"),
                "indexador": indexador,
                "vencimento": (bd.get("mtrtyDt") or "")[:10] or None,
                "taxaCompra": bd.get("anulInvstmtRate"),
                "taxaVenda": bd.get("anulRedRate"),
                "puCompra": bd.get("untrInvstmtVal"),
                "puVenda": bd.get("untrRedVal"),
                "investimentoMinimo": bd.get("minInvstmtAmt"),
            }
        )
    return [t for t in titulos if t["nome"]]


def carregar_historico(caminho: Path) -> list[dict]:
    if not caminho.exists():
        return []
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print(f"aviso: {caminho} estava corrompido/ilegível; começando histórico novo")
        return []


def atualizar_historico(historico: list[dict], snapshot: dict) -> list[dict]:
    """Insere o snapshot do dia, substituindo qualquer coleta já feita na mesma data."""
    historico = [h for h in historico if h.get("data") != snapshot["data"]]
    historico.append(snapshot)
    historico.sort(key=lambda h: h["data"])
    return historico


def salvar_json(caminho: Path, historico: list[dict]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(
        json.dumps(historico, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def salvar_csv(caminho: Path, historico: list[dict]) -> None:
    colunas = [
        "data", "coletadoEm", "nome", "indexador", "vencimento",
        "taxaCompra", "taxaVenda", "puCompra", "puVenda",
        "investimentoMinimo", "isin",
    ]
    with caminho.open("w", newline="", encoding="utf-8") as f:
        escritor = csv.DictWriter(f, fieldnames=colunas)
        escritor.writeheader()
        for snapshot in historico:
            for titulo in snapshot.get("titulos", []):
                linha = {
                    "data": snapshot["data"],
                    "coletadoEm": snapshot.get("coletadoEm"),
                    **titulo,
                }
                escritor.writerow({c: linha.get(c) for c in colunas})


def main() -> None:
    agora = datetime.now(FUSO_BR)
    dados = buscar_dados()
    titulos = extrair_titulos(dados)
    if not titulos:
        raise RuntimeError("A API respondeu, mas nenhum título foi encontrado no JSON.")

    snapshot = {
        "data": agora.strftime("%Y-%m-%d"),
        "coletadoEm": agora.isoformat(timespec="seconds"),
        "titulos": titulos,
    }

    historico = carregar_historico(ARQ_JSON)
    historico = atualizar_historico(historico, snapshot)
    salvar_json(ARQ_JSON, historico)
    salvar_csv(ARQ_CSV, historico)

    print(f"OK: {len(titulos)} títulos salvos para {snapshot['data']} em {ARQ_JSON}")


if __name__ == "__main__":
    try:
        main()
    except Exception as erro:  # noqa: BLE001
        print(f"ERRO: {erro}", file=sys.stderr)
        sys.exit(1)
