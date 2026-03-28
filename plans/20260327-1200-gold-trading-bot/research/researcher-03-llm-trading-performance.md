# LLM Trading Performance Research Report
## March 27, 2026

---

## Executive Summary

LLMs show **mixed profitability** in real-world trading. Major benchmarks (StockBench, LiveTradeBench) reveal traditional LLM capability scores do NOT predict trading success. Best performers achieve 2-14% returns with 11-14% max drawdown, but most fail to beat buy-and-hold baseline. Hallucination rates in financial data reach 2.1-13.8%. Reasoning models (o1, DeepSeek-R1) show no trading advantage over standard instruct models.

---

## Key Findings by Category

### 1. Benchmark Results (2024-2025)

**StockBench** (March-July 2025 data):
- Tests LLM agents on multi-month stock trading
- Evaluates: cumulative return, max drawdown, Sortino ratio
- Result: Most agents fail to outperform buy-and-hold

**LiveTradeBench** (August-October 2025, 50 days):
- 21 models tested: Claude, GPT, Gemini, DeepSeek, Llama, Qwen, Kimi
- **Best performers**: Kimi-K2, Qwen3-235B; ~2-3% cumulative returns, ~11-14% drawdown
- **Critical finding**: General LLM scores have **negligible correlation** with trading returns
- Reasoning fine-tuning (o1, R1) shows **no advantage** over instruct-tuned models

### 2. Model-Specific Performance

**Claude vs GPT-4o vs Gemini vs DeepSeek** (trading context):

| Model | Strength | Weakness |
|-------|----------|----------|
| **Claude 3.5** | Deep document analysis, fundamental research | Slower reasoning for real-time signals |
| **GPT-5/4o** | Data crunching, visualization, coding | Numeric hallucinations in price levels |
| **Gemini 2.5** | Real-time search integration | Higher trading losses in benchmarks |
| **DeepSeek V3.1** | 14%+ crypto returns (50 days) | Limited financial domain fine-tuning |

**Crypto Trading Arena (DeepSeek V3.1, Grok-4)**: 14%+ returns within 72 hours, but outlier; GPT-5 showed double-digit losses.

### 3. Financial Benchmarks (FinBen, XFinBench)

**o1-Preview** (best on FinBench):
- 67.3% accuracy on complex financial problems
- Still lags human experts significantly
- Reasoning models do NOT outperform standard models on financial tasks

**GPT-4-Turbo + Retrieval** (FinanceBench):
- 81% wrong or refusal rate on financial QA
- Highlights fundamental LLM limitations in financial accuracy

**Forecasting Task (Stock movement prediction)**:
- Smaller models (Llama-3.1-7b) often beat larger ones (Llama-3.1-70b)
- Model size ≠ trading accuracy
- Real-time sentiment + nuanced analysis critical

### 4. Hallucination Rates (Critical for Trading)

- **General hallucinations**: ~8.2% average (2026)
- **Financial data accuracy**: 2.1% hallucination (best) to 13.8% (average)
- **Complex reasoning tasks**: 5-20% false information rate
- **Price/numeric accuracy**: >15% error when analyzing provided statements

**Risk**: In trading, 5-10% price level hallucinations can trigger wrong signals, invalidating entire position.

### 5. Reasoning Models (o1, DeepSeek-R1)

**Performance on reasoning tasks**:
- DeepSeek-R1: ~79.8% on AIME (math), ~97.3% on MATH-500
- Comparable to OpenAI o1
- Strong on pure reasoning, weak on financial application

**Trading Impact**: No evidence reasoning superiority transfers to trading decisions. Trading requires pattern recognition + real-time sentiment, not pure math reasoning.

### 6. Real-World Gold/XAUUSD Strategies

**LLM-Based Approach** (Academic framework):
- Input: historical XAUUSD data + macro indicators (inflation, USD strength) + news sentiment (GPT-4)
- Processing: LSTM/GRU time-series models
- Output: real-time trading signals via MetaTrader 5
- Example result: AchillesV11 hybrid model → 184% net profit (1-month backtest)

**Limitations**: Single backtest ≠ consistent forward performance. Real gold market more volatile than backtests.

---

## Critical Insights

1. **Benchmark paradox**: High LLM scores (reasoning, math) ≠ high trading returns
2. **Hallucination risk**: 2-13% error rate in financial data is material for 50:1 leverage
3. **No reasoning advantage**: o1, DeepSeek-R1 underperform or match instruct models in trading
4. **Multi-agent edge**: TradingAgents (sentiment + fundamental + technical analysts) outperforms single-model approaches
5. **Real money gap**: Backtests show profitability; live trading data sparse (most projects simulated)

---

## Unresolved Questions

1. Which specific LLM models tested in StockBench/LiveTradeBench and their exact return percentages?
2. Do fine-tuned LLMs (finance-specific) outperform base models?
3. Why does Qwen3-235B outperform larger models in trading benchmarks?
4. Real-world live trading P&L for XAUUSD with LLMs (all found data is backtested)?

---

## Sources

- [StockBench: LLM Trading Benchmark](https://stockbench.github.io/)
- [StockBench Paper (arXiv)](https://arxiv.org/abs/2510.02209)
- [LiveTradeBench (alphaXiv)](https://www.alphaxiv.org/overview/2510.11695v1)
- [Frontiers: LLMs in Equity Markets](https://www.frontiersin.org/journals/artificial-intelligence/articles/10.3389/frai.2025.1608365/full)
- [ChatGPT vs Gemini vs Claude for Trading](https://daytradingtoolkit.com/comparisons/chatgpt-vs-gemini-vs-claude-trading/)
- [Crypto Trading Performance: DeepSeek vs GPT](https://bingx.com/en/learn/article/how-to-use-deepseek-ai-in-crypto-trading)
- [FinBench: Financial Problem Solving](https://openreview.net/forum?id=AeGrf1uY0p)
- [FinBen: Holistic Financial Benchmark](https://arxiv.org/abs/2402.12659)
- [XFinBench: Graduate-Level Financial Problems](https://arxiv.org/abs/2602.19073)
- [TradingAgents Framework](https://github.com/TauricResearch/TradingAgents)
- [FinMem: Memory-Enhanced Trading Agent](https://github.com/pipiku915/FinMem-LLM-StockTrading)
- [DeepSeek-R1 Paper](https://arxiv.org/abs/2501.12948)
- [DeepSeek-R1 vs o1 Comparison](https://www.prompthub.us/blog/deepseek-r-1-model-overview-and-how-it-ranks-against-openais-o1)
- [Hallucination Leaderboard (Vectara)](https://github.com/vectara/hallucination-leaderboard)
- [Ranked: AI Hallucination Rates by Model](https://www.visualcapitalist.com/sp/ter02-ranked-ai-hallucination-rates-by-model/)
- [XAUUSD ML+LLM Trading Guide](https://www.tradingview.com/chart/XAUUSD/veW9UcWK-Automate-Gold-Trading-with-Machine-Learning-and-LLMS-FULL-Guide/)
- [Sentiment + Time-Series Gold Trading Framework](https://www.sciencedirect.com/science/article/pii/S277266222500089X)
