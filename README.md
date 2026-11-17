# Goltrader (híbrido)

Plataforma de trading em Python: motor com 10 estratégias, backtest/walk-forward, autotrader, risco e kill switch,
integração Bybit (Testnet por padrão), painel Streamlit — em evolução para um **híbrido** com interface de fluxo de mercado
(estilo Market Pulse), camada multi-corretora (adapters) e deploy no notebook + VPS.

- Status e próximos passos: [`STATUS.md`](STATUS.md)
- Plano/arquitetura: [`docs/PLANO-HIBRIDO.md`](docs/PLANO-HIBRIDO.md)
- Engenharia reversa: [`docs/engenharia-reversa/`](docs/engenharia-reversa/)
- Licença: **AGPL-3.0** ([`LICENSE`](LICENSE), [`NOTICE.md`](NOTICE.md))

> Aviso: operar mercados envolve risco de perda. Testnet/paper por padrão; conta real exige confirmação explícita. Não é recomendação de investimento.

Configuração: copie `.env.example` para `.env` e preencha **só localmente** (o `.env` é ignorado pelo Git).
