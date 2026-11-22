"""Cálculo de tributos e conciliação tributária de notas fiscais (NFS-e e NF-e).

Lê PDFs (DANFSe do padrão nacional e DANFE) e XMLs em lote, identifica a operação (código de serviço da LC 116
ou CFOP), o enquadramento do emitente, quem recolhe cada tributo, o código de recolhimento, os valores bruto e
líquido, e aponta erros de emissão. Sem Streamlit: usado pela aba "Cálculo de Tributos" e pelos testes.

Layout do DANFSe: rótulos e códigos copiados do gerador oficial da Receita Federal
(github.com/ReceitaFederal/calculadora-rtc-gerador-danfse, internal/renderer/labels.go e
internal/domain/presentation/danfse_format.go). Cada campo é uma célula com o rótulo em cima e o valor embaixo,
numa grade de 4 colunas; o extrator procura o rótulo e lê o texto logo abaixo dele.
"""
from __future__ import annotations

import io
import os
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime
from typing import Iterable, Optional

import numpy as np
import pandas as pd

# ──────────────────────────────────────────────
# Tabelas de referência
# ──────────────────────────────────────────────

# LC 116/2003: itens da lista de serviços (os 2 primeiros dígitos do código de tributação nacional XX.XX.XX)
ITENS_LC116 = {
    1: "Informática", 2: "Pesquisa e desenvolvimento", 3: "Locação, cessão de direito de uso e congêneres",
    4: "Saúde e assistência médica", 5: "Medicina e assistência veterinária", 6: "Cuidados pessoais, estética e "
    "atividades físicas", 7: "Engenharia, arquitetura, construção civil, manutenção, limpeza e saneamento",
    8: "Educação, ensino e treinamento", 9: "Hospedagem, turismo e viagens", 10: "Intermediação e congêneres",
    11: "Guarda, estacionamento, armazenamento e vigilância", 12: "Diversões, lazer e entretenimento",
    13: "Fonografia, fotografia, cinematografia e reprografia", 14: "Serviços relativos a bens de terceiros",
    15: "Setor bancário ou financeiro", 16: "Transporte de natureza municipal", 17: "Apoio técnico, administrativo, "
    "jurídico, contábil e comercial", 18: "Regulação de sinistros e riscos de seguros", 19: "Loterias, bingos e "
    "apostas", 20: "Portuários, aeroportuários e terminais", 21: "Registros públicos, cartorários e notariais",
    22: "Exploração de rodovia", 23: "Programação e comunicação visual, desenho industrial", 24: "Chaveiros, "
    "carimbos, placas e sinalização visual", 25: "Funerários", 26: "Coleta e entrega de correspondências e "
    "objetos (courrier)", 27: "Assistência social", 28: "Avaliação de bens e serviços", 29: "Biblioteconomia",
    30: "Biologia, biotecnologia e química", 31: "Técnicos em edificações, eletrônica, mecânica e "
    "telecomunicações", 32: "Desenhos técnicos", 33: "Desembaraço aduaneiro, comissários e despachantes",
    34: "Investigações particulares", 35: "Reportagem, assessoria de imprensa e relações públicas",
    36: "Meteorologia", 37: "Artistas, atletas, modelos e manequins", 38: "Museologia", 39: "Ourivesaria e "
    "lapidação", 40: "Obras de arte sob encomenda",
}

# Gerador oficial do DANFSe (danfse_format.go): código → texto impresso
RETENCAO_ISSQN = {1: "Não Retido", 2: "Retido pelo Tomador", 3: "Retido pelo Intermediário"}
OPCAO_SIMPLES = {1: "1 - Não Optante", 2: "2 - Optante - MEI", 3: "3 - Optante - ME/EPP"}
REGIME_APURACAO_SN = {1: "1 - Regime de apuração dos tributos federais e municipal pelo SN",
                      2: "2 - Regime de apuração dos tributos federais pelo SN e ISSQN por fora do SN"}
REGIME_ESPECIAL = {0: "Nenhum", 1: "Ato Cooperado (Cooperativa)", 2: "Estimativa", 3: "Microempresa Municipal",
                   4: "Notário ou Registrador", 5: "Profissional Autônomo", 6: "Sociedade de Profissionais",
                   9: "Outros"}
TRIBUTACAO_ISSQN = {1: "Operação Tributável", 2: "Imunidade", 3: "Exportação de Serviço", 4: "Não Incidência"}
RETENCAO_PIS_COFINS = {0: "PIS/COFINS/CSLL Não Retidos", 1: "PIS/COFINS Retidos", 2: "PIS/COFINS Não Retidos",
                       3: "PIS/COFINS/CSLL Retidos", 4: "PIS/COFINS Retidos, CSLL Não Retido",
                       5: "PIS Retido, COFINS/CSLL Não Retido", 6: "COFINS Retido, PIS/CSLL Não Retido",
                       7: "PIS Não Retido, COFINS/CSLL Retidos", 8: "PIS/COFINS Não Retidos, CSLL Retido",
                       9: "COFINS Não Retido, PIS/CSLL Retidos"}
SITUACAO_NFSE = {"100": "NFS-e Regular", "101": "NFS-e Cancelada", "102": "NFS-e Substituída",
                 "103": "NFS-e de Decisão Judicial ou Administrativa", "104": "NFS-e Emitida em Contingência",
                 "105": "NFS-e de Ajuste"}
CRT = {1: "Simples Nacional", 2: "Simples Nacional - excesso de sublimite", 3: "Regime Normal", 4: "MEI"}

# Códigos de receita do DARF mais comuns em notas de serviço (tabela de códigos de receita da RFB).
# Confira sempre a natureza do serviço: o código depende do enquadramento do pagamento, não só da nota.
DARF = {
    "IRRF_SERVICOS": ("1708", "IRRF - serviços profissionais prestados por PJ (1,5%) e limpeza, conservação, "
                              "segurança e locação de mão de obra (1%)"),
    "IRRF_COMISSOES": ("8045", "IRRF - comissões, corretagens e serviços de propaganda (1,5%)"),
    "CSRF": ("5952", "Retenção de CSLL, COFINS e PIS/Pasep (4,65%)"),
    "CSLL": ("5987", "CSLL retida na fonte (1%)"),
    "COFINS": ("5960", "COFINS retida na fonte (3%)"),
    "PIS": ("5979", "PIS/Pasep retido na fonte (0,65%)"),
    "IPI": ("5123", "IPI - demais produtos"),
    "PIS_CUMULATIVO": ("8109", "PIS - regime cumulativo (lucro presumido)"),
    "COFINS_CUMULATIVO": ("2172", "COFINS - regime cumulativo (lucro presumido)"),
    "PIS_NAO_CUMULATIVO": ("6912", "PIS - regime não cumulativo (lucro real)"),
    "COFINS_NAO_CUMULATIVO": ("5856", "COFINS - regime não cumulativo (lucro real)"),
}
ITENS_COMISSAO = {10}                     # intermediação, corretagem, agenciamento → IRRF 8045
SUBITENS_COMISSAO = {"17.06"}             # propaganda e publicidade → IRRF 8045
ALIQ_ISS_MIN, ALIQ_ISS_MAX = 2.0, 5.0     # LC 116/2003, art. 8º, II e art. 8º-A
LIMITE_RETENCAO = 10.0                    # IRRF e CSRF iguais ou menores que R$ 10,00 não são retidos
TOLERANCIA = 0.05                         # diferença de arredondamento aceita nas somas (R$)

CFOP_OPERACAO = {
    "101": "Venda de produção do estabelecimento", "102": "Venda de mercadoria adquirida de terceiros",
    "103": "Venda de produção, efetuada fora do estabelecimento", "104": "Venda de mercadoria adquirida, "
    "efetuada fora do estabelecimento", "405": "Venda de mercadoria com ICMS-ST (substituído)",
    "401": "Venda de produção com ICMS-ST (substituto)", "403": "Venda de mercadoria com ICMS-ST (substituto)",
    "202": "Devolução de compra para industrialização", "411": "Devolução de compra com ST",
    "910": "Remessa em bonificação, doação ou brinde", "911": "Remessa de amostra grátis",
    "915": "Remessa para conserto ou reparo", "916": "Retorno de conserto ou reparo",
    "933": "Prestação de serviço tributado pelo ISSQN", "949": "Outra saída não especificada",
}

COLUNAS_NOTA = [
    "arquivo", "pagina", "tipo", "situacao", "numero", "chave", "emissao", "competencia",
    "emitente_cnpj", "emitente_nome", "emitente_municipio_uf", "simples_nacional", "regime_apuracao_sn",
    "regime_especial", "crt", "destinatario_cnpj", "destinatario_nome", "destinatario_uf",
    "codigo_servico", "codigo_municipal", "nbs", "descricao", "local_prestacao", "municipio_incidencia",
    "tributacao_issqn", "cfop", "natureza_operacao", "cst_csosn",
    "valor_bruto", "desconto_incondicionado", "desconto_condicionado", "deducoes", "bc_iss", "aliquota_iss",
    "retencao_iss", "iss", "irrf", "cp_retida", "contrib_sociais_retidas", "desc_contrib_sociais",
    "pis_proprio", "cofins_proprio", "total_retencoes", "valor_liquido",
    "bc_icms", "icms", "icms_st", "ipi", "pis", "cofins", "frete", "seguro", "outras_despesas",
    "cst_ibscbs", "cclasstrib", "ibs", "cbs",
]
NUMERICAS = ["valor_bruto", "desconto_incondicionado", "desconto_condicionado", "deducoes", "bc_iss",
             "aliquota_iss", "iss", "irrf", "cp_retida", "contrib_sociais_retidas", "pis_proprio", "cofins_proprio",
             "total_retencoes", "valor_liquido", "bc_icms", "icms", "icms_st", "ipi", "pis", "cofins", "frete",
             "seguro", "outras_despesas", "ibs", "cbs"]


# ──────────────────────────────────────────────
# Utilidades
# ──────────────────────────────────────────────

def numero(texto) -> float:
    """'R$ 1.234,56' → 1234.56; '2,00%' → 2.0; '-' ou vazio → 0."""
    if texto is None:
        return 0.0
    if isinstance(texto, (int, float, np.number)):
        return float(texto)
    t = re.sub(r"[^\d,.\-]", "", str(texto))
    if t in ("", "-", ".", ","):
        return 0.0
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", t):
        t = t.replace(".", "")
    try:
        return float(t)
    except ValueError:
        return 0.0


def so_digitos(texto) -> str:
    return re.sub(r"\D", "", str(texto or ""))


