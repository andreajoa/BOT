# -*- coding: utf-8 -*-
"""
Crypto Trader Bot - Mercado Lateral
Ponto de Entrada - Bot especializado em trading em mercados laterais.

ESTRATÉGIA: Mean Reversion (Reversão à Média)
- Comprar em suporte / banda inferior + RSI sobrevendido
- Vender em resistência / banda superior + RSI sobrecomprado
- Stop Loss estreito (0.5%)
- Take Profit curto (1.0%)
"""

import os
import sys

# Adicionar diretório raiz ao path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bot import LateralMarketBot
from utils.logger import Logger


def main():
    """Função principal."""
    print("\n" + "="*60)
    print("🤖 CRYPTO TRADER BOT - MERCADO LATERAL")
    print("="*60)
    print("\n📚 Estratégia: Mean Reversion")
    print("   • Comprar em banda inferior + RSI sobrevendido")
    print("   • Vender em banda superior + RSI sobrecomprado")
    print("   • Stop Loss: 0.5% (estreito)")
    print("   • Take Profit: 1.0% (curto)")
    print("   • Leverage: 2x (conservador)")
    print("\n⚠️  Uso em mercado de TENDÊNCIA não é recomendado!")
    print("="*60 + "\n")

    logger = Logger("logs")
    bot = LateralMarketBot(logger)
    bot.run()


if __name__ == "__main__":
    main()
