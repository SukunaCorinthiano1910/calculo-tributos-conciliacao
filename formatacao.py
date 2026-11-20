"""Formatação de números no padrão brasileiro."""


def brl(valor: float) -> str:
    """1234.5 → 'R$ 1.234,50' (negativos com sinal na frente)."""
    texto = f"{abs(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'-' if valor < 0 else ''}R$ {texto}"


def pct(valor: float, casas: int = 2, sinal: bool = False) -> str:
    """12.345 → '12,35%'."""
    return f"{valor:{'+' if sinal else ''}.{casas}f}%".replace(".", ",")