def cnpj_valido(cnpj) -> bool:
    d = so_digitos(cnpj)
    if len(d) != 14 or d == d[0] * 14:
        return False
    for tamanho in (12, 13):
        pesos = list(range(tamanho - 7, 1, -1)) + list(range(9, 1, -1))
        soma = sum(int(a) * b for a, b in zip(d[:tamanho], pesos))
        dv = 0 if soma % 11 < 2 else 11 - soma % 11
        if dv != int(d[tamanho]):
            return False
    return True


def cpf_valido(cpf) -> bool:
    d = so_digitos(cpf)
    if len(d) != 11 or d == d[0] * 11:
        return False
    for tamanho in (9, 10):
        soma = sum(int(a) * b for a, b in zip(d[:tamanho], range(tamanho + 1, 1, -1)))
        dv = (soma * 10) % 11 % 10
        if dv != int(d[tamanho]):
            return False
    return True


def formatar_cnpj(texto) -> str:
    d = so_digitos(texto)
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    return str(texto or "")


def br(v: float, casas: int = 2) -> str:
    """1234.5 → '1.234,50' (mensagens no padrão brasileiro)."""
    return f"{v:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _sem_acento(t: str) -> str:
    return unicodedata.normalize("NFKD", str(t)).encode("ascii", "ignore").decode().lower()


def _data(texto) -> Optional[str]:
    """'20/11/2026 10:30:00' ou '2026-11-20T10:30:00-03:00' → '2026-11-20'."""
    t = str(texto or "").strip()
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", t)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", t)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def _codigo_por_texto(texto: str, tabela: dict) -> Optional[int]:
    """Texto impresso ('Retido pelo Tomador', '3 - Optante - ME/EPP') → código da tabela. Aceita o texto sem o
    número e cortado com '...', como sai nas células estreitas do DANFSe."""
    alvo = _sem_acento(texto).strip()
    if not alvo or alvo == "-":
        return None
    if tabela is OPCAO_SIMPLES:      # o DANFSe real imprime "Optante - Microempresa ou Empresa de Pequeno Porte"
        if "nao optante" in alvo:
            return 1
        if re.search(r"\bmei\b|microempreendedor", alvo):
            return 2
        if re.search(r"me/epp|microempresa|pequeno porte|empresa de", alvo):
            return 3
    for codigo, rotulo in tabela.items():
        if _sem_acento(rotulo) == alvo:
            return codigo
    m = re.match(r"(\d+)\s*-", alvo)
    if m and int(m.group(1)) in tabela:
        return int(m.group(1))
    sem_reticencias = alvo.rstrip(". ")
    for codigo, rotulo in tabela.items():
        r = re.sub(r"^\d+\s*-\s*", "", _sem_acento(rotulo))
        if r in alvo or (len(sem_reticencias) >= 12 and r.startswith(sem_reticencias)):
            return codigo
    return None


def nota_vazia(**campos) -> dict:
    n = {c: None for c in COLUNAS_NOTA}
    n.update({c: 0.0 for c in NUMERICAS})
    n.update(campos)
    return n


# ──────────────────────────────────────────────
# Leitura do DANFSe (PDF da NFS-e nacional)
# ──────────────────────────────────────────────

LARGURA_COLUNA = 5.105 / 2.54 * 72        # 4 colunas de ~5,1 cm (layout.go)
SECOES_DANFSE = ["PRESTADOR / FORNECEDOR", "TOMADOR / ADQUIRENTE", "DESTINATÁRIO DA OPERAÇÃO",
                 "INTERMEDIÁRIO DA OPERAÇÃO", "SERVIÇO PRESTADO", "TRIBUTAÇÃO MUNICIPAL (ISSQN)",
                 "TRIBUTAÇÃO FEDERAL (EXCETO CBS)", "TRIBUTAÇÃO IBS / CBS", "VALOR TOTAL DA NFS-E",
                 "INFORMAÇÕES COMPLEMENTARES"]


class _PaginaDANFSe:
    """Leitura posicional do DANFSe. No PDF real, o título de cada bloco fica centralizado na célula sombreada,
    um pouco abaixo dos rótulos da mesma linha; por isso os limites dos blocos têm uma folga para cima."""
    FOLGA_TITULO = 12      # pt

    def __init__(self, pagina):
        self.p = pagina
        self.palavras = pagina.extract_words(keep_blank_chars=False)
        topos = {}
        for titulo in SECOES_DANFSE:
            achados = pagina.search(re.escape(titulo), regex=True, case=False)
            if achados:
                topos[titulo] = achados[0]["top"] - self.FOLGA_TITULO
        ordem = sorted(topos.items(), key=lambda x: x[1])
        self.limites = {t: (topo, ordem[i + 1][1] if i + 1 < len(ordem) else pagina.height)
                        for i, (t, topo) in enumerate(ordem)}

    def _fim_da_celula(self, m) -> float:
        """A célula termina onde começa o próximo rótulo da mesma linha (ou na margem direita)."""
        seguintes = [w["x0"] for w in self.palavras if abs(w["top"] - m["top"]) < 2 and w["x0"] > m["x1"] + 2]
        return (min(seguintes) - 1) if seguintes else self.p.width - 8

    def campo(self, rotulo: str, secao: Optional[str] = None, regex: bool = False, **_) -> str:
        """Texto da célula: o que está logo abaixo do rótulo, até o início da próxima célula."""
        if secao and secao not in self.limites:
            return ""
        ini, fim = self.limites[secao] if secao else (0, self.p.height)
        for m in self.p.search(rotulo if regex else re.escape(rotulo), regex=True, case=False):
            if not (ini <= m["top"] < fim):
                continue
            caixa = (max(m["x0"] - 1, 0), m["bottom"] + 0.3, self._fim_da_celula(m), min(m["bottom"] + 11, self.p.height))
            texto = " ".join((self.p.within_bbox(caixa).extract_text() or "").split())
            return "" if texto == "-" else texto
        return ""

    def texto_abaixo(self, rotulo: str, secao: str, linhas: float = 40) -> str:
        """Texto livre em várias linhas (descrição do serviço, informações complementares)."""
        ini, fim = self.limites.get(secao, (0, self.p.height))
        for m in self.p.search(re.escape(rotulo), regex=True, case=False):
            if ini <= m["top"] < fim:
                caixa = (0, m["bottom"] + 0.3, self.p.width, min(m["bottom"] + linhas, fim))
                return " ".join((self.p.within_bbox(caixa).extract_text() or "").split())
        return ""


def ler_danfse(pagina, arquivo: str = "", numero_pagina: int = 1) -> dict:
    d = _PaginaDANFSe(pagina)
    c = d.campo
    P, T, S = "PRESTADOR / FORNECEDOR", "TOMADOR / ADQUIRENTE", "SERVIÇO PRESTADO"
    M, F, I, V = ("TRIBUTAÇÃO MUNICIPAL (ISSQN)", "TRIBUTAÇÃO FEDERAL (EXCETO CBS)", "TRIBUTAÇÃO IBS / CBS",
                  "VALOR TOTAL DA NFS-E")
    codigos = c("Código de Tributação Nacional / Municipal", S)
    cod_nac = re.search(r"\d{2}\.\d{2}\.\d{2}", codigos)
    cod_mun = codigos.split("/", 1)[1].strip() if "/" in codigos else ""
    cod_mun = "" if cod_mun == "-" else cod_mun
    cst = c("CST / cClassTrib", I)
    partes_cst = [x.strip() for x in cst.split("/")] if cst and cst != "-" else []
    simples = c("Simples Nacional na Data de Competência", P, colunas=2)
    texto = pagina.extract_text() or ""
    situacao = "NFS-e Cancelada" if "CANCELADA" in texto else "NFS-e Substituída" if "SUBSTITUÍDA" in texto else \
        c("SITUAÇÃO DA NFS-e") or "NFS-e Regular"
    return nota_vazia(
        arquivo=arquivo, pagina=numero_pagina, tipo="NFS-e", situacao=situacao,
        numero=c("NÚMERO DA NFS-E"), chave=so_digitos(c("CHAVE DE ACESSO DA NFS-E", colunas=3)) or None,
        emissao=_data(c("DATA E HORA DA EMISSÃO DA NFS-E")), competencia=_data(c("COMPETÊNCIA DA NFS-E")),
        emitente_cnpj=c("CNPJ / CPF / NIF", P), emitente_nome=c("Nome / Nome Empresarial", P, colunas=2),
        emitente_municipio_uf=c("Município / Sigla UF", P), simples_nacional=simples,
        regime_apuracao_sn=c("Regime de Apuração Tributária pelo SN", P, colunas=2),
        regime_especial=c("Regime Especial de Tributação do ISSQN", M),
        destinatario_cnpj=c("CNPJ / CPF / NIF", T), destinatario_nome=c("Nome / Nome Empresarial", T, colunas=2),
        destinatario_uf=(c("Município / Sigla UF", T).split("/")[-1].strip() or None),
        codigo_servico=cod_nac.group(0) if cod_nac else (codigos or None), codigo_municipal=cod_mun or None,
        nbs=c("Código da NBS", S), descricao=d.texto_abaixo("Descrição do Serviço", S),
        local_prestacao=c("Local da Prestação / Sigla UF / País", S),
        municipio_incidencia=c("Município / Sigla UF / País de Incidência do ISSQN", M, colunas=2),
        tributacao_issqn=c("Tipo de Tributação do ISSQN", M),
        valor_bruto=numero(c("VALOR DA OPERAÇÃO / SERVIÇO", V)),
        desconto_incondicionado=numero(c("Desconto Incondicionado", V)),
        desconto_condicionado=numero(c("Desconto Condicionado", V)),
        deducoes=numero(c("Total Deduções/Reduções", M)), bc_iss=numero(c("BC ISSQN", M)),
        aliquota_iss=numero(c("Alíquota Aplicada", M)), retencao_iss=c("Retenção do ISSQN", M),
        iss=numero(c("ISSQN Apurado", M)), irrf=numero(c("IRRF", F)),
        cp_retida=numero(c("Contribuição Previdenciária - Retida", F)),
        contrib_sociais_retidas=numero(c("Contribuições Sociais - Retidas", F)),
        desc_contrib_sociais=c("Descrição Contrib. Sociais - Retidas", F, colunas=2),
        pis_proprio=numero(c("PIS - Débito Apuração Própria", F)),
        cofins_proprio=numero(c("COFINS - Débito Apuração Própria", F)),
        total_retencoes=numero(c("Total das Retenções (ISSQN / Federais)", V)),
        valor_liquido=numero(c(r"VALOR LÍQUIDO DA NFS-e(?! \+)", V, regex=True)),
        cst_ibscbs=partes_cst[0] if partes_cst else None, cclasstrib=partes_cst[1] if len(partes_cst) > 1 else None,
        ibs=numero(c("Valor Total Apurado - IBS", I)), cbs=numero(c("Valor Total Apurado - CBS", I)),
    )


