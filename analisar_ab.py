#!/usr/bin/env python3
"""
analisar_ab.py — Análise de teste A/B de cashback (Méliuz).

Uso:
    python analisar_ab.py --file dataset_01_parceiroA.csv \
        --nome "Cashback Parceiro A" \
        --descricao "Teste de 3 variantes de cashback no Parceiro A"

Funciona para qualquer CSV que siga o schema:
Data, Grupos de usuários, Parceiro, compradores, comissão, cashback, vendas totais
— com 2, 3 ou mais variantes — sem alteração de código.

Opcional:
    ANTHROPIC_API_KEY no ambiente -> narrativa do relatório escrita por Claude.
    --sheet-id + GOOGLE_SERVICE_ACCOUNT_JSON -> grava direto no Google Sheets.
    Sem isso, tudo funciona igual usando um CSV local (resultados_testes.csv).
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats

CURRENCY_COLS = ["comissão", "cashback", "vendas totais"]
REQUIRED_COLS = ["Data", "Grupos de usuários", "Parceiro", "compradores",
                  "comissão", "cashback", "vendas totais"]


# --------------------------------------------------------------------------- #
# 1. LIMPEZA E CARGA
# --------------------------------------------------------------------------- #

@dataclass
class CleaningReport:
    linhas_originais: int = 0
    linhas_finais: int = 0
    linhas_duplicadas_removidas: int = 0
    linhas_com_valor_invalido_removidas: int = 0
    linhas_com_data_invalida_removidas: int = 0
    linhas_negativas_removidas: int = 0
    grupos_normalizados: dict = field(default_factory=dict)
    avisos: list = field(default_factory=list)

    def resumo(self) -> str:
        linhas = [f"- Linhas originais: {self.linhas_originais}",
                  f"- Linhas finais (válidas): {self.linhas_finais}"]
        if self.linhas_duplicadas_removidas:
            linhas.append(f"- Duplicatas removidas: {self.linhas_duplicadas_removidas}")
        if self.linhas_com_valor_invalido_removidas:
            linhas.append(f"- Linhas com valor inválido removidas: {self.linhas_com_valor_invalido_removidas}")
        if self.linhas_com_data_invalida_removidas:
            linhas.append(f"- Linhas com data inválida removidas: {self.linhas_com_data_invalida_removidas}")
        if self.linhas_negativas_removidas:
            linhas.append(f"- Linhas com valores negativos removidas: {self.linhas_negativas_removidas}")
        if self.grupos_normalizados:
            linhas.append(f"- Nomes de grupo normalizados: {self.grupos_normalizados}")
        for a in self.avisos:
            linhas.append(f"- ⚠️ {a}")
        return "\n".join(linhas)


def _parse_brl(value) -> float:
    """'R$ 10.273' -> 10273.0 (ponto = milhar). 'R$ 1.234,56' -> 1234.56.
    Nunca lança exceção; retorna NaN se não conseguir converter."""
    if pd.isna(value):
        return np.nan
    if isinstance(value, (int, float)):
        return float(value)
    s = re.sub(r"[Rr]\$", "", str(value)).strip()
    if s == "":
        return np.nan
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    elif "." in s:
        head, tail = s.rsplit(".", 1)
        if len(tail) == 3 and head:
            s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return np.nan


def _normalize_group_name(raw) -> str:
    if pd.isna(raw):
        return raw
    s = str(raw).strip()
    m = re.match(r"(?i)^grupo\s*(\d+)$", s)
    return f"Grupo {int(m.group(1))}" if m else s


def load_and_clean(csv_path: str | Path) -> tuple[pd.DataFrame, CleaningReport]:
    csv_path = Path(csv_path)
    report = CleaningReport()
    df = pd.read_csv(csv_path, dtype=str)
    report.linhas_originais = len(df)

    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"{csv_path.name} não segue o schema esperado. Colunas ausentes: {missing}")

    before = len(df)
    df = df.drop_duplicates()
    report.linhas_duplicadas_removidas = before - len(df)

    original = df["Grupos de usuários"].copy()
    df["Grupos de usuários"] = df["Grupos de usuários"].apply(_normalize_group_name)
    changed = original[original != df["Grupos de usuários"]]
    if len(changed):
        report.grupos_normalizados = dict(zip(changed.unique(), df.loc[changed.index, "Grupos de usuários"].unique()))

    df["Data"] = pd.to_datetime(df["Data"], errors="coerce")
    bad_dates = df["Data"].isna().sum()
    if bad_dates:
        report.linhas_com_data_invalida_removidas = int(bad_dates)
        df = df[df["Data"].notna()]

    df["compradores"] = pd.to_numeric(df["compradores"], errors="coerce")
    for col in CURRENCY_COLS:
        df[col] = df[col].apply(_parse_brl)

    numeric_cols = ["compradores"] + CURRENCY_COLS
    invalid_mask = df[numeric_cols].isna().any(axis=1)
    report.linhas_com_valor_invalido_removidas = int(invalid_mask.sum())
    df = df[~invalid_mask]

    neg_mask = (df[numeric_cols] < 0).any(axis=1)
    report.linhas_negativas_removidas = int(neg_mask.sum())
    df = df[~neg_mask]

    if df["Grupos de usuários"].nunique() < 2:
        report.avisos.append("Menos de 2 grupos válidos após a limpeza — não é possível comparar variantes.")

    report.linhas_finais = len(df)
    df = df.sort_values("Data").reset_index(drop=True)

    # comissão == cashback quase sempre -> repasse integral, lucro zera por construção
    for grupo, g in df.groupby("Grupos de usuários"):
        if len(g) >= 5 and (g["comissão"] == g["cashback"]).mean() >= 0.95:
            report.avisos.append(
                f"{grupo}: comissão == cashback em quase todos os dias -> repasse integral (100%), "
                f"lucro líquido ~R$0 POR CONSTRUÇÃO, não por performance fraca."
            )
    return df, report


# --------------------------------------------------------------------------- #
# 2. MÉTRICAS
# --------------------------------------------------------------------------- #

@dataclass
class GroupMetrics:
    grupo: str
    dias: int
    total_compradores: int
    total_comissao: float
    total_cashback: float
    total_vendas: float
    lucro_liquido: float
    margem_sobre_vendas_pct: float
    ticket_medio: float
    lucro_liquido_dia_medio: float


def compute_group_metrics(df: pd.DataFrame) -> list[GroupMetrics]:
    out = []
    for grupo, g in df.groupby("Grupos de usuários"):
        total_compradores = int(g["compradores"].sum())
        total_comissao = float(g["comissão"].sum())
        total_cashback = float(g["cashback"].sum())
        total_vendas = float(g["vendas totais"].sum())
        lucro = total_comissao - total_cashback
        dias = g["Data"].nunique()
        out.append(GroupMetrics(
            grupo=grupo, dias=dias, total_compradores=total_compradores,
            total_comissao=total_comissao, total_cashback=total_cashback, total_vendas=total_vendas,
            lucro_liquido=lucro,
            margem_sobre_vendas_pct=(lucro / total_vendas * 100) if total_vendas else np.nan,
            ticket_medio=(total_vendas / total_compradores) if total_compradores else np.nan,
            lucro_liquido_dia_medio=lucro / dias if dias else np.nan,
        ))
    return sorted(out, key=lambda m: m.grupo)


# --------------------------------------------------------------------------- #
# 3. SIGNIFICÂNCIA ESTATÍSTICA
# --------------------------------------------------------------------------- #

@dataclass
class PairwiseTest:
    grupo_a: str
    grupo_b: str
    metrica: str
    media_a: float
    media_b: float
    diff_relativa_pct: float
    p_value: float
    significativo: bool


def run_pairwise_tests(df: pd.DataFrame, alpha: float = 0.05) -> list[PairwiseTest]:
    """Cada dia = uma observação. Welch t-test + Mann-Whitney; só é
    'significativo' se os dois concordarem em p<alpha (mais conservador)."""
    df = df.copy()
    df["lucro_liquido_dia"] = df["comissão"] - df["cashback"]
    metricas = ["compradores", "vendas totais", "lucro_liquido_dia"]
    grupos = sorted(df["Grupos de usuários"].unique())
    results = []
    for i in range(len(grupos)):
        for j in range(i + 1, len(grupos)):
            ga, gb = grupos[i], grupos[j]
            for metrica in metricas:
                a = df.loc[df["Grupos de usuários"] == ga, metrica].dropna().to_numpy()
                b = df.loc[df["Grupos de usuários"] == gb, metrica].dropna().to_numpy()
                if len(a) < 2 or len(b) < 2:
                    continue
                t_p = stats.ttest_ind(a, b, equal_var=False).pvalue
                u_p = stats.mannwhitneyu(a, b, alternative="two-sided").pvalue
                media_a, media_b = float(np.mean(a)), float(np.mean(b))
                diff_pct = ((media_b - media_a) / media_a * 100) if media_a else np.nan
                results.append(PairwiseTest(
                    grupo_a=ga, grupo_b=gb, metrica=metrica, media_a=media_a, media_b=media_b,
                    diff_relativa_pct=diff_pct, p_value=max(t_p, u_p),
                    significativo=bool(t_p < alpha and u_p < alpha),
                ))
    return results


# --------------------------------------------------------------------------- #
# 4. DECISÃO
# --------------------------------------------------------------------------- #

@dataclass
class Decisao:
    variante_recomendada: Optional[str]
    confianca: str
    justificativa: str


def decide_winner(group_metrics: list[GroupMetrics], tests: list[PairwiseTest]) -> Decisao:
    """Critério: maior lucro líquido/dia (comissão - cashback). Confiança
    'alta' só se a diferença for significativa contra TODAS as demais."""
    if not group_metrics:
        return Decisao(None, "baixa / inconclusivo", "Sem dados suficientes.")

    ranked = sorted(group_metrics, key=lambda m: m.lucro_liquido_dia_medio, reverse=True)
    top = ranked[0]
    relevant = [t for t in tests if t.metrica == "lucro_liquido_dia" and top.grupo in (t.grupo_a, t.grupo_b)]

    if not relevant:
        return Decisao(top.grupo, "baixa / inconclusivo",
                        f"{top.grupo} tem o maior lucro líquido médio/dia (R$ {top.lucro_liquido_dia_medio:,.2f}), "
                        f"mas não foi possível testar significância (dados insuficientes).")

    all_sig = all(t.significativo for t in relevant)
    any_sig = any(t.significativo for t in relevant)

    if all_sig:
        return Decisao(top.grupo, "alta",
                        f"{top.grupo} tem o maior lucro líquido/dia (R$ {top.lucro_liquido_dia_medio:,.2f}) "
                        f"e a diferença é significativa (p<0.05) contra todas as demais variantes.")
    if any_sig:
        return Decisao(top.grupo, "média",
                        f"{top.grupo} tem o maior lucro líquido/dia (R$ {top.lucro_liquido_dia_medio:,.2f}), "
                        f"significativo contra algumas variantes mas não todas. Escalar com monitoramento "
                        f"ou estender o teste.")
    return Decisao(top.grupo, "baixa / inconclusivo",
                    f"{top.grupo} lidera nominalmente (R$ {top.lucro_liquido_dia_medio:,.2f}/dia), mas a "
                    f"diferença não é estatisticamente significativa (p≥0.05). Não escalar ainda.")


# --------------------------------------------------------------------------- #
# 5. RELATÓRIO
# --------------------------------------------------------------------------- #

SYSTEM_PROMPT = """Você é um analista de growth sênior do Méliuz escrevendo o \
resumo executivo de um teste A/B de cashback para um gestor que tem 2 minutos \
para ler. Use APENAS os números fornecidos, não invente nada. Não repita a \
tabela de números. Seja direto sobre riscos/limitações. Termine com uma \
recomendação de negócio em uma frase, em negrito. Português do Brasil, \
120-180 palavras, sem emojis."""


def _fmt(v: float) -> str:
    return f"R$ {v:,.2f}"


def _metrics_table(metrics: list[GroupMetrics]) -> str:
    header = ("| Grupo | Dias | Compradores | Comissão | Cashback | Vendas totais | "
               "Lucro líquido | Margem | Ticket médio | Lucro líq./dia |\n|---|---|---|---|---|---|---|---|---|---|\n")
    rows = [f"| {m.grupo} | {m.dias} | {m.total_compradores} | {_fmt(m.total_comissao)} | "
            f"{_fmt(m.total_cashback)} | {_fmt(m.total_vendas)} | {_fmt(m.lucro_liquido)} | "
            f"{m.margem_sobre_vendas_pct:.1f}% | {_fmt(m.ticket_medio)} | {_fmt(m.lucro_liquido_dia_medio)} |"
            for m in metrics]
    return header + "\n".join(rows)


def _tests_table(tests: list[PairwiseTest]) -> str:
    if not tests:
        return "_Sem testes estatísticos (dados insuficientes)._"
    header = "| Comparação | Métrica | Média A | Média B | Diferença | p-value | Significativo? |\n|---|---|---|---|---|---|---|\n"
    rows = [f"| {t.grupo_a} vs {t.grupo_b} | {t.metrica} | {t.media_a:,.1f} | {t.media_b:,.1f} | "
            f"{t.diff_relativa_pct:+.1f}% | {t.p_value:.4f} | {'✅ Sim' if t.significativo else '❌ Não'} |"
            for t in tests]
    return header + "\n".join(rows)


def _deterministic_summary(metrics: list[GroupMetrics], decisao: Decisao) -> str:
    ranked = sorted(metrics, key=lambda m: m.lucro_liquido_dia_medio, reverse=True)
    top, rest = ranked[0], ranked[1:]
    comp = "; ".join(f"{r.grupo} R$ {r.lucro_liquido_dia_medio:,.2f}/dia" for r in rest)
    acao = (f"escalar {decisao.variante_recomendada} para 100% do tráfego."
            if decisao.confianca == "alta" else
            f"NÃO escalar ainda — manter o teste rodando ({decisao.variante_recomendada} à frente, "
            f"mas sem confiança estatística suficiente).")
    return (f"O {top.grupo} teve o maior lucro líquido médio diário, R$ {top.lucro_liquido_dia_medio:,.2f}, "
            f"contra {comp or 'nenhuma outra variante comparável'}. Ticket médio de R$ {top.ticket_medio:,.2f} "
            f"e margem de {top.margem_sobre_vendas_pct:.1f}%. Confiança: {decisao.confianca}. "
            f"{decisao.justificativa}\n\n**Recomendação: {acao}**")


def _try_llm_summary(metrics_table, tests_table, decisao, nome_teste) -> str | None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        user_prompt = (f"Teste: {nome_teste}\n\nMétricas:\n{metrics_table}\n\nTestes estatísticos:\n{tests_table}\n\n"
                        f"Decisão do sistema: {decisao.variante_recomendada}, confiança {decisao.confianca}. "
                        f"Justificativa: {decisao.justificativa}\n\nEscreva o resumo executivo.")
        resp = client.messages.create(model="claude-sonnet-4-6", max_tokens=500,
                                       system=SYSTEM_PROMPT, messages=[{"role": "user", "content": user_prompt}])
        return "".join(b.text for b in resp.content if b.type == "text").strip()
    except Exception as e:
        print(f"[aviso] narrativa via LLM falhou, usando fallback local: {e}", file=sys.stderr)
        return None


def generate_report(*, nome_teste, descricao, csv_path, cleaning, metrics, tests, decisao, out_dir) -> Path:
    metrics_table = _metrics_table(metrics)
    tests_table = _tests_table(tests)
    narrativa = _try_llm_summary(metrics_table, tests_table, decisao, nome_teste)
    fonte = "Claude (API)" if narrativa else "gerador determinístico local"
    narrativa = narrativa or _deterministic_summary(metrics, decisao)

    md = f"""# Relatório de Teste A/B — {nome_teste}

