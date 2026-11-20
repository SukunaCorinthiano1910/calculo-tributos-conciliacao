"""Cálculo de Tributos e Conciliação Tributária: app Streamlit.

Rode com:  streamlit run app.py
"""
import streamlit as st

import aba_tributos
from pagina import bloquear_traducao_automatica, pagina_em_portugues_desde_o_inicio


def main():
    st.set_page_config(page_title="Cálculo de Tributos", page_icon="🧾", layout="wide")
    pagina_em_portugues_desde_o_inicio()
    bloquear_traducao_automatica()
    aba_tributos.mostrar()


if __name__ == "__main__":
    main()