# ──────────────────────────────────────────────
# Leitura do DANFE (PDF da NF-e de venda)
# ──────────────────────────────────────────────

def _depois_do_rotulo(texto: str, rotulos: Iterable[str], padrao: str = r"[\d.]+,\d{2}") -> Optional[str]:
    """No texto corrido do DANFE, o valor costuma vir na linha seguinte ao rótulo, na mesma ordem das colunas."""
    linhas = [l.strip() for l in texto.splitlines()]
    for rotulo in rotulos:
        alvo = _sem_acento(rotulo)
        for i, linha in enumerate(linhas):
            norm = _sem_acento(linha)
            if alvo in norm:
                # posição do rótulo entre os rótulos da linha → mesmo índice entre os valores da linha seguinte
                antes = norm[:norm.index(alvo)]
                indice = len(re.findall(r"(valor|base|v\.|vl\.|desconto|frete|seguro|outras)", antes))
                for prox in linhas[i + 1:i + 3]:
                    valores = re.findall(padrao, prox)
                    if valores:
                        return valores[min(indice, len(valores) - 1)]
    return None


def ler_danfe(texto: str, arquivo: str = "", numero_pagina: int = 1) -> dict:
    """Leitura por texto do DANFE. O layout varia entre emissores: confira os campos vazios no XML da nota."""
    cnpjs = re.findall(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", texto)
    chave = re.search(r"((?:\d{4}\s?){11})", texto.replace(".", ""))
    cfops = re.findall(r"(?<![\d,.])([5-7]\d{3})(?![\d,.])", texto)
    cst = re.findall(r"(?<![\d,.])(\d{3})(?=\s+[5-7]\d{3}\b)", texto)        # CST/CSOSN antes do CFOP na linha do item
    natureza = re.search(r"NATUREZA DA OPERA[ÇC][ÃA]O\s*\n?\s*([^\n]+)", texto, re.I)
    valor = lambda *r: numero(_depois_do_rotulo(texto, r))  # noqa: E731
    emissao = re.search(r"DATA (?:DA|DE) EMISS[ÃA]O[^\n]*\n[^\n]*?(\d{2}/\d{2}/\d{4})", texto, re.I)
    numero_nf = re.search(r"N[º°o]\.?\s*(\d{1,3}(?:\.\d{3})+|\d+)", texto)
    return nota_vazia(
        arquivo=arquivo, pagina=numero_pagina, tipo="NF-e", situacao="Autorizada",
        numero=numero_nf.group(1) if numero_nf else None,
        chave=so_digitos(chave.group(1)) if chave and len(so_digitos(chave.group(1))) == 44 else None,
        emissao=_data(emissao.group(1)) if emissao else None,
        emitente_cnpj=cnpjs[0] if cnpjs else None, destinatario_cnpj=cnpjs[1] if len(cnpjs) > 1 else None,
        natureza_operacao=natureza.group(1).strip() if natureza else None,
        cfop=cfops[0] if cfops else None, cst_csosn=cst[0] if cst else None,
        bc_icms=valor("BASE DE CÁLCULO DO ICMS", "BASE DE CÁLC. DO ICMS", "BASE DE CALC. DO ICMS"),
        icms=valor("VALOR DO ICMS"), icms_st=valor("VALOR DO ICMS SUBST", "VALOR DO ICMS SUBSTITUIÇÃO",
                                                   "V. ICMS SUBST"),
        ipi=valor("VALOR DO IPI", "VALOR TOTAL DO IPI", "V. IPI"),
        valor_bruto=valor("VALOR TOTAL DOS PRODUTOS", "V. TOTAL PRODUTOS", "VALOR TOTAL PRODUTOS"),
        frete=valor("VALOR DO FRETE"), seguro=valor("VALOR DO SEGURO"),
        outras_despesas=valor("OUTRAS DESPESAS"), desconto_incondicionado=valor("DESCONTO"),
        valor_liquido=valor("VALOR TOTAL DA NOTA", "V. TOTAL DA NOTA"),
    )


# ──────────────────────────────────────────────
# Leitura de XML (mais confiável que o PDF)
# ──────────────────────────────────────────────

def _sem_namespace(raiz):
    for el in raiz.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return raiz


def _t(el, caminho: str, padrao=None):
    if el is None:
        return padrao
    achado = el.find(caminho)
    return achado.text.strip() if achado is not None and achado.text else padrao


def ler_xml(conteudo: bytes, arquivo: str = "") -> list:
    raiz = _sem_namespace(ET.fromstring(conteudo))
    notas = []
    for inf in raiz.iter("infNFSe"):
        notas.append(_nfse_xml(inf, arquivo))
    for inf in raiz.iter("infNFe"):
        notas.append(_nfe_xml(inf, raiz, arquivo))
    if not notas:
        raise ValueError("XML sem infNFSe nem infNFe: não é NFS-e nacional nem NF-e.")
    return notas


def _nfse_xml(inf, arquivo):
    dps = inf.find("DPS/infDPS")
    prest, toma, serv = (dps.find(x) if dps is not None else None for x in ("prest", "toma", "serv"))
    vdps = dps.find("valores") if dps is not None else None
    vnf = inf.find("valores")
    trib_mun = vdps.find("trib/tribMun") if vdps is not None else None
    trib_fed = vdps.find("trib/tribFed") if vdps is not None else None
    reg = prest.find("regTrib") if prest is not None else None
    emit = inf.find("emit")
    g = lambda el, c: _t(el, c)  # noqa: E731
    cod = g(serv, "cServ/cTribNac") or ""
    ret_pc = g(trib_fed, "piscofins/tpRetPisCofins")
    ibscbs = inf.find("IBSCBS")
    return nota_vazia(
        arquivo=arquivo, pagina=1, tipo="NFS-e", situacao=SITUACAO_NFSE.get(g(inf, "cStat") or "100", g(inf, "cStat")),
        numero=g(inf, "nNFSe"), chave=(inf.get("Id") or "").replace("NFS", "") or None,
        emissao=_data(g(dps, "dhEmi")), competencia=_data(g(dps, "dCompet")),
        emitente_cnpj=formatar_cnpj(g(emit, "CNPJ") or g(emit, "CPF") or g(prest, "CNPJ") or g(prest, "CPF")),
        emitente_nome=g(emit, "xNome"),
        emitente_municipio_uf=" / ".join(x for x in (g(emit, "enderNac/xMun"), g(emit, "enderNac/UF")) if x) or None,
        simples_nacional=OPCAO_SIMPLES.get(int(g(reg, "opSimpNac") or 0)),
        regime_apuracao_sn=REGIME_APURACAO_SN.get(int(g(reg, "regApTribSN") or 0)),
        regime_especial=REGIME_ESPECIAL.get(int(g(reg, "regEspTrib") or 0)),
        destinatario_cnpj=formatar_cnpj(g(toma, "CNPJ") or g(toma, "CPF")) or None,
        destinatario_nome=g(toma, "xNome"), destinatario_uf=g(toma, "end/endNac/UF"),
        codigo_servico=f"{cod[:2]}.{cod[2:4]}.{cod[4:6]}" if len(cod) == 6 else cod or None,
        codigo_municipal=g(serv, "cServ/cTribMun"), nbs=g(serv, "cServ/cNBS"),
        descricao=g(serv, "cServ/xDescServ"), local_prestacao=g(inf, "xLocPrestacao"),
        municipio_incidencia=" / ".join(x for x in (g(inf, "xLocIncid"), g(inf, "xUFIncid")) if x) or None,
        tributacao_issqn=TRIBUTACAO_ISSQN.get(int(g(trib_mun, "tribISSQN") or 0)),
        valor_bruto=numero(g(vdps, "vServPrest/vServ")),
        desconto_incondicionado=numero(g(vdps, "vDescCondIncond/vDescIncond")),
        desconto_condicionado=numero(g(vdps, "vDescCondIncond/vDescCond")),
        deducoes=numero(g(vnf, "vCalcDR") or g(vdps, "vDedRed/vDR")), bc_iss=numero(g(vnf, "vBC")),
        aliquota_iss=numero(g(vnf, "pAliqAplic")),
        retencao_iss=RETENCAO_ISSQN.get(int(g(trib_mun, "tpRetISSQN") or 0)), iss=numero(g(vnf, "vISSQN")),
        irrf=numero(g(trib_fed, "vRetIRRF")), cp_retida=numero(g(trib_fed, "vRetCP")),
        contrib_sociais_retidas=numero(g(trib_fed, "vRetCSLL")) + (
            numero(g(trib_fed, "piscofins/vPis")) + numero(g(trib_fed, "piscofins/vCofins"))
            if ret_pc in ("1", "3", "4", "5", "6", "7", "9") else 0.0),
        desc_contrib_sociais=RETENCAO_PIS_COFINS.get(int(ret_pc)) if ret_pc else None,
        pis_proprio=numero(g(trib_fed, "piscofins/vPis")) if ret_pc in ("0", "2", "8") else 0.0,
        cofins_proprio=numero(g(trib_fed, "piscofins/vCofins")) if ret_pc in ("0", "2", "8") else 0.0,
        total_retencoes=numero(g(vnf, "vTotalRet")), valor_liquido=numero(g(vnf, "vLiq")),
        cst_ibscbs=_t(dps, "IBSCBS/valores/trib/gIBSCBS/CST") if dps is not None else None,
        cclasstrib=_t(dps, "IBSCBS/valores/trib/gIBSCBS/cClassTrib") if dps is not None else None,
        ibs=numero(_t(ibscbs, "totCIBS/gIBS/vIBSTot")), cbs=numero(_t(ibscbs, "totCIBS/gCBS/vCBS")),
    )


def _nfe_xml(inf, raiz, arquivo):
    ide, emit, dest = inf.find("ide"), inf.find("emit"), inf.find("dest")
    tot = inf.find("total/ICMSTot")
    itens = inf.findall("det")
    cfops = sorted({_t(d, "prod/CFOP") for d in itens if _t(d, "prod/CFOP")})
    csts = set()
    for d in itens:
        for icms in d.findall("imposto/ICMS/*"):
            csts.add(_t(icms, "CSOSN") or ((_t(icms, "orig") or "") + (_t(icms, "CST") or "")))
    prot = raiz.find(".//protNFe/infProt")
    cstat = _t(prot, "cStat")
    g = lambda el, c: _t(el, c)  # noqa: E731
    return nota_vazia(
        arquivo=arquivo, pagina=1, tipo="NF-e",
        situacao={"100": "Autorizada", "101": "Cancelada", "135": "Cancelada", "110": "Denegada"}.get(
            cstat, "Autorizada" if cstat is None else f"Status {cstat}"),
        numero=g(ide, "nNF"), chave=(inf.get("Id") or "").replace("NFe", "") or None,
        emissao=_data(g(ide, "dhEmi") or g(ide, "dEmi")),
        emitente_cnpj=formatar_cnpj(g(emit, "CNPJ") or g(emit, "CPF")), emitente_nome=g(emit, "xNome"),
        emitente_municipio_uf=" / ".join(x for x in (g(emit, "enderEmit/xMun"), g(emit, "enderEmit/UF")) if x),
        crt=CRT.get(int(g(emit, "CRT") or 0)),
        destinatario_cnpj=formatar_cnpj(g(dest, "CNPJ") or g(dest, "CPF")) or None,
        destinatario_nome=g(dest, "xNome"), destinatario_uf=g(dest, "enderDest/UF"),
        cfop=", ".join(cfops) or None, natureza_operacao=g(ide, "natOp"),
        cst_csosn=", ".join(sorted(x for x in csts if x)) or None,
        valor_bruto=numero(g(tot, "vProd")), desconto_incondicionado=numero(g(tot, "vDesc")),
        bc_icms=numero(g(tot, "vBC")), icms=numero(g(tot, "vICMS")), icms_st=numero(g(tot, "vST")),
        ipi=numero(g(tot, "vIPI")), pis=numero(g(tot, "vPIS")), cofins=numero(g(tot, "vCOFINS")),
        frete=numero(g(tot, "vFrete")), seguro=numero(g(tot, "vSeg")), outras_despesas=numero(g(tot, "vOutro")),
        valor_liquido=numero(g(tot, "vNF")),
        ibs=numero(_t(inf, "total/IBSCBSTot/gIBS/vIBS")), cbs=numero(_t(inf, "total/IBSCBSTot/gCBS/vCBS")),
    )


# ──────────────────────────────────────────────
# Leitura em lote
# ──────────────────────────────────────────────

def ler_arquivo(nome: str, conteudo: bytes) -> list:
    """Um arquivo (PDF ou XML) → lista de notas. PDFs com várias notas: uma nota por página."""
    if nome.lower().endswith(".xml"):
        return ler_xml(conteudo, nome)
    import pdfplumber
    notas = []
    with pdfplumber.open(io.BytesIO(conteudo)) as pdf:
        for i, pagina in enumerate(pdf.pages, start=1):
            texto = pagina.extract_text() or ""
            if "DANFSe" in texto or "Documento Auxiliar da NFS-e" in texto:
                notas.append(ler_danfse(pagina, nome, i))
            elif re.search(r"DANFE|DOCUMENTO AUXILIAR DA NOTA FISCAL ELETR", texto, re.I):
                notas.append(ler_danfe(texto, nome, i))
            elif not texto.strip():
                raise ValueError("PDF sem texto (provavelmente digitalizado/imagem): precisa de OCR.")
    if not notas:
        raise ValueError("Não parece um DANFSe nem um DANFE.")
    return notas


def _ler_seguro(item):
    nome, conteudo = item
    try:
        return ler_arquivo(nome, conteudo), None
    except Exception as e:  # um arquivo ruim não pode derrubar o lote inteiro
        return [], f"{nome}: {e}"


def expandir_zip(arquivos: Iterable[tuple]) -> list:
    """(nome, bytes) → mesma lista, com os .zip abertos (só PDFs e XMLs)."""
    saida = []
    for nome, conteudo in arquivos:
        if nome.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(conteudo)) as z:
                for info in z.infolist():
                    if not info.is_dir() and info.filename.lower().endswith((".pdf", ".xml")):
                        saida.append((os.path.basename(info.filename), z.read(info)))
        elif nome.lower().endswith((".pdf", ".xml")):
            saida.append((nome, conteudo))
    return saida