**Descrição:** {descricao}
**Arquivo fonte:** `{Path(csv_path).name}`
**Gerado em:** {datetime.now().strftime('%d/%m/%Y %H:%M')}
**Variantes analisadas:** {len(metrics)}

---

## Resumo executivo

{narrativa}

*(narrativa gerada por: {fonte})*

---

## Decisão

- **Variante recomendada:** {decisao.variante_recomendada}
- **Confiança:** {decisao.confianca}
- **Justificativa:** {decisao.justificativa}

---

## Métricas por variante

{metrics_table}

*Lucro líquido = comissão − cashback (o que sobra pro Méliuz). É o critério principal — não vendas ou compradores isolados, pois cashback alto pode inflar as duas sem sobrar receita.*

---

## Significância estatística (Welch t-test + Mann-Whitney, base diária)

{tests_table}

---

## Qualidade dos dados

{cleaning.resumo()}
"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"relatorio_{nome_teste.lower().replace(' ', '_')}.md"
    out_path.write_text(md, encoding="utf-8")
    return out_path


# --------------------------------------------------------------------------- #
# 6. REGISTRO (Google Sheets opcional, formatado / CSV local)
# --------------------------------------------------------------------------- #

COLUNAS = ["Nome do teste", "Descrição", "Data da análise", "Variante recomendada",
           "Confiança", "Lucro líq./dia (vencedor)", "Ticket médio (vencedor)",
           "Resultado por variante", "Decisão", "Arquivo fonte", "Relatório"]

