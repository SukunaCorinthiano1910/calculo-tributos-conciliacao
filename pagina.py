"""Proteções contra a tradução automática do navegador em apps Streamlit em português.

O Streamlit declara a página em inglês; o Chrome oferece traduzir, e a tradução troca os textos por cópias que o
Streamlit não atualiza mais (o título de outra tela fica preso, números ficam desatualizados e às vezes a página
quebra com "NotFoundError: removeChild"). Chame as duas funções logo depois de st.set_page_config.
"""


def pagina_em_portugues_desde_o_inicio():
    """Faz a página do Streamlit já nascer em português e marcada para não traduzir.

    O index.html do Streamlit declara lang="en". O Chrome decide traduzir assim que a página carrega, antes do
    script de bloquear_traducao_automatica rodar; com a tradução ligada, ao trocar de seção o título da seção
    anterior ficava na tela (e antes a página quebrava com "removeChild"). Ajusta o index.html instalado: é
    idempotente e é refeito sozinho se o Streamlit for reinstalado ou atualizado.
    """
    from pathlib import Path

    import streamlit
    arquivo = Path(streamlit.__file__).parent / "static" / "index.html"
    try:
        html = arquivo.read_text(encoding="utf-8")
        if 'translate="no"' in html:
            return
        html = html.replace('<html lang="en">', '<html lang="pt-BR" translate="no" class="notranslate">', 1)
        html = html.replace("<head>", '<head>\n    <meta name="google" content="notranslate" />', 1)
        arquivo.write_text(html, encoding="utf-8")
    except OSError:
        pass  # sem permissão de escrita: fica só o bloqueio por script


def bloquear_traducao_automatica():
    """Marca a página como português e desliga a tradução do navegador.

    O Streamlit declara lang="en"; o Chrome então oferece traduzir, e a tradução troca os nós de
    texto da página, que deixam de ser atualizados a cada busca (título e legenda ficavam
    mostrando a ação anterior).
    """
    import streamlit.components.v1 as components
    components.html(
        """<script>
        const doc = window.parent.document;
        doc.documentElement.lang = "pt-BR";
        doc.documentElement.setAttribute("translate", "no");
        doc.documentElement.classList.add("notranslate");
        if (!doc.querySelector('meta[name="google"]')) {
            const m = doc.createElement("meta");
            m.name = "google"; m.content = "notranslate";
            doc.head.appendChild(m);
        }
        // Tradução do navegador e extensões (bloqueador de anúncios etc.) mexem no DOM por fora do React; ao trocar
        // de seção, o React tenta remover um nó que já saiu do lugar e a página quebra com "NotFoundError: Failed to
        // execute 'removeChild'". Correção conhecida (facebook/react#11538): ignorar a operação nesse caso.
        const P = window.parent.Node.prototype;
        if (!P.__notFoundProtegido) {
            const remover = P.removeChild, inserir = P.insertBefore;
            P.removeChild = function (filho) {
                if (filho.parentNode !== this) return filho;
                return remover.apply(this, arguments);
            };
            P.insertBefore = function (novo, referencia) {
                if (referencia && referencia.parentNode !== this) return inserir.call(this, novo, null);
                return inserir.apply(this, arguments);
            };
            P.__notFoundProtegido = true;
        }
        // Com a tradução do navegador ligada, os textos na tela deixam de ser atualizados (ex.: o título de outra
        // seção fica preso). O Google Tradutor marca a página com a classe translated-ltr/rtl: avisa no topo.
        // A função mora na página principal: o iframe deste script é recriado a cada atualização do app.
        if (!window.parent.__avisarTraducao) {
            window.parent.__avisarTraducao = new window.parent.Function(`
                const traduzida = /translated-(ltr|rtl)/.test(document.documentElement.className) ||
                                  document.querySelector('font[style*="vertical-align: inherit"]');
                let aviso = document.getElementById("aviso-traducao");
                if (traduzida && !aviso) {
                    aviso = document.createElement("div");
                    aviso.id = "aviso-traducao";
                    aviso.setAttribute("translate", "no");
                    aviso.className = "notranslate";
                    aviso.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:999999;background:#E5487A;" +
                        "color:#fff;padding:10px 16px;font:600 14px Inter,sans-serif;text-align:center";
                    aviso.textContent = "⚠️ A tradução automática do navegador está ligada e impede o app de " +
                        "atualizar os textos (títulos de outras seções ficam presos na tela). Clique no ícone de " +
                        "tradução na barra de endereço → Mostrar original, e escolha Nunca traduzir este site.";
                    document.body.appendChild(aviso);
                } else if (!traduzida && aviso) {
                    aviso.remove();
                }`);
            new window.parent.MutationObserver(window.parent.__avisarTraducao)
                .observe(doc.documentElement, {attributes: true, attributeFilter: ["class"]});
        }
        window.parent.__avisarTraducao();
        </script>""",
        height=0,
    )
