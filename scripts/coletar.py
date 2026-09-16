#!/usr/bin/env python3
"""
Coleta diária dos rendimentos dos títulos do Tesouro Direto.

Fonte dos dados: o dataset público "Taxas dos Títulos Ofertados pelo Tesouro
Direto", publicado em CSV pelo Tesouro Transparente (portal oficial de dados
abertos do Tesouro Nacional). É o mesmo dado que o site tesourodireto.com.br
usa, mas por um canal de dados abertos mais estável do que o endpoint interno
do site (que foi descontinuado).

O CSV traz o histórico completo (todas as datas já publicadas); este script
filtra apenas o dia mais recente disponível nele e acrescenta esse retrato
(snapshot) ao histórico acumulado em data/historico.json (e
data/historico.csv).

Uso:
    python scripts/coletar.py
"""

from __future__ import annotations

import csv
import io
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

URL_CSV = (
    "https://www.tesourotransparente.gov.br/ckan/dataset/"
    "df56aa42-484a-4a59-8184-7676580c81e3/resource/"
    "796d2059-14e9-44e3-80c9-2d9e30b405c1/download/PrecoTaxaTesouroDireto.csv"
)
FUSO_BR = ZoneInfo("America/Sao_Paulo")

RAIZ = Path(__file__).resolve().parent.parent
ARQ_JSON = RAIZ / "data" / "historico.json"
ARQ_CSV = RAIZ / "data" / "historico.csv"

CABECALHOS_HTTP = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,*/*",
}

# Indexador aproximado a partir do nome do tipo de título (o CSV não traz
# essa coluna diretamente).
_PISTAS_INDEXADOR = [
    ("Selic", "SELIC"),
    ("IPCA", "IPCA"),
    ("IGPM", "IGP-M"),
    ("Educa+", "IPCA"),
    ("Renda+", "IPCA"),
    ("Prefixado", "PREFIXADO"),
]


def _indexador_para(tipo_titulo: str) -> str | None:
    for pista, nome in _PISTAS_INDEXADOR:
        if pista.lower() in tipo_titulo.lower():
            return nome
    return None


def _num_br(valor: str) -> float | None:
    """Converte '14,23' (formato brasileiro) em 14.23; vazio vira None."""
    valor = (valor or "").strip()
    if not valor:
        return None
    return float(valor.replace(".", "").replace(",", "."))


def _data_br_para_iso(data_br: str) -> str | None:
    """Converte 'dd/mm/aaaa' em 'aaaa-mm-dd'."""
    data_br = (data_br or "").strip()
    if not data_br:
        return None
    dia, mes, ano = data_br.split("/")
    return f"{ano}-{mes}-{dia}"


def buscar_csv(tentativas: int = 4, espera_seg: float = 6.0) -> str:
    """Baixa o CSV completo do Tesouro Transparente, com novas tentativas."""
    ultimo_erro: Exception | None = None
    for tentativa in range(1, tentativas + 1):
        try:
            resp = requests.get(URL_CSV, headers=CABECALHOS_HTTP, timeout=60)
            resp.raise_for_status()
            resp.encoding = resp.encoding or "utf-8"
            return resp.text
        except Exception as erro:  # noqa: BLE001 - queremos tentar de novo
            ultimo_erro = erro
            print(f"[tentativa {tentativa}/{tentativas}] falhou: {erro}")
            if tentativa < tentativas:
                time.sleep(espera_seg)
    raise RuntimeError(
        "Não foi possível baixar o CSV do Tesouro Transparente após várias "
        f"tentativas (último erro: {ultimo_erro}). Tente novamente mais "
        "tarde ou rode o workflow manualmente."
    )


def extrair_titulos_do_dia_mais_recente(texto_csv: str) -> tuple[str, list[dict]]:
    """Lê o CSV completo e devolve (data_iso_mais_recente, títulos daquele dia)."""
    leitor = csv.DictReader(io.StringIO(texto_csv), delimiter=";")

    linhas_por_data: dict[str, list[dict]] = {}
    for linha in leitor:
        data_iso = _data_br_para_iso(linha.get("Data Base", ""))
        tipo_titulo = (linha.get("Tipo Titulo") or "").strip()
        vencimento_iso = _data_br_para_iso(linha.get("Data Vencimento", ""))
        if not data_iso or not tipo_titulo or not vencimento_iso:
            continue

        ano_vencimento = vencimento_iso[:4]
        titulo = {
            "nome": f"{tipo_titulo} {ano_vencimento}",
            "isin": None,
            "indexador": _indexador_para(tipo_titulo),
            "vencimento": vencimento_iso,
            "taxaCompra": _valor_ou_none(_num_br(linha.get("Taxa Compra Manha", "")), escala=0.01),
            "taxaVenda": _valor_ou_none(_num_br(linha.get("Taxa Venda Manha", "")), escala=0.01),
            "puCompra": _num_br(linha.get("PU Compra Manha", "")),
            "puVenda": _num_br(linha.get("PU Venda Manha", "")),
            "investimentoMinimo": None,
        }
        linhas_por_data.setdefault(data_iso, []).append(titulo)

    if not linhas_por_data:
        raise RuntimeError("O CSV foi baixado, mas nenhuma linha válida foi encontrada.")

    data_mais_recente = max(linhas_por_data.keys())
    return data_mais_recente, linhas_por_data[data_mais_recente]


def _valor_ou_none(valor: float | None, escala: float) -> float | None:
    if valor is None:
        return None
    return valor * escala


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
    texto_csv = buscar_csv()
    data_referencia, titulos = extrair_titulos_do_dia_mais_recente(texto_csv)

    snapshot = {
        "data": data_referencia,
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