def ler_lote(arquivos: Iterable[tuple], processos: Optional[int] = None, progresso=None) -> tuple:
    """Lê muitos arquivos em paralelo (um processo por núcleo a partir de 20 arquivos).
    Devolve (DataFrame das notas, lista de arquivos com problema)."""
    itens = expandir_zip(arquivos)
    notas, problemas = [], []
    if len(itens) >= 20 and (processos or os.cpu_count() or 1) > 1:
        with ProcessPoolExecutor(max_workers=processos or os.cpu_count()) as ex:
            for i, (lidas, erro) in enumerate(ex.map(_ler_seguro, itens, chunksize=8), start=1):
                notas += lidas
                problemas += [erro] if erro else []
                if progresso:
                    progresso(i, len(itens))
    else:
        for i, item in enumerate(itens, start=1):
            lidas, erro = _ler_seguro(item)
            notas += lidas
            problemas += [erro] if erro else []
            if progresso:
                progresso(i, len(itens))
    df = pd.DataFrame(notas, columns=COLUNAS_NOTA)
    return df, problemas


# ──────────────────────────────────────────────
# Análise tributária e conciliação
# ──────────────────────────────────────────────

def enquadramento(n: dict) -> str:
    if n.get("tipo") == "NF-e":
        if n.get("crt"):
            return n["crt"]
        cst = str(n.get("cst_csosn") or "")
        if re.fullmatch(r"(101|102|103|201|202|203|300|400|500|900)(, .*)?", cst):
            return "Simples Nacional (CSOSN na nota)"
        return "Regime Normal (CST na nota)" if cst else "Não identificado no PDF"
    cod = _codigo_por_texto(n.get("simples_nacional") or "", OPCAO_SIMPLES)
    base = {1: "Não optante (Lucro Presumido ou Real)", 2: "MEI", 3: "Simples Nacional - ME/EPP"}.get(cod,
                                                                                                    "Não identificado")
    esp = _codigo_por_texto(n.get("regime_especial") or "", REGIME_ESPECIAL)
    return base + (f" · {REGIME_ESPECIAL[esp]}" if esp else "")


def _item_lc116(codigo) -> Optional[int]:
    m = re.match(r"(\d{2})\.(\d{2})", str(codigo or ""))
    return int(m.group(1)) if m else None


def operacao(n: dict) -> str:
    if n.get("tipo") == "NFS-e":
        item = _item_lc116(n.get("codigo_servico"))
        return f"Serviço · item {item:02d} LC 116 - {ITENS_LC116[item]}" if item in ITENS_LC116 else \
            "Serviço · código de tributação não identificado"
    cfop = str(n.get("cfop") or "").split(",")[0].strip()
    if len(cfop) != 4:
        return "Mercadoria · CFOP não identificado"
    destino = {"5": "dentro do estado", "6": "para outro estado", "7": "para o exterior",
               "1": "entrada do estado", "2": "entrada de outro estado", "3": "entrada do exterior"}.get(cfop[0], "")
    return f"Mercadoria · CFOP {cfop} - {CFOP_OPERACAO.get(cfop[1:], 'Outra operação')} ({destino})"


