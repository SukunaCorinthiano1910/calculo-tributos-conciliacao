"""Aba "Cálculo de Tributos": leitura de notas fiscais em lote (PDF e XML), quem recolhe cada tributo, códigos de
recolhimento, valores bruto e líquido, enquadramento do emitente, erros de emissão e planilha dinâmica."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

import modulo_tributos as mt
from formatacao import brl

CAMPOS_DINAMICA = {"Tributo": "tributo", "Esfera": "esfera", "Quem recolhe": "responsavel", "Guia": "guia",
                   "Código de recolhimento": "codigo_recolhimento", "Emitente": "emitente_nome", "Tipo de nota": "tipo",
                   "Situação": "situacao", "Considerar na apuração": "considerar", "Duplicada": "duplicada"}
FUNCOES = {"Soma": "sum", "Média": "mean", "Quantidade": "count", "Máximo": "max"}
LIMITE_CONSULTA = 60   # CNPJs por consulta à BrasilAPI (serviço público com limite de uso)


def _processar(arquivos: list):
    barra = st.progress(0.0, text="Lendo notas...")
    notas, problemas = mt.ler_lote(arquivos, progresso=lambda i, total: barra.progress(
        i / total, text=f"Lendo notas... {i} de {total} arquivos"))
    barra.empty()
    completas, tributos, erros = mt.conciliar(notas)
    st.session_state["trib_resultado"] = {"notas": completas, "tributos": tributos, "erros": erros,
                                          "problemas": problemas, "cadastro": pd.DataFrame()}


def _entrada():
    c1, c2 = st.columns([3, 2])
    with c1:
        enviados = st.file_uploader("Notas fiscais (PDF do DANFSe/DANFE, XML ou .zip com vários arquivos)",
                                    type=["pdf", "xml", "zip"], accept_multiple_files=True, key="trib_upload")
        if st.button("⚙️ Processar notas enviadas", type="primary", disabled=not enviados, key="trib_processar"):
            _processar([(a.name, a.getvalue()) for a in enviados])
    with c2:
        pasta = st.text_input("…ou uma pasta deste computador (para lotes grandes)", key="trib_pasta",
                              placeholder="/home/usuario/Documentos/Notas 2026-11")
        b1, b2 = st.columns(2)
        if b1.button("📂 Ler pasta", disabled=not pasta, key="trib_ler_pasta"):
            raiz = Path(pasta).expanduser()
            if not raiz.is_dir():
                st.error("Pasta não encontrada.")
            else:
                arquivos = [(p.name, p.read_bytes()) for p in sorted(raiz.rglob("*"))
                            if p.suffix.lower() in (".pdf", ".xml", ".zip")]
                if arquivos:
                    _processar(arquivos)
                else:
                    st.warning("Nenhum PDF, XML ou ZIP nessa pasta.")
        if b2.button("🧪 Lote de exemplo", key="trib_exemplo",
                     help="12 DANFSe fictícios no layout oficial, alguns com erros de propósito"):
            _processar(mt.notas_de_exemplo(12))


def _metricas(r):
    notas, erros = r["notas"], r["erros"]
    ativas = notas[notas["considerar"] == "Sim"]          # sem duplicadas, canceladas e substituídas
    duplicadas = int((notas["duplicada"] == "Sim").sum())
    c = st.columns(5)
    avisos = [f"{len(r['problemas'])} arquivo(s) com problema"] if r["problemas"] else []
    avisos += [f"{duplicadas} duplicada(s) fora dos totais"] if duplicadas else []
    c[0].metric("Notas lidas", f"{len(notas)}", " · ".join(avisos) or None, delta_color="inverse")
    c[1].metric("Valor bruto", brl(ativas["valor_bruto"].sum()))
    c[2].metric("Valor líquido", brl(ativas["valor_liquido"].sum()))
    c[3].metric("A recolher pelo tomador", brl(ativas["recolhe_tomador"].sum()),
                help="Retenções: ISS retido, IRRF, PIS/COFINS/CSLL e INSS")
    c[4].metric("Notas com erro", f"{(notas['qtd_erros'] > 0).sum()}",
                f"{(erros['gravidade'] == 'Alerta').sum() if not erros.empty else 0} alertas", delta_color="off")
    if r["problemas"]:
        with st.expander(f"⚠️ {len(r['problemas'])} arquivo(s) não lidos"):
            for p in r["problemas"]:
                st.write("- " + p)


def _aba_notas(notas: pd.DataFrame):
    c1, c2, c3, c4 = st.columns(4)
    tipos = c1.multiselect("Tipo", sorted(notas["tipo"].dropna().unique()), key="trib_f_tipo")
    enqs = c2.multiselect("Enquadramento", sorted(notas["enquadramento"].dropna().unique()), key="trib_f_enq")
    emits = c3.multiselect("Emitente", sorted(notas["emitente_nome"].dropna().unique()), key="trib_f_emit")
    so_erro = c4.toggle("Só notas com erro", key="trib_f_erro")
    f = notas
    if tipos:
        f = f[f["tipo"].isin(tipos)]
    if enqs:
        f = f[f["enquadramento"].isin(enqs)]
    if emits:
        f = f[f["emitente_nome"].isin(emits)]
    if so_erro:
        f = f[f["qtd_erros"] > 0]
    colunas = [c for c in mt.ORDEM_NOTAS if c in f.columns]
    moeda = {mt.ROTULOS[c]: st.column_config.NumberColumn(mt.ROTULOS[c], format="R$ %.2f")
             for c in colunas if c in mt.MOEDA}
    st.dataframe(f[colunas].rename(columns=mt.ROTULOS), hide_index=True, use_container_width=True, height=420,
                 column_config=moeda)
    st.caption(f"{len(f)} de {len(notas)} notas.")


def _aba_dinamica(tributos: pd.DataFrame, notas: pd.DataFrame):
    if tributos.empty:
        st.info("Nenhum tributo encontrado nas notas.")
        return
    t = tributos
    incluir = st.toggle("Incluir notas duplicadas, canceladas e substituídas", key="trib_d_cancel",
                        help="Desligado, a tabela usa só as notas com 'Considerar na apuração' = Sim")
    if not incluir:
        t = t[t["considerar"] == "Sim"]
    c1, c2, c3, c4 = st.columns([3, 2, 1, 1])
    linhas = c1.multiselect("Linhas", list(CAMPOS_DINAMICA), default=["Tributo", "Quem recolhe"], key="trib_d_lin")
    colunas = c2.selectbox("Colunas", ["(nenhuma)"] + list(CAMPOS_DINAMICA), index=0, key="trib_d_col")
    valor = c3.selectbox("Valor", ["Valor", "Base"], key="trib_d_val")
    funcao = c4.selectbox("Função", list(FUNCOES), key="trib_d_fun")
    if not linhas:
        st.info("Escolha ao menos um campo em Linhas.")
        return
    piv = mt.resumo_dinamico(t, [CAMPOS_DINAMICA[l] for l in linhas],
                             None if colunas == "(nenhuma)" else [CAMPOS_DINAMICA[colunas]],
                             {"Valor": "valor", "Base": "base"}[valor], FUNCOES[funcao], so_consideradas=False)
    piv.index.names = linhas
    formato = "%d" if funcao == "Quantidade" else "R$ %.2f"
    st.dataframe(piv, use_container_width=True,
                 column_config={str(c): st.column_config.NumberColumn(format=formato) for c in piv.columns})
    grafico = t.groupby(CAMPOS_DINAMICA[linhas[0]], as_index=False)["valor"].sum().sort_values("valor")
    fig = px.bar(grafico, x="valor", y=CAMPOS_DINAMICA[linhas[0]], orientation="h", text_auto=".2s",
                 labels={"valor": "Valor (R$)", CAMPOS_DINAMICA[linhas[0]]: linhas[0]},
                 color_discrete_sequence=["#6A00F4"])
    fig.update_layout(height=max(260, 34 * len(grafico)), margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Códigos de DARF pela natureza mais comum do serviço: confira o enquadramento de cada pagamento. "
               "IBS/CBS de 2026 aparecem como destaque informativo (fase de teste da LC 214/2025).")


def _aba_erros(erros: pd.DataFrame):
    if erros.empty:
        st.success("Nenhum erro de emissão encontrado.")
        return
    contagem = erros["gravidade"].value_counts()
    c = st.columns(3)
    for col, g in zip(c, ["Erro", "Alerta", "Informação"]):
        col.metric(g + ("s" if g != "Informação" else "ões"), int(contagem.get(g, 0)))
    grav = st.multiselect("Gravidade", ["Erro", "Alerta", "Informação"], default=["Erro", "Alerta"], key="trib_e_g")
    f = erros[erros["gravidade"].isin(grav)]
    st.dataframe(f.rename(columns=mt.ROTULOS), hide_index=True, use_container_width=True, height=380)


def _aba_receita(r):
    st.caption("Confere o enquadramento declarado na nota com o cadastro público do CNPJ na Receita Federal, "
               f"pela BrasilAPI (até {LIMITE_CONSULTA} CNPJs por vez). Envia só o CNPJ dos emitentes.")
    cnpjs = [c for c in r["notas"]["emitente_cnpj"].dropna().unique() if len(mt.so_digitos(c)) == 14]
    if st.button(f"🔎 Consultar {min(len(cnpjs), LIMITE_CONSULTA)} CNPJ(s)", disabled=not cnpjs, key="trib_receita"):
        barra, linhas, falhas = st.progress(0.0), [], []
        for i, c in enumerate(cnpjs[:LIMITE_CONSULTA], start=1):
            try:
                linhas.append(mt.consultar_cnpj(c))
            except Exception as e:
                falhas.append(f"{c}: {e}")
            barra.progress(i / min(len(cnpjs), LIMITE_CONSULTA))
        barra.empty()
        r["cadastro"] = pd.DataFrame(linhas)
        if falhas:
            st.warning("Não consegui consultar: " + "; ".join(falhas[:5]))
    if not r["cadastro"].empty:
        comp = mt.comparar_enquadramento(r["notas"], r["cadastro"])
        divergentes = comp[comp["diverge"]] if not comp.empty else comp
        if not divergentes.empty:
            st.error(f"{len(divergentes)} nota(s) com enquadramento diferente do cadastro na Receita.")
        st.dataframe(comp.rename(columns={**mt.ROTULOS, "opcao_receita": "Opção na Receita", "porte": "Porte",
                                          "situacao_cadastral": "Situação cadastral", "diverge": "Diverge"}),
                     hide_index=True, use_container_width=True)


def mostrar():
    st.subheader("🧾 Cálculo de Tributos e Conciliação Tributária")
    st.caption("Lê notas fiscais em lote (DANFSe da NFS-e nacional, DANFE e XMLs), identifica o código de serviço "
               "ou o CFOP, o enquadramento do emitente, quem recolhe cada tributo e com qual código, os valores bruto "
               "e líquido, e aponta erros de emissão. O XML é mais confiável que o PDF: use-o quando tiver.")
    _entrada()
    r = st.session_state.get("trib_resultado")
    if not r:
        st.info("Envie as notas, informe uma pasta ou gere o lote de exemplo para começar.")
        return
    if r["notas"].empty:
        st.warning("Nenhuma nota reconhecida nos arquivos enviados.")
        for p in r["problemas"]:
            st.write("- " + p)
        return
    _metricas(r)
    abas = st.tabs(["📋 Notas", "🧮 Tabela dinâmica", "⚠️ Erros de emissão", "🏛️ Enquadramento na Receita",
                    "⬇️ Planilha"])
    with abas[0]:
        _aba_notas(r["notas"])
    with abas[1]:
        _aba_dinamica(r["tributos"], r["notas"])
    with abas[2]:
        _aba_erros(r["erros"])
    with abas[3]:
        _aba_receita(r)
    with abas[4]:
        st.write("Planilha Excel com as abas **Resumo** (totais dinâmicos), **Notas**, **Tributos** (base pronta para "
                 "Inserir → Tabela Dinâmica), **Erros** e, se consultado, **Enquadramento Receita**. Todas com filtros.")
        st.download_button("⬇️ Baixar planilha de conciliação (.xlsx)",
                           mt.planilha(r["notas"], r["tributos"], r["erros"], r["cadastro"]),
                           file_name=f"conciliacao_tributaria_{pd.Timestamp.now():%Y-%m-%d}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary")
