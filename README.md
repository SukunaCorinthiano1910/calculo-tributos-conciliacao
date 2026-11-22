# Cálculo de Tributos e Conciliação Tributária

App em [Streamlit](https://streamlit.io) que lê notas fiscais em lote (PDF e XML) e faz a conciliação tributária:
quem recolhe cada tributo, com qual guia e código, os valores bruto e líquido, o enquadramento do emitente e os
erros de emissão. A saída é uma tabela dinâmica interativa e uma planilha Excel.

## O que ele faz

- **Lê em lote:** PDF do **DANFSe** (NFS-e do padrão nacional), PDF do **DANFE** (NF-e), **XML** de NFS-e nacional e
  de NF-e, arquivos `.zip` e pastas inteiras. A partir de 20 arquivos, a leitura roda em paralelo (cerca de 200 notas
  em 12 segundos).
- **Identifica a operação:** serviço pelo código de tributação nacional (item da LC 116/2003) ou venda pelo **CFOP**
  (interna, interestadual ou exportação).
- **Enquadramento do emitente:** não optante, Simples Nacional ME/EPP ou MEI, e o regime especial. Opcionalmente,
  compara com o cadastro público do CNPJ na Receita (via [BrasilAPI](https://brasilapi.com.br)).
- **Quem recolhe cada tributo, por qual guia e com qual código:**

  | Tributo | Quem recolhe | Guia / código |
  |---|---|---|
  | ISS retido | Tomador (ou intermediário) | Guia do município de incidência |
  | ISS próprio | Prestador | Guia municipal; **DAS** no Simples; **DAS-MEI** no MEI |
  | IRRF | Tomador | DARF **1708** (serviços) ou **8045** (comissões, propaganda) |
  | PIS/COFINS/CSLL retidos | Tomador | DARF **5952**, ou **5979** / **5960** / **5987** separados |
  | INSS retido | Tomador | **DCTFWeb** (EFD-Reinf R-2010) |
  | ICMS, ICMS-ST, IPI | Emitente | Guia estadual, **GNRE**, DARF **5123** |
  | IBS / CBS (2026) | — | Só destaque informativo (ano de teste da LC 214/2025) |

- **Aponta erros de emissão:** CNPJ/CPF inválido, valor líquido ou total de retenções que não fecha, ISS diferente de
  base × alíquota, alíquota de ISS fora de 2%–5%, retenção federal de optante do Simples, percentual errado de
  IRRF/CSRF/INSS, código de serviço inexistente, CFOP incompatível com a UF, CST usado por empresa do Simples, notas
  canceladas ou duplicadas e data de emissão no futuro.
- **Saída:** tabela dinâmica interativa no app (você escolhe linhas, colunas, valor e função) e planilha Excel com as
  abas *Resumo*, *Notas*, *Tributos*, *Erros* e *Leia-me*, todas com filtros. A aba *Tributos* já vem no formato para
  **Inserir → Tabela Dinâmica** no Excel.
- **Notas duplicadas não somam duas vezes:** quando a mesma nota chega mais de uma vez (por exemplo, em PDF e em
  XML), a cópia é marcada nas abas *Notas* e *Tributos* (colunas *Duplicada* e *Considerar na apuração*). Fica valendo
  o XML, que é mais confiável. O *Resumo*, os totais do app e a tabela dinâmica da tela já saem sem duplicadas,
  canceladas e substituídas; na tabela dinâmica do Excel, filtre *Considerar na apuração* = Sim.

## Como rodar

Precisa de Python 3.9 ou mais novo.

```bash
git clone https://github.com/SukunaCorinthiano1910/calculo-tributos-conciliacao.git
cd calculo-tributos-conciliacao
python -m venv .venv
source .venv/bin/activate        # no Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Para testar sem notas reais, clique em **🧪 Lote de exemplo**: o app gera 12 DANFSe fictícios no layout oficial,
alguns com erros de propósito.

## Como o PDF é lido

O DANFSe é lido pela **posição** dos campos: cada célula tem o rótulo em cima e o valor embaixo, numa grade de
4 colunas. O extrator procura o rótulo e lê o texto logo abaixo dele, até o rótulo seguinte da mesma linha. Os
rótulos e as tabelas de códigos (retenção do ISS, opção pelo Simples, regime especial, PIS/COFINS retidos) foram
copiados do gerador oficial da Receita Federal,
[calculadora-rtc-gerador-danfse](https://github.com/ReceitaFederal/calculadora-rtc-gerador-danfse), e ajustados com
uma NFS-e real (o PDF impresso tem diferenças em relação ao código, como "NFS-e" em minúsculas).

## Limitações

- **PDFs de prefeituras no layout antigo** (anteriores à NFS-e nacional) ainda não são lidos: cada cidade tem o seu.
- **DANFE em PDF** varia entre os programas emissores e é lido com menos precisão. Para notas de venda, prefira o XML.
- **PDF escaneado** (imagem) não tem texto e precisaria de OCR; esses arquivos aparecem na lista de não lidos.
- **Códigos de DARF** são sugeridos pela natureza mais comum do serviço: confira caso a caso.
- Não substitui a análise de um contador: use como apoio à conferência.

## Privacidade (LGPD)

Notas fiscais têm dados de clientes e fornecedores. O `.gitignore` já impede que PDFs, XMLs, ZIPs e planilhas sejam
enviados ao GitHub. A consulta opcional à Receita envia só o CNPJ dos emitentes, que é um dado público.

## Estrutura

| Arquivo | Conteúdo |
|---|---|
| `app.py` | Ponto de entrada do Streamlit |
| `aba_tributos.py` | Telas: envio das notas, métricas, tabela dinâmica, erros, enquadramento e planilha |
| `modulo_tributos.py` | Leitura de PDF/XML, regras tributárias, erros de emissão e planilha (sem Streamlit, dá para usar em scripts) |
| `pagina.py` | Evita a tradução automática do navegador, que deixa textos desatualizados na tela |
| `formatacao.py` | Números no padrão brasileiro (R$ 1.234,56) |
| `test_tributos.py` | Testes (notas geradas e XMLs sintéticos, sem internet) |

## Contribuindo

Ideias, correções e melhorias são bem-vindas. Ótimos pontos de partida:

- leitores para os layouts de NFS-e das prefeituras (São Paulo, Rio, BH…);
- OCR para PDFs escaneados;
- mais regras de conferência (alíquotas de ICMS por UF, retenções em pagamentos de órgãos públicos etc.);
- integração com as calculadoras de IBS/CBS da Receita
  ([regime regular](https://github.com/ReceitaFederal/calculadora-rtc-regime-regular) e
  [Simples Nacional](https://github.com/ReceitaFederal/calculadora-rtc-simples-nacional)), que são APIs HTTP.

Para contribuir:

1. Faça um *fork* e crie um branch para a sua mudança.
2. Rode os testes: `python -m pytest -q`.
3. Abra um *pull request* explicando o que mudou e por quê. Se for um leitor novo, inclua um PDF de exemplo
   **com dados fictícios**.

## Licença

[MIT](LICENSE): use, modifique e distribua à vontade, mantendo o aviso de autoria.