CONF_COLOR = {
    "alta": {"red": 0.80, "green": 0.94, "blue": 0.80},              # verde claro
    "média": {"red": 1.0, "green": 0.95, "blue": 0.75},               # amarelo claro
    "baixa / inconclusivo": {"red": 1.0, "green": 0.85, "blue": 0.85},  # vermelho claro
}
HEADER_BG = {"red": 0.16, "green": 0.29, "blue": 0.49}   # azul escuro
HEADER_FG = {"red": 1.0, "green": 1.0, "blue": 1.0}      # branco


def _build_row(nome_teste, descricao, metrics, decisao, csv_path, report_path) -> list:
    ranked = sorted(metrics, key=lambda m: m.lucro_liquido_dia_medio, reverse=True)
    top = ranked[0]
    resultado = " | ".join(f"{m.grupo}: R$ {m.lucro_liquido_dia_medio:,.2f}/dia, "
                            f"ticket R$ {m.ticket_medio:,.2f}" for m in ranked)
    decisao_txt = (f"Escalar {decisao.variante_recomendada} para 100% do tráfego" if decisao.confianca == "alta"
                    else f"Manter teste rodando ({decisao.variante_recomendada} à frente, confiança {decisao.confianca})")
    return [nome_teste, descricao, datetime.now().strftime("%Y-%m-%d %H:%M"),
            decisao.variante_recomendada or "n/d", decisao.confianca,
            f"R$ {top.lucro_liquido_dia_medio:,.2f}", f"R$ {top.ticket_medio:,.2f}",
            resultado, decisao_txt, Path(csv_path).name, str(report_path)]