def tributos_da_nota(n: dict) -> list:
    """Uma linha por tributo: esfera, quem recolhe, como recolhe (guia e código) e o valor."""
    linhas = []
    enq = enquadramento(n)
    simples_me = enq.startswith("Simples")
    mei = enq.startswith("MEI")

    def add(tributo, esfera, responsavel, guia, codigo, valor, situacao, base=None, aliquota=None):
        linhas.append({"tributo": tributo, "esfera": esfera, "responsavel": responsavel, "guia": guia,
                       "codigo_recolhimento": codigo, "base": base, "aliquota": aliquota, "valor": round(valor, 2),
                       "situacao": situacao})

    if n.get("tipo") == "NFS-e":
        ret = _codigo_por_texto(n.get("retencao_iss") or "", RETENCAO_ISSQN)
        trib = _codigo_por_texto(n.get("tributacao_issqn") or "", TRIBUTACAO_ISSQN) or 1
        apur_sn = _codigo_por_texto(n.get("regime_apuracao_sn") or "", REGIME_APURACAO_SN)
        incid = n.get("municipio_incidencia") or "município de incidência"
        if trib != 1:
            add("ISSQN", "Municipal", "-", "Sem recolhimento", "-", 0.0, TRIBUTACAO_ISSQN[trib])
        elif ret == 2:
            add("ISSQN", "Municipal", "Tomador (retido)", f"Guia municipal de ISS retido ({incid})", "Guia municipal",
                n["iss"], "Retido na fonte", n["bc_iss"], n["aliquota_iss"])
        elif ret == 3:
            add("ISSQN", "Municipal", "Intermediário (retido)", f"Guia municipal de ISS retido ({incid})",
                "Guia municipal", n["iss"], "Retido na fonte", n["bc_iss"], n["aliquota_iss"])
        elif mei:
            add("ISSQN", "Municipal", "Prestador (MEI)", "DAS-MEI (valor fixo mensal)", "DAS-MEI", n["iss"],
                "Incluído no valor fixo do MEI", n["bc_iss"], n["aliquota_iss"])
        elif simples_me and apur_sn != 2:
            add("ISSQN", "Municipal", "Prestador (Simples Nacional)", "DAS (PGDAS-D)", "DAS", n["iss"],
                "Recolhido dentro do DAS", n["bc_iss"], n["aliquota_iss"])
        else:
            add("ISSQN", "Municipal", "Prestador", f"Guia municipal de ISS próprio ({incid})", "Guia municipal",
                n["iss"], "Apuração própria", n["bc_iss"], n["aliquota_iss"])
        item = _item_lc116(n.get("codigo_servico"))
        sub = (n.get("codigo_servico") or "")[:5]
        if n["irrf"] > 0:
            chave = "IRRF_COMISSOES" if item in ITENS_COMISSAO or sub in SUBITENS_COMISSAO else "IRRF_SERVICOS"
            add("IRRF", "Federal", "Tomador (fonte pagadora)", "DARF", DARF[chave][0], n["irrf"], "Retido na fonte",
                n["valor_bruto"], round(100 * n["irrf"] / n["valor_bruto"], 2) if n["valor_bruto"] else None)
        if n["contrib_sociais_retidas"] > 0:
            desc = _sem_acento(n.get("desc_contrib_sociais") or "")
            if not desc or "pis/cofins/csll retidos" in desc:
                codigo = DARF["CSRF"][0]
            else:
                retidos = [t for t in ("pis", "cofins", "csll") if re.search(rf"\b{t}\b[^,]*retid(?!o nao)", desc)
                           and not re.search(rf"\b{t}\b[^,]*nao retid", desc)]
                codigo = " + ".join(DARF[t.upper()][0] for t in retidos) or DARF["CSRF"][0]
            add("PIS/COFINS/CSLL (CSRF)", "Federal", "Tomador (fonte pagadora)", "DARF", codigo,
                n["contrib_sociais_retidas"], "Retido na fonte", n["valor_bruto"],
                round(100 * n["contrib_sociais_retidas"] / n["valor_bruto"], 2) if n["valor_bruto"] else None)
        if n["cp_retida"] > 0:
            add("INSS (contribuição previdenciária)", "Federal", "Tomador (fonte pagadora)",
                "DCTFWeb (EFD-Reinf R-2010), DARF gerado pela DCTFWeb", "DCTFWeb", n["cp_retida"], "Retido na fonte",
                n["valor_bruto"], round(100 * n["cp_retida"] / n["valor_bruto"], 2) if n["valor_bruto"] else None)
        for trib, campo in (("PIS", "pis_proprio"), ("COFINS", "cofins_proprio")):
            if n[campo] > 0:
                cods = f"{DARF[trib + '_CUMULATIVO'][0]} (cumulativo) ou {DARF[trib + '_NAO_CUMULATIVO'][0]} " \
                       "(não cumulativo)"
                add(trib, "Federal", "Prestador", "DARF", "DAS" if simples_me or mei else cods, n[campo],
                    "Apuração própria")
    else:
        uf = (n.get("emitente_municipio_uf") or "").split("/")[-1].strip() or "UF do emitente"
        guia_das = simples_me or mei
        if n["icms"] > 0 or not guia_das:
            add("ICMS", "Estadual", "Emitente", "DAS (PGDAS-D)" if guia_das else f"Guia estadual de ICMS ({uf})",
                "DAS" if guia_das else "Guia estadual", n["icms"], "Apuração própria", n["bc_icms"])
        if n["icms_st"] > 0:
            interestadual = str(n.get("cfop") or "").startswith("6")
            add("ICMS-ST", "Estadual", "Emitente (substituto tributário)",
                "GNRE" if interestadual else f"Guia estadual de ICMS-ST ({uf})",
                "GNRE" if interestadual else "Guia estadual", n["icms_st"], "Substituição tributária")
        if n["ipi"] > 0:
            add("IPI", "Federal", "Emitente", "DARF", DARF["IPI"][0], n["ipi"], "Apuração própria")
        for trib in ("pis", "cofins"):
            if n[trib] > 0:
                t = trib.upper()
                add(t, "Federal", "Emitente", "DAS (PGDAS-D)" if guia_das else "DARF",
                    "DAS" if guia_das else f"{DARF[t + '_CUMULATIVO'][0]} ou {DARF[t + '_NAO_CUMULATIVO'][0]}",
                    n[trib], "Apuração própria")
    for trib, campo, esfera in (("IBS", "ibs", "Estadual + Municipal"), ("CBS", "cbs", "Federal")):
        if n[campo] > 0:
            add(trib, esfera, n.get("tipo") == "NFS-e" and "Prestador" or "Emitente",
                "Reforma tributária: destaque na nota", "-", n[campo],
                "2026: fase de teste (LC 214/2025); confira a dispensa de recolhimento")
    return linhas


def erros_da_nota(n: dict, hoje: Optional[date] = None, vistos: Optional[set] = None) -> list:
    """Problemas de emissão: (gravidade, mensagem). Gravidade: Erro, Alerta ou Informação."""
    hoje = hoje or date.today()
    e = []
    add = lambda g, m: e.append((g, m))  # noqa: E731
    for papel, campo in (("emitente", "emitente_cnpj"), ("tomador/destinatário", "destinatario_cnpj")):
        doc = so_digitos(n.get(campo))
        if len(doc) == 14 and not cnpj_valido(doc):
            add("Erro", f"CNPJ do {papel} inválido (dígito verificador): {n.get(campo)}")
        elif len(doc) == 11 and not cpf_valido(doc):
            add("Erro", f"CPF do {papel} inválido (dígito verificador): {n.get(campo)}")
        elif papel == "emitente" and not doc:
            add("Erro", "CNPJ/CPF do emitente não encontrado")
    if "cancel" in _sem_acento(n.get("situacao") or "") or "substitu" in _sem_acento(n.get("situacao") or ""):
        add("Alerta", f"Nota {n.get('situacao')}: não deve entrar na apuração")
    if n.get("emissao"):
        emissao = date.fromisoformat(n["emissao"])
        if emissao > hoje:
            add("Erro", f"Data de emissão no futuro ({emissao:%d/%m/%Y})")
        if n.get("competencia") and date.fromisoformat(n["competencia"]) > emissao:
            add("Alerta", "Competência posterior à data de emissão")
    else:
        add("Alerta", "Data de emissão não encontrada")
    if vistos is not None:
        chave = n.get("chave") or f"{so_digitos(n.get('emitente_cnpj'))}-{n.get('tipo')}-{n.get('numero')}"
        if chave in vistos:
            add("Erro", "Nota em duplicidade no lote (mesma chave ou mesmo emitente e número)")
        vistos.add(chave)
    if n["valor_bruto"] <= 0:
        add("Erro", "Valor bruto não encontrado ou zerado")
        return e

    enq = enquadramento(n)
    optante = enq.startswith(("Simples", "MEI"))
    if n.get("tipo") == "NFS-e":
        cod = n.get("codigo_servico") or ""
        item = _item_lc116(cod)
        if not re.fullmatch(r"\d{2}\.\d{2}\.\d{2}", cod):
            add("Erro", f"Código de tributação nacional ausente ou fora do formato XX.XX.XX ({cod or 'vazio'})")
        elif item not in ITENS_LC116:
            add("Erro", f"Item {item:02d} não existe na lista da LC 116/2003 (itens 01 a 40)")
        if enq.startswith("Não identificado"):
            add("Alerta", "Opção pelo Simples Nacional não informada na nota")
        trib = _codigo_por_texto(n.get("tributacao_issqn") or "", TRIBUTACAO_ISSQN) or 1
        ret = _codigo_por_texto(n.get("retencao_iss") or "", RETENCAO_ISSQN)
        iss_no_das = enq.startswith("MEI") or (enq.startswith("Simples") and _codigo_por_texto(
            n.get("regime_apuracao_sn") or "", REGIME_APURACAO_SN) != 2 and not n["aliquota_iss"])
        if trib == 1 and not iss_no_das:
            base_calc = n["valor_bruto"] - n["desconto_incondicionado"] - n["deducoes"]
            if n["bc_iss"] and abs(n["bc_iss"] - base_calc) > TOLERANCIA:
                add("Erro", f"Base de cálculo do ISS ({br(n['bc_iss'], 2)}) diferente de valor − desconto incondicionado"
                            f" − deduções ({br(base_calc, 2)})")
            if n["aliquota_iss"] and not (ALIQ_ISS_MIN - 1e-9 <= n["aliquota_iss"] <= ALIQ_ISS_MAX + 1e-9):
                add("Erro", f"Alíquota do ISS de {br(n['aliquota_iss'], 2)}% fora do intervalo de 2% a 5% "
                            "(LC 116/2003, arts. 8º e 8º-A)")
            if n["bc_iss"] and n["aliquota_iss"] and abs(n["bc_iss"] * n["aliquota_iss"] / 100 - n["iss"]) > TOLERANCIA:
                add("Erro", f"ISS apurado ({br(n['iss'], 2)}) diferente de base × alíquota "
                            f"({br(n['bc_iss'] * n['aliquota_iss'] / 100, 2)})")
            if not n["aliquota_iss"] and not n["iss"]:
                add("Alerta", "Operação tributável sem alíquota nem valor de ISS")
        if ret in (2, 3) and not so_digitos(n.get("destinatario_cnpj")):
            add("Erro", "ISS retido pelo tomador, mas o tomador não está identificado")
        elif ret == 2 and len(so_digitos(n.get("destinatario_cnpj"))) == 11:
            add("Alerta", "ISS retido por tomador pessoa física: confira se a lei do município prevê")
        incid = _sem_acento((n.get("municipio_incidencia") or "").split("/")[0]).strip()
        sede = _sem_acento((n.get("emitente_municipio_uf") or "").split("/")[0]).strip()
        if incid and sede and incid != sede and ret not in (2, 3) and trib == 1:
            add("Alerta", "ISS devido em município diferente da sede do prestador e sem retenção: "
                          "confira a guia no município de incidência")
        if optante and (n["irrf"] > 0 or n["contrib_sociais_retidas"] > 0):
            add("Erro", "Retenção de IRRF/PIS/COFINS/CSLL em prestador optante do Simples Nacional "
                        "(em regra não se aplica)")
        if 0 < n["irrf"] <= LIMITE_RETENCAO:
            add("Alerta", f"IRRF de R$ {br(n['irrf'], 2)}: valores até R$ 10,00 não são retidos")
        if 0 < n["contrib_sociais_retidas"] <= LIMITE_RETENCAO:
            add("Alerta", f"PIS/COFINS/CSLL retidos de R$ {br(n['contrib_sociais_retidas'], 2)}: "
                          "valores até R$ 10,00 não são retidos")
        if n["irrf"] > 0:
            p = 100 * n["irrf"] / n["valor_bruto"]
            if not any(abs(p - x) <= 0.02 for x in (1.0, 1.5)):
                add("Erro", f"IRRF de {br(p, 2)}% do valor: o esperado é 1,5% (ou 1% em limpeza, conservação, "
                            "segurança e locação de mão de obra)")
        if n["contrib_sociais_retidas"] > 0:
            p = 100 * n["contrib_sociais_retidas"] / n["valor_bruto"]
            if abs(p - 4.65) > 0.02 and "pis/cofins/csll retidos" in _sem_acento(n.get("desc_contrib_sociais") or
                                                                                 "pis/cofins/csll retidos"):
                add("Erro", f"PIS/COFINS/CSLL retidos de {br(p, 2)}% do valor: o esperado é 4,65%")
        if n["cp_retida"] > 0:
            p = 100 * n["cp_retida"] / n["valor_bruto"]
            if not any(abs(p - x) <= 0.02 for x in (11.0, 3.5)):
                add("Alerta", f"INSS retido de {br(p, 2)}% do valor: o usual é 11% (3,5% para empresas com "
                              "desoneração da folha)")
        retencoes = (n["iss"] if ret in (2, 3) else 0) + n["irrf"] + n["cp_retida"] + n["contrib_sociais_retidas"]
        if n["total_retencoes"] and abs(n["total_retencoes"] - retencoes) > TOLERANCIA:
            add("Erro", f"Total das retenções ({br(n['total_retencoes'], 2)}) diferente da soma das retenções "
                        f"({br(retencoes, 2)})")
        liquido = n["valor_bruto"] - n["desconto_incondicionado"] - n["desconto_condicionado"] - retencoes
        if n["valor_liquido"] and abs(n["valor_liquido"] - liquido) > TOLERANCIA:
            add("Erro", f"Valor líquido ({br(n['valor_liquido'], 2)}) diferente de bruto − descontos − retenções "
                        f"({br(liquido, 2)})")
        if n.get("emissao") and n["emissao"] >= "2026-01-01" and not (n["ibs"] or n["cbs"]):
            add("Informação", "Sem destaque de IBS/CBS (2026 é o ano de teste da reforma tributária)")
    else:
        cfop = str(n.get("cfop") or "").split(",")[0].strip()
        uf_emit = (n.get("emitente_municipio_uf") or "").split("/")[-1].strip()
        uf_dest = (n.get("destinatario_uf") or "").strip()
        if not re.fullmatch(r"[1-7]\d{3}", cfop):
            add("Erro" if n.get("arquivo", "").lower().endswith(".xml") else "Alerta", "CFOP não encontrado")
        elif uf_emit and uf_dest:
            if cfop[0] == "5" and uf_emit != uf_dest:
                add("Erro", f"CFOP {cfop} (operação interna) para destinatário em outra UF ({uf_dest})")
            if cfop[0] == "6" and uf_emit == uf_dest:
                add("Erro", f"CFOP {cfop} (interestadual) para destinatário na mesma UF ({uf_dest})")
        cst = str(n.get("cst_csosn") or "")
        tem_csosn = bool(re.search(r"\b(101|102|103|201|202|203|300|400|500|900)\b", cst))
        if n.get("crt") and cst:
            if n["crt"].startswith(("Simples", "MEI")) and not tem_csosn and "excesso" not in n["crt"]:
                add("Erro", "Emitente do Simples Nacional usando CST em vez de CSOSN")
            if n["crt"] == "Regime Normal" and tem_csosn:
                add("Erro", "Emitente do regime normal usando CSOSN em vez de CST")
        esperado = (n["valor_bruto"] - n["desconto_incondicionado"] + n["icms_st"] + n["frete"] + n["seguro"] +
                    n["outras_despesas"] + n["ipi"])
        if n["valor_liquido"] and abs(n["valor_liquido"] - esperado) > TOLERANCIA and \
                n.get("arquivo", "").lower().endswith(".xml"):
            add("Erro", f"Valor total da nota ({br(n['valor_liquido'], 2)}) diferente de produtos − desconto + ST + "
                        f"frete + seguro + outras + IPI ({br(esperado, 2)})")
        if n["icms"] and n["bc_icms"]:
            p = 100 * n["icms"] / n["bc_icms"]
            if p > 35:
                add("Alerta", f"Alíquota efetiva de ICMS de {br(p, 1)}% parece alta: confira a base de cálculo")
    return e


