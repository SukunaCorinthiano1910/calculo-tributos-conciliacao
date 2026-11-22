"""Testes do cálculo de tributos e da conciliação (DANFSe gerado no layout oficial, XMLs sintéticos, sem internet)."""
import io
import zipfile
from datetime import date

import pandas as pd
import pytest
from openpyxl import load_workbook

import modulo_tributos as mt

HOJE = date(2026, 11, 20)


@pytest.fixture(scope="module")
def lote():
    notas, problemas = mt.ler_lote(mt.notas_de_exemplo(12), processos=1)
    assert problemas == []
    completas, tributos, erros = mt.conciliar(notas, hoje=HOJE)
    return completas, tributos, erros


def _nota(completas, numero):
    return completas[completas["numero"] == str(numero)].iloc[0]


def test_documentos_e_numeros():
    assert mt.cnpj_valido("11.222.333/0001-81") and not mt.cnpj_valido("11.222.333/0001-80")
    assert mt.cpf_valido("529.982.247-25") and not mt.cpf_valido("111.111.111-11")
    assert mt.numero("R$ 1.234,56") == 1234.56 and mt.numero("2,90%") == 2.9 and mt.numero("-") == 0
    assert mt.br(1234.5) == "1.234,50" and mt.formatar_cnpj("11222333000181") == "11.222.333/0001-81"


def test_le_os_campos_do_danfse(lote):
    completas, _, _ = lote
    n = _nota(completas, 106)                              # limpeza, não optante, ISS retido, INSS 11%
    assert n["tipo"] == "NFS-e" and n["emitente_cnpj"] == "24.681.357/0001-40"
    assert n["emitente_nome"] == "Gama Limpeza Ltda" and n["emitente_municipio_uf"] == "Osasco / SP"
    assert n["destinatario_cnpj"] == "98.765.432/0001-98" and n["codigo_servico"] == "07.10.01"
    assert n["codigo_municipal"] == "0710" and n["emissao"] == "2026-11-09" and n["competencia"] == "2026-11-09"
    assert n["valor_bruto"] == 10500 and n["aliquota_iss"] == 2.9 and n["iss"] == pytest.approx(304.5)
    assert n["retencao_iss"] == "Retido pelo Tomador" and n["irrf"] == 105 and n["cp_retida"] == 1155
    assert n["contrib_sociais_retidas"] == pytest.approx(488.25) and n["desc_contrib_sociais"] == "PIS/COFINS/CSLL Retidos"
    assert n["valor_liquido"] == pytest.approx(8447.25) and n["cst_ibscbs"] == "000" and n["cclasstrib"] == "000001"
    assert n["enquadramento"].startswith("Não optante") and "item 07" in n["operacao"]
    assert "Serviço de exemplo nº 7" in n["descricao"]


def test_quem_recolhe_e_codigos(lote):
    _, tributos, _ = lote
    t = tributos[tributos["numero"] == "106"].set_index("tributo")
    assert t.loc["ISSQN", "responsavel"] == "Tomador (retido)" and t.loc["ISSQN", "codigo_recolhimento"] == "Guia municipal"
    assert t.loc["IRRF", "codigo_recolhimento"] == "1708" and t.loc["IRRF", "aliquota"] == 1.0
    assert t.loc["PIS/COFINS/CSLL (CSRF)", "codigo_recolhimento"] == "5952"
    assert t.loc["INSS (contribuição previdenciária)", "codigo_recolhimento"] == "DCTFWeb"
    simples = tributos[tributos["numero"] == "101"].set_index("tributo")
    assert simples.loc["ISSQN", "guia"] == "DAS (PGDAS-D)" and simples.loc["ISSQN", "responsavel"].startswith("Prestador")
    assert set(tributos["esfera"]) >= {"Municipal", "Federal"}


def test_valores_a_recolher_sem_ibs_cbs(lote):
    completas, _, _ = lote
    n = _nota(completas, 100)                              # ISS, IRRF e CSRF retidos; IBS/CBS só informativos
    assert n["recolhe_tomador"] == pytest.approx(886.90) and n["recolhe_emitente"] == 0


def test_aponta_os_erros_plantados(lote):
    _, _, erros = lote
    por_nota = erros.groupby("numero")["problema"].apply(" | ".join)
    assert "optante do Simples" in por_nota["105"]
    assert "Alíquota do ISS de 6,00%" in por_nota["107"]
    assert "Valor líquido (1.825,00)" in por_nota["109"]
    assert "Cancelada" in por_nota["110"]
    assert "CNPJ do emitente inválido" in por_nota["111"]
    sem_erro = {"100", "103", "104", "106", "108"}
    assert not (set(erros.loc[erros["gravidade"] == "Erro", "numero"]) & sem_erro)