def _ensure_csv_header(csv_out: Path) -> None:
    """Garante que a primeira linha do CSV seja o cabeçalho correto —
    mesmo se o arquivo já existia (de uma versão antiga) sem cabeçalho ou
    com cabeçalho desatualizado. Nunca duplica dados."""
    if not csv_out.exists():
        return
    with open(csv_out, "r", newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if rows and rows[0] == COLUNAS:
        return  # já está correto
    with open(csv_out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(COLUNAS)
        for r in rows:
            if r and r != COLUNAS:
                writer.writerow(r)


def _append_csv(row, csv_out: str | Path) -> Path:
    csv_out = Path(csv_out)
    if csv_out.exists():
        _ensure_csv_header(csv_out)
    else:
        with open(csv_out, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(COLUNAS)
    with open(csv_out, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)
    return csv_out


def _get_sheets_client(sheet_id: str):
    creds_path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not creds_path or not sheet_id:
        return None
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_file(
        creds_path, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    gc = gspread.authorize(creds)
    return gc.open_by_key(sheet_id)


def _format_summary_header(ws) -> None:
    """Aplica formatação visual na primeira aba (cabeçalho em negrito/azul,
    linha congelada, largura das colunas)."""
    ws.format(f"A1:{chr(64 + len(COLUNAS))}1", {
        "backgroundColor": HEADER_BG,
        "textFormat": {"bold": True, "foregroundColor": HEADER_FG, "fontSize": 11},
        "horizontalAlignment": "CENTER",
        "wrapStrategy": "WRAP",
    })
    try:
        ws.freeze(rows=1)
    except Exception:
        pass


def _color_confidence_cell(ws, row_number: int, confianca: str) -> None:
    color = CONF_COLOR.get(confianca)
    if not color:
        return
    col_letter = chr(64 + COLUNAS.index("Confiança") + 1)
    try:
        ws.format(f"{col_letter}{row_number}", {
            "backgroundColor": color,
            "textFormat": {"bold": True},
            "horizontalAlignment": "CENTER",
        })
    except Exception:
        pass


def _sanitize_sheet_name(nome_teste: str) -> str:
    safe = re.sub(r"[\\/*\[\]:?]", "-", nome_teste).strip()
    return safe[:95] or "Teste"


def _write_detail_tab(spreadsheet, nome_teste: str, metrics: list[GroupMetrics],
                       tests: list[PairwiseTest], decisao: Decisao, descricao: str) -> None:
    """Cria (ou substitui) uma aba dedicada a este teste, com a tabela
    completa de métricas por variante e os testes estatísticos —
    exatamente o mesmo conteúdo do relatório .md, só que na planilha."""
    title = _sanitize_sheet_name(nome_teste)
    try:
        existing = spreadsheet.worksheet(title)
        spreadsheet.del_worksheet(existing)
    except Exception:
        pass

    n_rows = 8 + len(metrics) + len(tests) + 6
    ws = spreadsheet.add_worksheet(title=title, rows=str(max(n_rows, 20)), cols="10")

    rows: list[list] = []
    rows.append([f"Teste: {nome_teste}"])
    rows.append([f"Descrição: {descricao}"])
    rows.append([f"Decisão: {decisao.variante_recomendada} (confiança: {decisao.confianca})"])
    rows.append([decisao.justificativa])
    rows.append([])
    rows.append(["Grupo", "Dias", "Compradores", "Comissão", "Cashback", "Vendas totais",
                 "Lucro líquido", "Margem %", "Ticket médio", "Lucro líq./dia"])
    for m in metrics:
        rows.append([m.grupo, m.dias, m.total_compradores, round(m.total_comissao, 2),
                     round(m.total_cashback, 2), round(m.total_vendas, 2), round(m.lucro_liquido, 2),
                     round(m.margem_sobre_vendas_pct, 1), round(m.ticket_medio, 2),
                     round(m.lucro_liquido_dia_medio, 2)])
    rows.append([])
    rows.append(["Comparação", "Métrica", "Média A", "Média B", "Diferença %", "p-value", "Significativo?"])
    for t in tests:
        rows.append([f"{t.grupo_a} vs {t.grupo_b}", t.metrica, round(t.media_a, 1), round(t.media_b, 1),
                     round(t.diff_relativa_pct, 1) if t.diff_relativa_pct == t.diff_relativa_pct else "n/d",
                     round(t.p_value, 4), "Sim" if t.significativo else "Não"])

    ws.update(range_name="A1", values=rows)
    ws.format("A1:A1", {"textFormat": {"bold": True, "fontSize": 13}})
    ws.format("A3:A4", {"textFormat": {"bold": True}})
    metrics_header_row = 6
    ws.format(f"A{metrics_header_row}:J{metrics_header_row}",
              {"backgroundColor": HEADER_BG, "textFormat": {"bold": True, "foregroundColor": HEADER_FG}})
    tests_header_row = 7 + len(metrics)
    ws.format(f"A{tests_header_row}:G{tests_header_row}",
              {"backgroundColor": HEADER_BG, "textFormat": {"bold": True, "foregroundColor": HEADER_FG}})
    try:
        ws.columns_auto_resize(0, 9)
    except Exception:
        pass


def _ensure_sheet_header(ws) -> None:
    """Garante que a linha 1 da aba resumo tenha o cabeçalho correto —
    mesmo se a planilha já tinha linhas de execuções anteriores sem
    cabeçalho (versão antiga do script)."""
    try:
        primeira_linha = ws.row_values(1)
    except Exception:
        primeira_linha = []
    if primeira_linha != COLUNAS:
        ws.insert_row(COLUNAS, index=1)
        _format_summary_header(ws)


def _append_google_sheets(row, sheet_id: str, nome_teste: str, descricao: str,
                           metrics: list[GroupMetrics], tests: list[PairwiseTest], decisao: Decisao) -> bool:
    try:
        sh = _get_sheets_client(sheet_id)
        if sh is None:
            return False
        ws = sh.sheet1
        _ensure_sheet_header(ws)
        ws.append_row(row)
        new_row_number = len(ws.get_all_values())
        _color_confidence_cell(ws, new_row_number, decisao.confianca)

        _write_detail_tab(sh, nome_teste, metrics, tests, decisao, descricao)
        return True
    except Exception as e:
        print(f"[aviso] Google Sheets falhou, usando CSV local: {e}")
        return False


def registrar_teste(*, nome_teste, descricao, metrics, decisao, csv_path, report_path,
                     tests: list[PairwiseTest] = None,
                     csv_out="resultados_testes.csv", google_sheet_id=None) -> str:
    row = _build_row(nome_teste, descricao, metrics, decisao, csv_path, report_path)
    if google_sheet_id and _append_google_sheets(row, google_sheet_id, nome_teste, descricao,
                                                    metrics, tests or [], decisao):
        return (f"Registrado no Google Sheets (aba resumo + aba detalhada '{_sanitize_sheet_name(nome_teste)}'): "
                f"https://docs.google.com/spreadsheets/d/{google_sheet_id}")
    path = _append_csv(row, csv_out)
    return f"Registrado no CSV local: {path}"


# --------------------------------------------------------------------------- #
# 7. SELEÇÃO DE ARQUIVO(S) — CLI ou menu interativo
# --------------------------------------------------------------------------- #

def _escolher_arquivos_interativamente() -> list[Path]:
    candidatos = sorted(Path(".").glob("*.csv"))
    if not candidatos:
        print("Nenhum arquivo .csv encontrado nesta pasta.")
        sys.exit(1)

    print("\nArquivos CSV encontrados nesta pasta:")
    for i, f in enumerate(candidatos, 1):
        print(f"  [{i}] {f.name}")
    print("\nDigite os números separados por vírgula (ex: 1,3), 'todos' para processar todos, "
          "ou ENTER para cancelar:")
    escolha = input("> ").strip().lower()

    if not escolha:
        print("Nenhum arquivo selecionado. Encerrando.")
        sys.exit(0)
    if escolha in ("todos", "all", "*"):
        return candidatos

    indices = []
    for parte in escolha.split(","):
        parte = parte.strip()
        if parte.isdigit() and 1 <= int(parte) <= len(candidatos):
            indices.append(int(parte))
    if not indices:
        print("Nenhum número válido reconhecido. Encerrando.")
        sys.exit(1)
    return [candidatos[i - 1] for i in indices]


def _perguntar_nome_descricao(arquivo: Path) -> tuple[str, str]:
    sugestao_nome = arquivo.stem.replace("_", " ").replace("-", " ").title()
    nome = input(f"Nome do teste para '{arquivo.name}' [{sugestao_nome}]: ").strip() or sugestao_nome
    descricao = input(f"Descrição do teste para '{arquivo.name}': ").strip() or f"Teste A/B a partir de {arquivo.name}"
    return nome, descricao


# --------------------------------------------------------------------------- #
# 8. CLI
# --------------------------------------------------------------------------- #

def processar_arquivo(arquivo: Path, nome: str, descricao: str, out_dir: str, sheet_id: str | None, alpha: float):
    print(f"\n========== {arquivo.name} ==========")
    print(f"→ Carregando e limpando {arquivo} ...")
    df, cleaning = load_and_clean(arquivo)
    print(cleaning.resumo())

    print("→ Calculando métricas por variante ...")
    metrics = compute_group_metrics(df)
    for m in metrics:
        print(f"   {m.grupo}: lucro líq./dia = R$ {m.lucro_liquido_dia_medio:,.2f} | "
              f"ticket médio = R$ {m.ticket_medio:,.2f} | dias = {m.dias}")

    print("→ Testes de significância ...")
    tests = run_pairwise_tests(df, alpha=alpha)

    print("→ Decidindo ...")
    decisao = decide_winner(metrics, tests)
    print(f"   Recomendação: {decisao.variante_recomendada} (confiança: {decisao.confianca})")

    print("→ Gerando relatório ...")
    report_path = generate_report(nome_teste=nome, descricao=descricao, csv_path=arquivo,
                                   cleaning=cleaning, metrics=metrics, tests=tests, decisao=decisao,
                                   out_dir=out_dir)
    print(f"   Relatório: {report_path}")

    print("→ Registrando na planilha de acompanhamento ...")
    status = registrar_teste(nome_teste=nome, descricao=descricao, metrics=metrics, decisao=decisao,
                              csv_path=arquivo, report_path=str(report_path), tests=tests,
                              csv_out=Path(out_dir) / "resultados_testes.csv", google_sheet_id=sheet_id)
    print(f"   {status}")


def main():
    parser = argparse.ArgumentParser(
        description="Analisa um ou mais testes A/B de cashback e recomenda a variante a escalar. "
                     "Rode sem --file para escolher interativamente os CSVs da pasta atual.")
    parser.add_argument("--file", nargs="+", default=None,
                         help="Um ou mais caminhos de CSV. Se omitido, mostra um menu interativo.")
    parser.add_argument("--nome", nargs="+", default=None,
                         help="Nome de cada teste, na mesma ordem de --file. Se omitido, pergunta interativamente.")
    parser.add_argument("--descricao", nargs="+", default=None,
                         help="Descrição de cada teste, na mesma ordem de --file. Se omitido, pergunta interativamente.")
    parser.add_argument("--out-dir", default="reports")
    parser.add_argument("--sheet-id", default=None)
    parser.add_argument("--alpha", type=float, default=0.05)
    args = parser.parse_args()

    if args.file:
        arquivos = [Path(f) for f in args.file]
    else:
        arquivos = _escolher_arquivos_interativamente()

    usa_nomes_cli = args.nome and len(args.nome) == len(arquivos)
    usa_descs_cli = args.descricao and len(args.descricao) == len(arquivos)

    tarefas = []
    for i, arquivo in enumerate(arquivos):
        if usa_nomes_cli and usa_descs_cli:
            nome, descricao = args.nome[i], args.descricao[i]
        else:
            nome, descricao = _perguntar_nome_descricao(arquivo)
        tarefas.append((arquivo, nome, descricao))

    print(f"\n{len(tarefas)} teste(s) selecionado(s) para processar.")
    for arquivo, nome, descricao in tarefas:
        try:
            processar_arquivo(arquivo, nome, descricao, args.out_dir, args.sheet_id, args.alpha)
        except Exception as e:
            print(f"❌ Erro ao processar {arquivo}: {e}")

    print("\n✅ Concluído.")


if __name__ == "__main__":
    main()