def chave_da_nota(n: dict) -> str:
    """Identidade da nota: a chave de acesso; sem ela, emitente + tipo + número."""
    chave = so_digitos(n.get("chave"))
    return chave if len(chave) >= 44 else f"{so_digitos(n.get('emitente_cnpj'))}-{n.get('tipo')}-{n.get('numero')}"


def marcar_duplicadas(notas: pd.DataFrame) -> pd.Series:
    """Para cada nota, o arquivo da nota original quando ela é repetida no lote (None quando é a original).
    A mesma nota costuma chegar em PDF e em XML: a original é a do XML, que é mais confiável; entre arquivos do
    mesmo tipo, a primeira lida."""
    if notas.empty:
        return pd.Series(dtype=object)
    chaves = notas.apply(lambda r: chave_da_nota(r.to_dict()), axis=1)
    prioridade = (~notas["arquivo"].fillna("").str.lower().str.endswith(".xml")).astype(int)
    ordem = pd.DataFrame({"chave": chaves, "prioridade": prioridade, "pos": range(len(notas))}, index=notas.index)
    original = ordem.sort_values(["prioridade", "pos"]).groupby("chave").head(1)
    arquivo_original = notas.loc[original.index].set_index(chaves[original.index])["arquivo"]
    dup = ~notas.index.isin(original.index)
    return pd.Series(np.where(dup, chaves.map(arquivo_original), None), index=notas.index)


def conciliar(notas: pd.DataFrame, hoje: Optional[date] = None) -> tuple:
    """Notas lidas → (notas com enquadramento/operação/quem recolhe/erros, tributos em formato longo, erros)."""
    if notas.empty:
        vazio = pd.DataFrame()
        return notas.assign(enquadramento=[], operacao=[], qtd_erros=[], qtd_alertas=[]), vazio, vazio
    notas = notas.reset_index(drop=True)
    duplicada_de = marcar_duplicadas(notas)
    linhas_trib, linhas_erro, resumo = [], [], []
    for i, linha in notas.iterrows():
        n = linha.to_dict()
        for c in NUMERICAS:
            n[c] = float(n.get(c) or 0)
        cancelada = re.search(r"cancel|substitu", _sem_acento(n.get("situacao") or "")) is not None
        original = duplicada_de[i]
        # coluna de filtro para a tabela dinâmica: fora duplicadas, canceladas e substituídas
        marcas = {"duplicada": "Sim" if original else "Não", "considerar": "Não" if original or cancelada else "Sim"}
        ident = {"arquivo": n["arquivo"], "pagina": n["pagina"], "tipo": n["tipo"], "numero": n["numero"],
                 "emitente_cnpj": n["emitente_cnpj"], "emitente_nome": n["emitente_nome"], **marcas}
        trib = tributos_da_nota(n)
        linhas_trib += [{**ident, **t} for t in trib]
        erros = erros_da_nota(n, hoje)
        if original:
            erros.insert(0, ("Erro", f"Nota em duplicidade: a mesma nota está em {original} e só ela entra nos "
                                     "totais (filtre 'Considerar na apuração' = Sim)"))
        linhas_erro += [{**ident, "gravidade": g, "problema": m} for g, m in erros]
        a_recolher = [t for t in trib if t["tributo"] not in ("IBS", "CBS") and t["situacao"] not in
                      TRIBUTACAO_ISSQN.values()]                      # IBS/CBS de 2026: só destaque informativo
        retido_tomador = sum(t["valor"] for t in a_recolher if t["responsavel"].startswith(("Tomador", "Intermed")))
        resumo.append({**marcas, "duplicada_de": original, "enquadramento": enquadramento(n), "operacao": operacao(n),
                       "recolhe_tomador": round(retido_tomador, 2),
                       "recolhe_emitente": round(sum(t["valor"] for t in a_recolher) - retido_tomador, 2),
                       "qtd_erros": sum(g == "Erro" for g, _ in erros),
                       "qtd_alertas": sum(g == "Alerta" for g, _ in erros)})
    completas = pd.concat([notas.reset_index(drop=True), pd.DataFrame(resumo)], axis=1)
    return completas, pd.DataFrame(linhas_trib), pd.DataFrame(linhas_erro,
                                                              columns=["arquivo", "pagina", "tipo", "numero",
                                                                       "emitente_cnpj", "emitente_nome", "duplicada",
                                                                       "considerar", "gravidade", "problema"])


# ──────────────────────────────────────────────
# Enquadramento pela Receita (dados públicos do CNPJ)
# ──────────────────────────────────────────────

def consultar_cnpj(cnpj: str, timeout: int = 15) -> dict:
    """Porte, opção pelo Simples/MEI e atividade pela BrasilAPI (dados públicos do CNPJ na Receita)."""
    import requests
    d = so_digitos(cnpj)
    r = requests.get(f"https://brasilapi.com.br/api/cnpj/v1/{d}", timeout=timeout)
    r.raise_for_status()
    j = r.json()
    simples = "MEI" if j.get("opcao_pelo_mei") else "Simples Nacional" if j.get("opcao_pelo_simples") else \
        "Não optante"
    return {"cnpj": formatar_cnpj(d), "razao_social": j.get("razao_social"), "porte": j.get("porte"),
            "opcao_receita": simples, "natureza_juridica": j.get("natureza_juridica"),
            "atividade_principal": j.get("cnae_fiscal_descricao"), "situacao_cadastral":
            j.get("descricao_situacao_cadastral")}


def comparar_enquadramento(notas: pd.DataFrame, cadastro: pd.DataFrame) -> pd.DataFrame:
    """Enquadramento declarado na nota × situação na Receita. Diverge quando a nota diz uma coisa e o CNPJ outra."""
    if cadastro.empty or notas.empty:
        return pd.DataFrame()
    m = notas.merge(cadastro, left_on="emitente_cnpj", right_on="cnpj", how="inner")
    nota_sn = m["enquadramento"].str.startswith(("Simples", "MEI"))
    receita_sn = m["opcao_receita"].isin(["Simples Nacional", "MEI"])
    m["diverge"] = nota_sn != receita_sn
    return m[["arquivo", "numero", "emitente_cnpj", "emitente_nome", "enquadramento", "opcao_receita", "porte",
              "situacao_cadastral", "diverge"]]


# ──────────────────────────────────────────────
# Planilha de saída
# ──────────────────────────────────────────────