def test_duplicidade_e_data_futura():
    n = mt.nota_vazia(tipo="NFS-e", numero="1", emitente_cnpj="11.222.333/0001-81", emissao="2026-12-25",
                      valor_bruto=100.0, codigo_servico="17.01.01", simples_nacional="1 - Não Optante")
    vistos = set()
    assert not any("duplicidade" in m for _, m in mt.erros_da_nota(n, HOJE, vistos))
    msgs = [m for _, m in mt.erros_da_nota(n, HOJE, vistos)]
    assert any("duplicidade" in m for m in msgs) and any("futuro" in m for m in msgs)


NFSE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<NFSe xmlns="http://www.sped.fazenda.gov.br/nfse" versao="1.00"><infNFSe Id="NFS35503082211222333000181000000000000126110000000001">
<xLocIncid>Sao Paulo</xLocIncid><xUFIncid>SP</xUFIncid><nNFSe>77</nNFSe><cStat>100</cStat>
<emit><CNPJ>11222333000181</CNPJ><xNome>Alfa Sistemas Ltda</xNome><enderNac><xMun>Sao Paulo</xMun><UF>SP</UF></enderNac></emit>
<valores><vBC>1000.00</vBC><pAliqAplic>2.00</pAliqAplic><vISSQN>20.00</vISSQN><vTotalRet>71.50</vTotalRet><vLiq>928.50</vLiq></valores>
<DPS><infDPS Id="DPS1"><dhEmi>2026-11-10T09:00:00-03:00</dhEmi><dCompet>2026-11-10</dCompet>
<prest><CNPJ>11222333000181</CNPJ><regTrib><opSimpNac>1</opSimpNac><regEspTrib>0</regEspTrib></regTrib></prest>
<toma><CNPJ>98765432000198</CNPJ><xNome>Cliente S.A.</xNome></toma>
<serv><cServ><cTribNac>010701</cTribNac><xDescServ>Suporte tecnico</xDescServ></cServ></serv>
<valores><vServPrest><vServ>1000.00</vServ></vServPrest><trib><tribMun><tribISSQN>1</tribISSQN><tpRetISSQN>2</tpRetISSQN></tribMun>
<tribFed><vRetIRRF>15.00</vRetIRRF><piscofins><vPis>6.50</vPis><vCofins>30.00</vCofins><tpRetPisCofins>1</tpRetPisCofins></piscofins></tribFed></trib></valores>
</infDPS></DPS></infNFSe></NFSe>"""


def test_le_xml_da_nfse_nacional():
    n = mt.ler_xml(NFSE_XML, "nota.xml")[0]
    assert n["numero"] == "77" and n["codigo_servico"] == "01.07.01" and n["retencao_iss"] == "Retido pelo Tomador"
    assert n["simples_nacional"] == "1 - Não Optante" and n["emissao"] == "2026-11-10"
    assert n["contrib_sociais_retidas"] == pytest.approx(36.5) and n["desc_contrib_sociais"] == "PIS/COFINS Retidos"
    trib = {t["tributo"]: t for t in mt.tributos_da_nota(n)}
    assert trib["PIS/COFINS/CSLL (CSRF)"]["codigo_recolhimento"] == "5979 + 5960"    # PIS e COFINS sem CSLL
    msgs = [m for _, m in mt.erros_da_nota(n, HOJE)]
    assert not any("Valor líquido" in m or "Total das retenções" in m for m in msgs), msgs


NFE_XML = b"""<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe"><NFe><infNFe Id="NFe35261111222333000181550010000012341000012345">
<ide><nNF>1234</nNF><natOp>Venda de mercadoria</natOp><dhEmi>2026-11-12T14:00:00-03:00</dhEmi></ide>
<emit><CNPJ>11222333000181</CNPJ><xNome>Loja Alfa</xNome><enderEmit><xMun>Sao Paulo</xMun><UF>SP</UF></enderEmit><CRT>1</CRT></emit>
<dest><CNPJ>98765432000198</CNPJ><xNome>Cliente</xNome><enderDest><UF>SP</UF></enderDest></dest>
<det><prod><CFOP>6102</CFOP></prod><imposto><ICMS><ICMS00><orig>0</orig><CST>00</CST></ICMS00></ICMS></imposto></det>
<total><ICMSTot><vBC>1000.00</vBC><vICMS>180.00</vICMS><vST>0</vST><vProd>1000.00</vProd><vFrete>50.00</vFrete>
<vSeg>0</vSeg><vDesc>0</vDesc><vIPI>0</vIPI><vPIS>0</vPIS><vCOFINS>0</vCOFINS><vOutro>0</vOutro><vNF>1000.00</vNF></ICMSTot></total>
</infNFe></NFe><protNFe><infProt><cStat>100</cStat></infProt></protNFe></nfeProc>"""


def test_le_xml_da_nfe_e_aponta_erros_de_venda():
    n = mt.ler_xml(NFE_XML, "venda.xml")[0]
    assert n["tipo"] == "NF-e" and n["cfop"] == "6102" and n["crt"] == "Simples Nacional" and n["cst_csosn"] == "000"
    assert "CFOP 6102" in mt.operacao(n) and "outro estado" in mt.operacao(n)
    msgs = " | ".join(m for _, m in mt.erros_da_nota(n, HOJE))
    assert "interestadual) para destinatário na mesma UF" in msgs
    assert "Simples Nacional usando CST" in msgs
    assert "Valor total da nota (1.000,00)" in msgs                     # faltou somar o frete


def test_le_texto_de_danfe():
    texto = ("DANFE\nDOCUMENTO AUXILIAR DA NOTA FISCAL ELETRÔNICA\nNº 000.001.234\nNATUREZA DA OPERAÇÃO\nVENDA DE MERCADORIA\n"
             "11.222.333/0001-81\nDESTINATÁRIO\n98.765.432/0001-98\nDATA DA EMISSÃO\n12/11/2026\n"
             "BASE DE CÁLCULO DO ICMS VALOR DO ICMS VALOR TOTAL DOS PRODUTOS\n1.000,00 180,00 1.000,00\n"
             "VALOR DO FRETE VALOR DO SEGURO DESCONTO OUTRAS DESPESAS VALOR DO IPI VALOR TOTAL DA NOTA\n"
             "50,00 0,00 0,00 0,00 0,00 1.050,00\nCOD PRODUTO 000 5102 UN 1\n")
    n = mt.ler_danfe(texto, "danfe.pdf")
    assert n["emitente_cnpj"] == "11.222.333/0001-81" and n["destinatario_cnpj"] == "98.765.432/0001-98"
    assert n["icms"] == 180 and n["valor_bruto"] == 1000 and n["frete"] == 50 and n["valor_liquido"] == 1050
    assert n["cfop"] == "5102" and n["cst_csosn"] == "000" and n["emissao"] == "2026-11-12"


def test_lote_com_zip_e_arquivo_ruim():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("notas/a.xml", NFSE_XML)
        z.writestr("notas/leia.txt", "ignorar")
    notas, problemas = mt.ler_lote([("lote.zip", buf.getvalue()), ("ruim.pdf", b"nao e pdf"),
                                    ("venda.xml", NFE_XML)], processos=1)
    assert sorted(notas["tipo"]) == ["NF-e", "NFS-e"] and len(problemas) == 1 and problemas[0].startswith("ruim.pdf")


def test_planilha_dinamica(lote):
    completas, tributos, erros = lote
    livro = load_workbook(io.BytesIO(mt.planilha(completas, tributos, erros)))
    assert livro.sheetnames[:3] == ["Leia-me", "Resumo", "Notas"] and {"Tributos", "Erros"} <= set(livro.sheetnames)
    assert set(livro["Tributos"].tables) == {"Tributos"} and livro["Notas"].auto_filter is not None
    cab = [c.value for c in livro["Tributos"][1]]
    assert {"Tributo", "Quem recolhe", "Código de recolhimento", "Valor"} <= set(cab)
    piv = mt.resumo_dinamico(tributos, ["tributo"], ["responsavel"])
    considerados = tributos.loc[tributos["considerar"] == "Sim", "valor"].sum()      # sem a nota cancelada
    assert piv.loc["Total", "Total"] == pytest.approx(considerados, abs=0.05)


def test_compara_enquadramento_com_a_receita(lote):
    completas, _, _ = lote
    cadastro = pd.DataFrame([{"cnpj": "12.345.678/0001-95", "opcao_receita": "Não optante", "porte": "DEMAIS",
                              "situacao_cadastral": "ATIVA"}])
    comp = mt.comparar_enquadramento(completas, cadastro)
    assert len(comp) == 3 and comp["diverge"].all()       # a nota diz Simples, a Receita diz não optante


def test_layout_do_danfse_real(lote):
    """Pontos vistos numa NFS-e real: 'NFS-e' em minúsculas, título do bloco abaixo dos rótulos, Simples sem o
    número e cortado, ISS do Simples como '-' e campos vazios impressos como '-'."""
    completas, _, erros = lote
    n = _nota(completas, 101)                              # prestador do Simples, ISS dentro do DAS
    assert n["emitente_cnpj"] == "12.345.678/0001-95" and n["destinatario_cnpj"] == "98.765.432/0001-98"
    assert n["numero"] == "101" and n["chave"] and n["emissao"] == "2026-11-04"
    assert n["enquadramento"] == "Simples Nacional - ME/EPP" and n["iss"] == 0 and n["aliquota_iss"] == 0
    assert n["codigo_municipal"] == "1701" and n["valor_bruto"] == 1700
    assert not ((erros["numero"] == "101") & (erros["gravidade"] == "Erro")).any()
    assert mt._codigo_por_texto("Optante - Microempresa ou Empresa de ...", mt.OPCAO_SIMPLES) == 3
    assert mt._codigo_por_texto("Regime de apuração dos tributos federais e municipal pelo SN",
                                mt.REGIME_APURACAO_SN) == 1


def test_nota_em_pdf_e_xml_nao_soma_duas_vezes():
    """Caso apontado por um usuário: a mesma nota em PDF e em XML aparecia em Erros, mas as duas cópias geravam
    linhas na aba Tributos (base da tabela dinâmica) e o tributo era somado duas vezes."""
    base = dict(tipo="NFS-e", numero="77", chave="3550308" + "1" * 43, emitente_cnpj="11.222.333/0001-81",
                emitente_nome="Alfa", emissao="2026-11-10", valor_bruto=1000.0, valor_liquido=985.0, irrf=15.0,
                total_retencoes=15.0, codigo_servico="17.01.01", simples_nacional="1 - Não Optante",
                tributacao_issqn="Operação Tributável", retencao_iss="Não Retido", situacao="NFS-e Regular")
    notas = pd.DataFrame([mt.nota_vazia(arquivo="nota77.pdf", pagina=1, **base),
                          mt.nota_vazia(arquivo="nota77.xml", pagina=1, **base),
                          mt.nota_vazia(arquivo="outra.pdf", pagina=1, **{**base, "numero": "78",
                                                                           "chave": "3550308" + "2" * 43})])
    completas, tributos, erros = mt.conciliar(notas, hoje=HOJE)
    marcas = completas.set_index("arquivo")[["duplicada", "considerar", "duplicada_de"]]
    assert marcas.loc["nota77.pdf"].tolist() == ["Sim", "Não", "nota77.xml"]      # o XML é a original
    assert marcas.loc["nota77.xml"].tolist()[:2] == ["Não", "Sim"]
    irrf = tributos[tributos["tributo"] == "IRRF"]
    assert set(irrf.loc[irrf["arquivo"] == "nota77.pdf", "considerar"]) == {"Não"}  # marcada na própria aba Tributos
    piv = mt.resumo_dinamico(tributos, ["tributo"])
    assert piv.loc["IRRF", "valor"] == 30.0                                         # 2 notas, não 3
    assert mt.resumo_dinamico(tributos, ["tributo"], so_consideradas=False).loc["IRRF", "valor"] == 45.0
    dup = erros[erros["problema"].str.contains("duplicidade")]
    assert dup["arquivo"].tolist() == ["nota77.pdf"] and "nota77.xml" in dup["problema"].iloc[0]
    livro = load_workbook(io.BytesIO(mt.planilha(completas, tributos, erros)))
    cab = [c.value for c in livro["Tributos"][1]]
    assert {"Considerar na apuração", "Duplicada"} <= set(cab)
    resumo = [r for r in livro["Resumo"].iter_rows(values_only=True) if r[0] == "IRRF"]
    assert resumo and 30.0 in resumo[0]                                            # o Resumo já sai sem duplicadas


def test_canceladas_ficam_fora_da_apuracao(lote):
    completas, tributos, _ = lote
    cancelada = _nota(completas, 110)
    assert cancelada["considerar"] == "Não" and cancelada["duplicada"] == "Não"
    assert set(tributos.loc[tributos["numero"] == "110", "considerar"]) == {"Não"}
