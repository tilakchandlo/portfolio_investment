from utils.get_data import YahooData
import pandas as pd
import numpy as np
import datetime
from config import basedir
import matplotlib.pyplot as plt

plt.style.use('ggplot')

stocks = pd.read_excel(f'{basedir}/nyse_tickers.xlsx')
stock_list = stocks['Symbol'].tolist()


class CrossSectionalMomentum:
    """
    Defines a Cross Sectional Momentum strategy.
    The strategy uses weekly returns, automatically computed by feeding prices in.

    Weight = (rank - average of ranks) / n° of securities


    Parameters
    ----------
    prices: Pandas Time Series dataframe of prices
    benchmark. Pandas Time Series dataframe of prices. Benchmark used to compute Information Ratio

    Returns
    -------
    time_series_mom: returns a Pd Series of portfolio returns
    cumulative_ret: returns the cumulative returns and the cumulative wealth (eg capital * cumulative returns)
    sharpe_ratio: returns the strategy Sharpe Ratio over the whole back-testing period
    information_ratio: returns the strategy Information Ratio over the whole back-testing period

    """

    def __init__(self, prices, benchmark, capital=100, risk_free=0):
        self.prices = prices
        self.benchmark = benchmark
        self.capital = capital
        self.risk_free = risk_free
        self.returns = self.prices.pct_change().resample('W').last()
        self.port_ret = self.cross_sec_mom()

    def cross_sec_mom(self):
        if isinstance(self.returns, pd.DataFrame):
            rank = pd.DataFrame(columns = self.returns.columns)
            weights = pd.DataFrame(columns = self.returns.columns)
            mean_ret = self.returns.rolling(window = 48).sum()[: -4]
            for idx, row in mean_ret.iterrows():
                tmp_rank = row.rank(ascending = False)
                rank.loc[len(rank)] = tmp_rank
                weights.loc[len(weights)] = (tmp_rank - tmp_rank.mean()) / len(tmp_rank)
            rank.index = self.returns.index[4:]
            weights.index = self.returns.index[4:]

            port_ret = (weights.shift(1) * self.returns).dropna().sum(axis = 1).to_frame()
            port_ret.colums = ['Portfolio Returns']

            return port_ret

    def cumulative(self):
        cum = self.port_ret.cumsum()
        cum_wealth = (cum + 1) * self.capital

        return cum, cum_wealth

    def sharpe_ratio(self):
        stdev = self.port_ret.std()
        ann_sharpe = (self.port_ret.mean() - self.risk_free) / stdev * np.sqrt(52)

        return ann_sharpe.to_numpy()

    def get_benchmark_ret(self):
        benchmark_ret = self.benchmark.pct_change().dropna()

        return benchmark_ret

    def information_ratio(self):
        inf_ratio = (self.port_ret.mean().values -
                     self.get_benchmark_ret().mean().values) / self.port_ret.std() * np.sqrt(52)
        return inf_ratio.to_numpy()


if __name__ == "__main__":
    ticker = ['GE', 'IBM', 'GOOG']
    # ticker = stock_list[80: 100]
    start = datetime.datetime(2006, 1, 1)
    end = datetime.datetime(2020, 1, 1)
    series = 'Adj Close'
    dataframe = YahooData(ticker, start, end, series).get_series()
    dataframe.dropna(axis = 'columns', inplace = True)
    benchmark = YahooData(['SPY'], start, end, series).get_series()
    cs_mom = CrossSectionalMomentum(dataframe, benchmark)
    port_ret = cs_mom.cross_sec_mom()
    cum_ret, cum_wealth = cs_mom.cumulative()
    sharpe = cs_mom.sharpe_ratio()
    info_ratio = cs_mom.information_ratio()

    plt.plot(cum_wealth, label = 'strategy_backtest')
    plt.title('Cross Sectional Momentum: Cumulative Wealth')
    plt.legend()
    plt.grid()
    plt.xticks(rotation = 45)
    plt.show()