ROTULOS = {
    "arquivo": "Arquivo", "pagina": "Página", "tipo": "Tipo", "situacao": "Situação", "numero": "Número",
    "chave": "Chave de acesso", "emissao": "Emissão", "competencia": "Competência",
    "emitente_cnpj": "CNPJ emitente", "emitente_nome": "Emitente", "emitente_municipio_uf": "Município/UF emitente",
    "enquadramento": "Enquadramento", "operacao": "Operação", "simples_nacional": "Simples Nacional (nota)",
    "regime_apuracao_sn": "Regime de apuração SN", "regime_especial": "Regime especial ISSQN", "crt": "CRT",
    "destinatario_cnpj": "CNPJ tomador/destinatário", "destinatario_nome": "Tomador/destinatário",
    "destinatario_uf": "UF tomador/destinatário", "codigo_servico": "Código de serviço (LC 116)",
    "codigo_municipal": "Código municipal", "nbs": "NBS", "descricao": "Descrição", "local_prestacao":
    "Local da prestação", "municipio_incidencia": "Município de incidência", "tributacao_issqn": "Tributação ISSQN",
    "cfop": "CFOP", "natureza_operacao": "Natureza da operação", "cst_csosn": "CST/CSOSN",
    "valor_bruto": "Valor bruto", "desconto_incondicionado": "Desconto incondicionado",
    "desconto_condicionado": "Desconto condicionado", "deducoes": "Deduções", "bc_iss": "Base ISS",
    "aliquota_iss": "Alíquota ISS (%)", "retencao_iss": "Retenção do ISS", "iss": "ISS", "irrf": "IRRF",
    "cp_retida": "INSS retido", "contrib_sociais_retidas": "PIS/COFINS/CSLL retidos",
    "desc_contrib_sociais": "Contribuições retidas (detalhe)", "pis_proprio": "PIS próprio",
    "cofins_proprio": "COFINS próprio", "total_retencoes": "Total retenções", "valor_liquido": "Valor líquido",
    "bc_icms": "Base ICMS", "icms": "ICMS", "icms_st": "ICMS-ST", "ipi": "IPI", "pis": "PIS", "cofins": "COFINS",
    "frete": "Frete", "seguro": "Seguro", "outras_despesas": "Outras despesas", "cst_ibscbs": "CST IBS/CBS",
    "cclasstrib": "cClassTrib", "ibs": "IBS", "cbs": "CBS", "recolhe_tomador": "Recolhe o tomador",
    "recolhe_emitente": "Recolhe o emitente", "qtd_erros": "Erros", "qtd_alertas": "Alertas",
    "tributo": "Tributo", "esfera": "Esfera", "responsavel": "Quem recolhe", "guia": "Guia",
    "codigo_recolhimento": "Código de recolhimento", "base": "Base", "aliquota": "Alíquota (%)", "valor": "Valor",
    "gravidade": "Gravidade", "problema": "Problema", "duplicada": "Duplicada",
    "duplicada_de": "Original no arquivo", "considerar": "Considerar na apuração",
}
ORDEM_NOTAS = ["arquivo", "pagina", "tipo", "situacao", "considerar", "duplicada", "duplicada_de", "numero", "emissao", "competencia", "emitente_cnpj",
               "emitente_nome", "enquadramento", "operacao", "codigo_servico", "cfop", "destinatario_cnpj",
               "destinatario_nome", "municipio_incidencia", "retencao_iss", "valor_bruto", "desconto_incondicionado",
               "desconto_condicionado", "bc_iss", "aliquota_iss", "iss", "irrf", "contrib_sociais_retidas",
               "cp_retida", "total_retencoes", "valor_liquido", "icms", "icms_st", "ipi", "pis", "cofins", "ibs",
               "cbs", "recolhe_tomador", "recolhe_emitente", "qtd_erros", "qtd_alertas", "chave",
               "simples_nacional", "regime_especial", "crt", "cst_csosn", "natureza_operacao", "nbs", "descricao"]
MOEDA = {"valor_bruto", "desconto_incondicionado", "desconto_condicionado", "deducoes", "bc_iss", "iss", "irrf",
         "cp_retida", "contrib_sociais_retidas", "pis_proprio", "cofins_proprio", "total_retencoes", "valor_liquido",
         "bc_icms", "icms", "icms_st", "ipi", "pis", "cofins", "frete", "seguro", "outras_despesas", "ibs", "cbs",
         "recolhe_tomador", "recolhe_emitente", "base", "valor"}


def resumo_dinamico(tributos: pd.DataFrame, linhas: list, colunas: Optional[list] = None,
                    valor: str = "valor", funcao: str = "sum", so_consideradas: bool = True) -> pd.DataFrame:
    """Tabela dinâmica (pivot) dos tributos, com total geral. Por padrão, sem notas duplicadas, canceladas e
    substituídas (coluna 'considerar')."""
    if so_consideradas and "considerar" in tributos:
        tributos = tributos[tributos["considerar"] == "Sim"]
    if tributos.empty:
        return pd.DataFrame()
    return pd.pivot_table(tributos, index=linhas, columns=colunas or None, values=valor, aggfunc=funcao,
                          fill_value=0, margins=True, margins_name="Total").round(2)


def planilha(notas: pd.DataFrame, tributos: pd.DataFrame, erros: pd.DataFrame,
             cadastro: Optional[pd.DataFrame] = None) -> bytes:
    """Excel com tabelas filtráveis (Notas, Tributos, Erros), resumos dinâmicos e instruções."""
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.table import Table, TableStyleInfo

    abas = {"Notas": notas[[c for c in ORDEM_NOTAS if c in notas.columns]] if not notas.empty else notas,
            "Tributos": tributos, "Erros": erros}
    if cadastro is not None and not cadastro.empty:
        abas["Enquadramento Receita"] = cadastro
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        pd.DataFrame({"Como usar esta planilha": [
            "Notas: uma linha por nota fiscal, com enquadramento, operação, valores, retenções e quantidade de erros.",
            "Tributos: uma linha por tributo de cada nota (quem recolhe, guia, código de recolhimento e valor). "
            "É a base ideal para tabela dinâmica: Inserir → Tabela Dinâmica → tabela 'Tributos'.",
            "Na tabela dinâmica, filtre 'Considerar na apuração' = Sim: tira notas duplicadas (a mesma nota em PDF e "
            "XML, ou enviada duas vezes), canceladas e substituídas. A coluna 'Duplicada' mostra só as repetidas.",
            "Erros: problemas de emissão encontrados em cada nota (Erro, Alerta ou Informação).",
            "Resumo: totais prontos por tributo × quem recolhe e por código de recolhimento, já sem duplicadas, "
            "canceladas e substituídas.",
            "Todas as abas têm filtros nos cabeçalhos. Códigos de DARF: confira a natureza do pagamento.",
        ]}).to_excel(xl, sheet_name="Leia-me", index=False)
        for nome, df in abas.items():
            df.rename(columns=ROTULOS).to_excel(xl, sheet_name=nome, index=False)
        linha = 0
        if not tributos.empty:
            for titulo, idx, cols in (("Valor por tributo e quem recolhe", ["tributo"], ["responsavel"]),
                                      ("Valor por código de recolhimento", ["esfera", "tributo", "codigo_recolhimento"],
                                       None),
                                      ("Valor por emitente e tributo", ["emitente_nome"], ["tributo"])):
                piv = resumo_dinamico(tributos, idx, cols)
                piv.index.names = [ROTULOS.get(n, n) for n in piv.index.names]
                if cols:
                    piv.columns.name = ROTULOS.get(cols[0], cols[0])
                pd.DataFrame({titulo: []}).to_excel(xl, sheet_name="Resumo", startrow=linha, index=False)
                piv.to_excel(xl, sheet_name="Resumo", startrow=linha + 1)
                linha += len(piv) + 5
        livro = xl.book
        cab = PatternFill("solid", fgColor="33006B")
        for nome, df in abas.items():
            ws = livro[nome]
            ws.freeze_panes = "A2"
            if not df.empty:
                ref = f"A1:{get_column_letter(ws.max_column)}{ws.max_row}"
                tabela = Table(displayName=re.sub(r"\W", "", nome), ref=ref)
                tabela.tableStyleInfo = TableStyleInfo(name="TableStyleMedium12", showRowStripes=True)
                ws.add_table(tabela)
            for j, col in enumerate(df.columns, start=1):
                letra = get_column_letter(j)
                ws[f"{letra}1"].font = Font(bold=True, color="FFFFFF")
                ws[f"{letra}1"].fill = cab
                ws[f"{letra}1"].alignment = Alignment(wrap_text=True, vertical="center")
                largura = max([len(str(ROTULOS.get(col, col)))] +
                              [len(str(v)) for v in df[col].head(200).tolist()]) if len(df) else 12
                ws.column_dimensions[letra].width = min(max(largura + 2, 10), 60)
                if col in MOEDA:
                    for cel in ws[letra][1:]:
                        cel.number_format = '"R$" #,##0.00'
            ws.row_dimensions[1].height = 30
        livro["Leia-me"].column_dimensions["A"].width = 120
        if "Resumo" in livro.sheetnames:
            ws = livro["Resumo"]
            for row in ws.iter_rows():
                for cel in row:
                    if isinstance(cel.value, (int, float)):
                        cel.number_format = '"R$" #,##0.00'
            ws.column_dimensions["A"].width = 34
            ws.column_dimensions["B"].width = 34
            ws.column_dimensions["C"].width = 30
        livro.move_sheet("Resumo", offset=-(len(livro.sheetnames) - 2)) if "Resumo" in livro.sheetnames else None
    return buf.getvalue()


# ──────────────────────────────────────────────
# DANFSe de exemplo (testes e demonstração)
# ──────────────────────────────────────────────

def gerar_danfse_exemplo(dados: dict) -> bytes:
    """Gera um PDF no layout do DANFSe nacional (grade de 4 colunas, rótulo em cima e valor embaixo).
    Serve para testar a aba sem notas reais; os valores vêm prontos em `dados` (texto já formatado)."""
    from fpdf import FPDF
    cols = [0.30, 5.41, 10.51, 15.62]
    larg = 5.10
    pdf = FPDF(unit="cm", format="A4")
    pdf.set_auto_page_break(False)
    pdf.add_page()
    v = lambda k: str(dados.get(k, "-") or "-")  # noqa: E731

    def celula(col, y, rotulo, valor, span=1):
        pdf.set_xy(cols[col] + 0.05, y + 0.06)
        pdf.set_font("Helvetica", "B", 6)
        pdf.cell(larg * span - 0.1, 0.25, rotulo)
        pdf.set_xy(cols[col] + 0.05, y + 0.36)
        pdf.set_font("Helvetica", "", 7)
        pdf.cell(larg * span - 0.1, 0.25, valor[:int(38 * span)])

    def titulo(y, texto):                     # no PDF real o título fica centralizado, abaixo dos rótulos da linha
        pdf.set_xy(cols[0] + 0.05, y + 0.30)
        pdf.set_font("Helvetica", "B", 7)
        pdf.cell(larg - 0.1, 0.3, texto)

    pdf.set_font("Helvetica", "B", 11)
    pdf.set_xy(0.3, 0.4)
    pdf.cell(8, 0.5, "DANFSe v2.0")
    pdf.set_font("Helvetica", "", 8)
    pdf.set_xy(0.3, 0.9)
    pdf.cell(8, 0.4, "Documento Auxiliar da NFS-e")
    if dados.get("marca_dagua"):
        pdf.set_font("Helvetica", "B", 28)
        pdf.set_xy(5, 10)
        pdf.cell(10, 1, dados["marca_dagua"])
    celula(0, 1.48, "CHAVE DE ACESSO DA NFS-e", v("chave"), span=3)
    celula(0, 2.27, "NÚMERO DA NFS-e", v("numero"))
    celula(1, 2.27, "COMPETÊNCIA DA NFS-e", v("competencia"))
    celula(2, 2.27, "DATA E HORA DA EMISSÃO DA NFS-e", v("emissao"))
    celula(0, 3.65, "EMITENTE DA NFS-e", "Prestador")
    celula(1, 3.65, "SITUAÇÃO DA NFS-e", v("situacao"))
    celula(2, 3.65, "FINALIDADE", "NFS-e regular")
    y = 4.4
    titulo(y, "PRESTADOR / FORNECEDOR")
    celula(1, y, "CNPJ / CPF / NIF", v("prest_cnpj"))
    celula(2, y, "Indicador Municipal (Inscrição)", "-")
    celula(3, y, "Telefone", "-")
    celula(0, y + 0.64, "Nome / Nome Empresarial", v("prest_nome"), 2)
    celula(2, y + 0.64, "Município / Sigla UF", v("prest_mun"))
    celula(3, y + 0.64, "Código IBGE / CEP", "-")
    celula(0, y + 1.28, "Endereço", "-", 2)
    celula(2, y + 1.28, "E-mail", "-", 2)
    celula(0, y + 1.92, "Simples Nacional na Data de Competência", v("simples"), 2)
    celula(2, y + 1.92, "Regime de Apuração Tributária pelo SN", v("regime_sn"), 2)
    y = 7.2
    titulo(y, "TOMADOR / ADQUIRENTE")
    celula(1, y, "CNPJ / CPF / NIF", v("toma_cnpj"))
    celula(2, y, "Indicador Municipal (Inscrição)", "-")
    celula(3, y, "Telefone", "-")
    celula(0, y + 0.64, "Nome / Nome Empresarial", v("toma_nome"), 2)
    celula(2, y + 0.64, "Município / Sigla UF", v("toma_mun"))
    celula(3, y + 0.64, "Código IBGE / CEP", "-")
    y = 9.0
    titulo(y, "SERVIÇO PRESTADO")
    celula(1, y, "Código de Tributação Nacional / Municipal", v("codigo"))
    celula(2, y, "Código da NBS", v("nbs"))
    celula(3, y, "Local da Prestação / Sigla UF / País", v("local"))
    pdf.set_font("Helvetica", "B", 6)
    pdf.set_xy(cols[0] + 0.05, y + 1.05)
    pdf.cell(5, 0.25, "Descrição do Serviço")
    pdf.set_font("Helvetica", "", 7)
    pdf.set_xy(cols[0] + 0.05, y + 1.35)
    pdf.multi_cell(20, 0.3, v("descricao"))
    y = 11.0
    titulo(y, "TRIBUTAÇÃO MUNICIPAL (ISSQN)")
    celula(1, y, "Tipo de Tributação do ISSQN", v("trib_issqn"))
    celula(2, y, "Município / Sigla UF / País de Incidência do ISSQN", v("incidencia"), 2)
    celula(0, y + 0.65, "Regime Especial de Tributação do ISSQN", v("regime_especial"))
    celula(1, y + 0.65, "Tipo de Imunidade do ISSQN", "-")
    celula(2, y + 0.65, "Suspensão da Exigibilidade do ISSQN", "-")
    celula(3, y + 0.65, "Número Processo Suspensão", "-")
    celula(0, y + 1.30, "Benefício Municipal", "-")
    celula(1, y + 1.30, "Cálculo do BM", "-")
    celula(2, y + 1.30, "Total Deduções/Reduções", v("deducoes"))
    celula(3, y + 1.30, "Desconto Incondicionado", v("desc_incond"))
    celula(0, y + 1.95, "BC ISSQN", v("bc_iss"))
    celula(1, y + 1.95, "Alíquota Aplicada", v("aliquota"))
    celula(2, y + 1.95, "Retenção do ISSQN", v("retencao"))
    celula(3, y + 1.95, "ISSQN Apurado", v("iss"))
    y = 13.8
    titulo(y, "TRIBUTAÇÃO FEDERAL (EXCETO CBS)")
    celula(1, y, "IRRF", v("irrf"))
    celula(2, y, "Contribuição Previdenciária - Retida", v("cp"))
    celula(3, y, "Contribuições Sociais - Retidas", v("csrf"))
    celula(0, y + 0.65, "PIS - Débito Apuração Própria", v("pis"))
    celula(1, y + 0.65, "COFINS - Débito Apuração Própria", v("cofins"))
    celula(2, y + 0.65, "Descrição Contrib. Sociais - Retidas", v("desc_csrf"), 2)
    y = 15.3
    titulo(y, "TRIBUTAÇÃO IBS / CBS")
    celula(1, y, "CST / cClassTrib", v("cst"))
    celula(0, y + 1.95, "Valor Total Apurado - IBS", v("ibs"))
    celula(3, y + 1.95, "Valor Total Apurado - CBS", v("cbs"))
    y = 18.0
    titulo(y, "VALOR TOTAL DA NFS-e")
    celula(1, y, "VALOR DA OPERAÇÃO / SERVIÇO", v("valor"))
    celula(2, y, "Desconto Incondicionado", v("desc_incond"))
    celula(3, y, "Desconto Condicionado", v("desc_cond"))
    celula(0, y + 0.65, "Total das Retenções (ISSQN / Federais)", v("total_ret"))
    celula(1, y + 0.65, "VALOR LÍQUIDO DA NFS-e", v("liquido"))
    celula(2, y + 0.65, "Total do IBS/CBS", "-")
    celula(3, y + 0.65, "VALOR LÍQUIDO DA NFS-e + IBS/CBS", v("liquido_ibscbs"))
    pdf.set_xy(0.35, 19.6)
    pdf.set_font("Helvetica", "B", 6)
    pdf.cell(5, 0.3, "INFORMAÇÕES COMPLEMENTARES")
    return bytes(pdf.output())


def _brl(v: float) -> str:
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def notas_de_exemplo(quantidade: int = 12, semente: int = 3) -> list:
    """Lote fictício de DANFSe (com alguns erros de propósito) para demonstrar a aba."""
    rng = np.random.default_rng(semente)
    emitentes = [("11.222.333/0001-81", "Alfa Sistemas Ltda", "São Paulo / SP", 1, "17.01.01 / 1701", "1,5"),
                 ("12.345.678/0001-95", "Beta Consultoria ME", "Campinas / SP", 3, "17.01.01 / 1701", ""),
                 ("24.681.357/0001-40", "Gama Limpeza Ltda", "Osasco / SP", 1, "07.10.01 / 0710", "1"),
                 ("13.579.246/0001-01", "Delta Software Ltda", "São Paulo / SP", 1, "01.07.01 / 0107", "1,5")]
    arquivos = []
    for i in range(quantidade):
        cnpj, nome, mun, simples, codigo, irrf_p = emitentes[i % len(emitentes)]
        valor = float(rng.integers(8, 120) * 100)
        aliq = 2.0 if simples == 3 else 5.0 if codigo.startswith("01") else 2.9
        ret_iss = i % 3 == 0
        iss = round(valor * aliq / 100, 2)
        irrf = round(valor * float(irrf_p.replace(",", ".")) / 100, 2) if irrf_p else 0.0
        csrf = round(valor * 0.0465, 2) if simples == 1 else 0.0
        cp = round(valor * 0.11, 2) if codigo.startswith("07") else 0.0
        if i == 5:                            # erro de propósito: retenção federal de prestador do Simples
            irrf, csrf = round(valor * 0.015, 2), round(valor * 0.0465, 2)
        if i == 7:                            # erro de propósito: alíquota de ISS acima do máximo
            aliq, iss = 6.0, round(valor * 0.06, 2)
        if simples == 3:
            iss = 0.0
        total_ret = (iss if ret_iss else 0) + irrf + csrf + cp
        liquido = valor - total_ret + (25.0 if i == 9 else 0)     # erro de propósito: líquido não fecha
        dia = 3 + i
        dados = {
            "chave": f"3550308{cnpj.replace('.', '').replace('/', '').replace('-', '')}{i:020d}"[:50],
            "numero": str(100 + i), "competencia": f"{dia:02d}/11/2026", "emissao": f"{dia:02d}/11/2026 10:00:00",
            "situacao": "NFS-e Regular", "prest_cnpj": "11.222.333/0001-80" if i == 11 else cnpj,
            "prest_nome": nome, "prest_mun": mun,
            # como no DANFSe real: texto sem o número e cortado na célula
            "simples": "Optante - Microempresa ou Empresa de ..." if simples == 3 else "Não Optante",
            "regime_sn": REGIME_APURACAO_SN[1][4:] if simples == 3 else "-", "toma_cnpj": "98.765.432/0001-98",
            "toma_nome": "Cliente Exemplo S.A.", "toma_mun": "São Paulo / SP", "codigo": codigo,
            "nbs": "1.1503.10.000", "local": "São Paulo / SP / Brasil",
            "descricao": f"Serviço de exemplo nº {i + 1} (nota fictícia gerada para demonstração).",
            "trib_issqn": "Operação Tributável", "incidencia": "São Paulo / SP / Brasil", "regime_especial": "Nenhum",
            "deducoes": "-", "desc_incond": "-",
            # Simples com ISS no DAS: o DANFSe real sai sem base, alíquota e ISS
            "bc_iss": "-" if simples == 3 else _brl(valor),
            "aliquota": "-" if simples == 3 else f"{aliq:.2f}%".replace(".", ","),
            "retencao": "Retido pelo Tomador" if ret_iss else "Não Retido", "iss": "-" if simples == 3 else _brl(iss),
            "irrf": _brl(irrf) if irrf else "-", "cp": _brl(cp) if cp else "-", "csrf": _brl(csrf) if csrf else "-",
            "desc_csrf": RETENCAO_PIS_COFINS[3] if csrf else "-", "cst": "000 / 000001",
            "ibs": _brl(round(valor * 0.001, 2)), "cbs": _brl(round(valor * 0.009, 2)), "valor": _brl(valor),
            "desc_cond": "-", "total_ret": _brl(total_ret), "liquido": _brl(liquido), "liquido_ibscbs": "-",
            "marca_dagua": "CANCELADA" if i == 10 else "",
        }
        arquivos.append((f"nfse_exemplo_{100 + i}.pdf", gerar_danfse_exemplo(dados)))
    return arquivos
