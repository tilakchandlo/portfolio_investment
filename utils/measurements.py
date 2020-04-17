from __future__ import division
import os

from functools import wraps
import math
import pandas as pd
import numpy as np
from math import pow
from scipy import stats, optimize
from six import iteritems
from sys import float_info
from numpy.lib.stride_tricks import as_strided
import logging

logger = logging.getLogger(__name__)
logging.basicConfig(level=os.environ.get("LOGLEVEL", "INFO"))

try:
    import bottleneck as bn

    def _wrap_function(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            out = kwargs.pop('out', None)
            data = f(*args, **kwargs)
            if out is None:
                out = data
            else:
                out[()] = data

            return out

        return wrapped

    nanmean = _wrap_function(bn.nanmean)
    nanstd = _wrap_function(bn.nanstd)
    nansum = _wrap_function(bn.nansum)
    nanmax = _wrap_function(bn.nanmax)
    nanmin = _wrap_function(bn.nanmin)
    nanargmax = _wrap_function(bn.nanargmax)
    nanargmin = _wrap_function(bn.nanargmin)

except ImportError:
    nanmean = np.nanmean
    nanstd = np.nanstd
    nansum = np.nansum
    nanmax = np.nanmax
    nanmin = np.nanmin
    nanargmax = np.nanargmax
    nanargmin = np.nanargmin

APPROX_BDAYS_PER_MONTH = 21
APPROX_BDAYS_PER_YEAR = 262

MONTHS_PER_YEAR = 12
WEEKS_PER_YEAR = 52
QTRS_PER_YEAR = 4

DAILY = 'daily'
WEEKLY = 'weekly'
MONTHLY = 'monthly'
QUARTERLY = 'quarterly'
YEARLY = 'yearly'

ANNUALIZATION_FACTORS = {
    DAILY: APPROX_BDAYS_PER_YEAR,
    WEEKLY: WEEKS_PER_YEAR,
    MONTHLY: MONTHS_PER_YEAR,
    QUARTERLY: QTRS_PER_YEAR,
    YEARLY: 1
}


def roll(*args, **kwargs):
    """
    Calculates a given statistic across a rolling time period.
    Parameters
    ----------
    returns : pd.Series or np.ndarray
        Daily returns of the portfolio, noncumulative.
    factor_returns (optional): float / series
        Benchmark return to compare returns against.
    function:
        the function to run for each rolling window.
    window (keyword): int
        the number of periods included in each calculation.
    (other keywords): other keywords that are required to be passed to the
        function in the 'function' argument may also be passed in.
    Returns
    -------
    np.ndarray, pd.Series
        depends on input type
        ndarray(s) ==> ndarray
        Series(s) ==> pd.Series
        A Series or ndarray of the results of the stat across the rolling
        window.
    """
    func = kwargs.pop('function')
    window = kwargs.pop('window')
    if len(args) > 2:
        raise ValueError("Cannot pass more than 2 return sets")

    if len(args) == 2:
        if not isinstance(args[0], type(args[1])):
            raise ValueError("The two returns arguments are not the same.")

    if isinstance(args[0], np.ndarray):
        return _roll_ndarray(func, window, *args, **kwargs)
    return _roll_pandas(func, window, *args, **kwargs)


def up(returns, factor_returns, **kwargs):
    """
    Calculates a given statistic filtering only positive factor return periods.
    Parameters
    ----------
    returns : pd.Series or np.ndarray
        Daily returns of the portfolio, noncumulative.
    factor_returns (optional): float / series
        Benchmark return to compare returns against.
    function:
        the function to run for each rolling window.
    (other keywords): other keywords that are required to be passed to the
        function in the 'function' argument may also be passed in.
    Returns
    -------
    Same as the return of the function
    """
    func = kwargs.pop('function')
    returns = returns[factor_returns > 0]
    factor_returns = factor_returns[factor_returns > 0]
    return func(returns, factor_returns, **kwargs)


def down(returns, factor_returns, **kwargs):
    """
    Calculates a given statistic filtering only negative factor return periods.
    Parameters
    ----------
    returns : pd.Series or np.ndarray
        Daily returns of the portfolio, noncumulative.
    factor_returns (optional): float / series
        Benchmark return to compare returns against.
    function:
        the function to run for each rolling window.
    (other keywords): other keywords that are required to be passed to the
        function in the 'function' argument may also be passed in.
    Returns
    -------
    Same as the return of the 'function'
    """
    func = kwargs.pop('function')
    returns = returns[factor_returns < 0]
    factor_returns = factor_returns[factor_returns < 0]
    return func(returns, factor_returns, **kwargs)


def _roll_ndarray(func, window, *args, **kwargs):
    data = []
    for i in range(window, len(args[0]) + 1):
        rets = [s[i - window:i] for s in args]
        data.append(func(*rets, **kwargs))
    return np.array(data)


def _roll_pandas(func, window, *args, **kwargs):
    data = {}
    index_values = []
    for i in range(window, len(args[0]) + 1):
        rets = [s.iloc[i - window:i] for s in args]
        index_value = args[0].index[i - 1]
        index_values.append(index_value)
        data[index_value] = func(*rets, **kwargs)
    return pd.Series(data, index=type(args[0].index)(index_values))


def rolling_window(array, length, mutable=False):
    """
    Restride an array of shape
        (X_0, ... X_N)
    into an array of shape
        (length, X_0 - length + 1, ... X_N)
    where each slice at index i along the first axis is equivalent to
        result[i] = array[length * i:length * (i + 1)]
    Parameters
    ----------
    array : np.ndarray
        The base array.
    length : int
        Length of the synthetic first axis to generate.
    mutable : bool, optional
        Return a mutable array? The returned array shares the same memory as
        the input array. This means that writes into the returned array affect
        ``array``. The returned array also uses strides to map the same values
        to multiple indices. Writes to a single index may appear to change many
        values in the returned array.
    Returns
    -------
    out : np.ndarray

    """
    if not length:
        raise ValueError("Can't have 0-length window")

    orig_shape = array.shape
    if not orig_shape:
        raise IndexError("Can't restride a scalar.")
    elif orig_shape[0] < length:
        raise IndexError(
            "Can't restride array of shape {shape} with"
            " a window length of {len}".format(
                shape=orig_shape,
                len=length,
            )
        )

    num_windows = (orig_shape[0] - length + 1)
    new_shape = (num_windows, length) + orig_shape[1:]

    new_strides = (array.strides[0],) + array.strides

    out = as_strided(array, new_shape, new_strides)
    out.setflags(write=mutable)
    return out


def _create_unary_vectorized_roll_function(function):
    def unary_vectorized_roll(arr, window, out=None, **kwargs):
        """
        Computes the {human_readable} measure over a rolling window.
        Parameters
        ----------
        arr : array-like
            The array to compute the rolling {human_readable} over.
        window : int
            Size of the rolling window in terms of the periodicity of the data.
        out : array-like, optional
            Array to use as output buffer.
            If not passed, a new array will be created.
        **kwargs
        Returns
        -------
        rolling_{name} : array-like
            The rolling {human_readable}.
        """
        allocated_output = out is None

        if len(arr):
            out = function(
                rolling_window(_flatten(arr), min(len(arr), window)).T,
                out=out,
                **kwargs
            )
        else:
            out = np.empty(0, dtype='float64')

        if allocated_output and isinstance(arr, pd.Series):
            out = pd.Series(out, index=arr.index[-len(out):])

        return out

    unary_vectorized_roll.__doc__ = unary_vectorized_roll.__doc__.format(
        name=function.__name__,
        human_readable=function.__name__.replace('_', ' '),
    )

    return unary_vectorized_roll


def _create_binary_vectorized_roll_function(function):
    def binary_vectorized_roll(lhs, rhs, window, out=None, **kwargs):
        """
        Computes the {human_readable} measure over a rolling window.
        Parameters
        ----------
        lhs : array-like
            The first array to pass to the rolling {human_readable}.
        rhs : array-like
            The second array to pass to the rolling {human_readable}.
        window : int
            Size of the rolling window in terms of the periodicity of the data.
        out : array-like, optional
            Array to use as output buffer.
            If not passed, a new array will be created.
        **kwargs

        Returns
        -------
        rolling_{name} : array-like
            The rolling {human_readable}.
        """
        allocated_output = out is None

        if window >= 1 and len(lhs) and len(rhs):
            out = function(
                rolling_window(_flatten(lhs), min(len(lhs), window)).T,
                rolling_window(_flatten(rhs), min(len(rhs), window)).T,
                out=out,
                **kwargs
            )
        elif allocated_output:
            out = np.empty(0, dtype='float64')
        else:
            out[()] = np.nan

        if allocated_output:
            if out.ndim == 1 and isinstance(lhs, pd.Series):
                out = pd.Series(out, index=lhs.index[-len(out):])
            elif out.ndim == 2 and isinstance(lhs, pd.Series):
                out = pd.DataFrame(out, index=lhs.index[-len(out):])
        return out

    binary_vectorized_roll.__doc__ = binary_vectorized_roll.__doc__.format(
        name=function.__name__,
        human_readable=function.__name__.replace('_', ' '),
    )

    return binary_vectorized_roll


def _flatten(arr):
    return arr if not isinstance(arr, pd.Series) else arr.values


def _adjust_returns(returns, adjustment_factor):
    """
    Returns the returns series adjusted by adjustment_factor. Optimizes for the
    case of adjustment_factor being 0 by returning returns itself, not a copy!
    Parameters
    ----------
    returns : pd.Series or np.ndarray
    adjustment_factor : pd.Series or np.ndarray or float or int
    Returns
    -------
    adjusted_returns : array-like
    """
    if isinstance(adjustment_factor, (float, int)) and adjustment_factor == 0:
        return returns
    return returns - adjustment_factor


def annualization_factor(period, annualization):
    """
    Return annualization factor from period entered or if a custom
    value is passed in.
    Parameters
    ----------
    period : str, optional
        Defines the periodicity of the 'returns' data for purposes of
        annualizing. Value ignored if `annualization` parameter is specified.
        Defaults are::
            'monthly':12
            'weekly': 52
            'daily': 252
    annualization : int, optional
        Used to suppress default values available in `period` to convert
        returns into annual returns. Value should be the annual frequency of
        `returns`.
    Returns
    -------
    annualization_factor : float
    """
    if annualization is None:
        try:
            factor = ANNUALIZATION_FACTORS[period]
        except KeyError:
            raise ValueError(
                "Period cannot be '{}'. "
                "Can be '{}'.".format(
                    period, "', '".join(ANNUALIZATION_FACTORS.keys())
                )
            )
    else:
        factor = annualization
    return factor


def simple_returns(prices):
    """
    Compute simple returns from a timeseries of prices.
    Parameters
    ----------
    prices : pd.Series, pd.DataFrame or np.ndarray
        Prices of assets in wide-format, with assets as columns,
        and indexed by datetimes.
    Returns
    -------
    returns : array-like
        Returns of assets in wide-format, with assets as columns,
        and index coerced to be tz-aware.
    """
    if isinstance(prices, (pd.DataFrame, pd.Series)):
        out = prices.pct_change().iloc[1:]
    else:
        out = np.diff(prices, axis=0)
        np.divide(out, prices[:-1], out=out)

    return out


def cum_returns(returns, starting_value=0, out=None):
    """
    Compute cumulative returns from simple returns.
    Parameters
    ----------
    returns : pd.Series, np.ndarray, or pd.DataFrame
        Returns of the portfolio as a percentage, noncumulative.
         - Time series with decimal returns.
         - Example::
            2015-07-16   -0.012143
            2015-07-17    0.045350
            2015-07-20    0.030957
            2015-07-21    0.004902
         - Also accepts two dimensional data. In this case, each column is
           cumulated.
    starting_value : float, optional
       The starting returns.
    out : array-like, optional
        Array to use as output buffer.
        If not passed, a new array will be created.
    Returns
    -------
    cumulative_returns : array-like
        Series of cumulative returns.
    """
    if len(returns) < 1:
        return returns.copy()

    nanmask = np.isnan(returns)
    if np.any(nanmask):
        returns = returns.copy()
        returns[nanmask] = 0

    allocated_output = out is None
    if allocated_output:
        out = np.empty_like(returns)

    np.add(returns, 1, out=out)
    out.cumprod(axis=0, out=out)

    if starting_value == 0:
        np.subtract(out, 1, out=out)
    else:
        np.multiply(out, starting_value, out=out)

    if allocated_output:
        if returns.ndim == 1 and isinstance(returns, pd.Series):
            out = pd.Series(out, index=returns.index)
        elif isinstance(returns, pd.DataFrame):
            out = pd.DataFrame(
                out, index=returns.index, columns=returns.columns,
            )

    return out


def cum_returns_final(returns, starting_value=0):
    """
    Compute total returns from simple returns.
    Parameters
    ----------
    returns : pd.DataFrame, pd.Series, or np.ndarray
       Noncumulative simple returns of one or more timeseries.
    starting_value : float, optional
       The starting returns.
    Returns
    -------
    total_returns : pd.Series, np.ndarray, or float
        If input is 1-dimensional (a Series or 1D numpy array), the result is a
        scalar.
        If input is 2-dimensional (a DataFrame or 2D numpy array), the result
        is a 1D array containing cumulative returns for each column of input.
    """
    if len(returns) == 0:
        return np.nan

    if isinstance(returns, pd.DataFrame):
        result = (returns + 1).prod()
    else:
        result = np.nanprod(returns + 1, axis=0)

    if starting_value == 0:
        result -= 1
    else:
        result *= starting_value

    return result


def aggregate_returns(returns, convert_to):
    """
    Aggregates returns by week, month, or year.
    Parameters
    ----------
    returns : pd.Series
       Daily returns of the portfolio, noncumulative.
    convert_to : str
        Can be 'weekly', 'monthly', or 'yearly'.
    Returns
    -------
    aggregated_returns : pd.Series
    """

    def cumulate_returns(x):
        return cum_returns(x).iloc[-1]

    if convert_to == WEEKLY:
        grouping = [lambda x: x.year, lambda x: x.isocalendar()[1]]
    elif convert_to == MONTHLY:
        grouping = [lambda x: x.year, lambda x: x.month]
    elif convert_to == QUARTERLY:
        grouping = [lambda x: x.year, lambda x: int(math.ceil(x.month / 3.))]
    elif convert_to == YEARLY:
        grouping = [lambda x: x.year]
    else:
        raise ValueError(
            'convert_to must be {}, {} or {}'.format(WEEKLY, MONTHLY, YEARLY)
        )

    return returns.groupby(grouping).apply(cumulate_returns)


def max_drawdown(returns, out=None):
    """
    Determines the maximum drawdown of a portfolio.
    Parameters
    ----------
    returns : pd.Series or np.ndarray
        Daily returns of the portfolio, noncumulative.
    out : array-like, optional
        Array to use as output buffer.
        If not passed, a new array will be created.
    Returns
    -------
    max_drawdown : float
    """
    allocated_output = out is None
    if allocated_output:
        out = np.empty(returns.shape[1:])

    returns_1d = returns.ndim == 1

    if len(returns) < 1:
        out[()] = np.nan
        if returns_1d:
            out = out.item()
        return out

    returns_array = np.asanyarray(returns)

    cumulative = np.empty(
        (returns.shape[0] + 1,) + returns.shape[1:],
        dtype='float64',
    )
    cumulative[0] = start = 100
    cum_returns(returns_array, starting_value=start, out=cumulative[1:])

    max_return = np.fmax.accumulate(cumulative, axis=0)

    nanmin((cumulative - max_return) / max_return, axis=0, out=out)
    if returns_1d:
        out = out.item()
    elif allocated_output and isinstance(returns, pd.DataFrame):
        out = pd.Series(out)

    return out


roll_max_drawdown = _create_unary_vectorized_roll_function(max_drawdown)


def annual_return(returns, period=DAILY, annualization=None):
    """
    Determines the mean annual growth rate of returns. This is equivilent
    to the compound annual growth rate.
    Parameters
    ----------
    returns : pd.Series or np.ndarray
        Periodic returns of the portfolio, noncumulative.
    period : str, optional
        Defines the periodicity of the 'returns' data for purposes of
        annualizing. Value ignored if `annualization` parameter is specified.
        Defaults are::
            'monthly':12
            'weekly': 52
            'daily': 252
    annualization : int, optional
        Used to suppress default values available in `period` to convert
        returns into annual returns. Value should be the annual frequency of
        `returns`.
    Returns
    -------
    annual_return : float
        Annual Return as CAGR (Compounded Annual Growth Rate).
    """

    if len(returns) < 1:
        return np.nan

    ann_factor = annualization_factor(period, annualization)
    num_years = len(returns) / ann_factor
    ending_value = cum_returns_final(returns, starting_value=1)

    return ending_value ** (1 / num_years) - 1


def cagr(returns, period=DAILY, annualization=None):
    """
    Compute compound annual growth rate. Alias function for
    Parameters
    ----------
    returns : pd.Series or np.ndarray
        Daily returns of the portfolio, noncumulative.
    period : str, optional
        Defines the periodicity of the 'returns' data for purposes of
        annualizing. Value ignored if `annualization` parameter is specified.
        Defaults are::
            'monthly':12
            'weekly': 52
            'daily': 252
    annualization : int, optional
        Used to suppress default values available in `period` to convert
        returns into annual returns. Value should be the annual frequency of
        `returns`.
    Returns
    -------
    cagr : float
        The CAGR value.
    """
    return annual_return(returns, period, annualization)


roll_cagr = _create_unary_vectorized_roll_function(cagr)


def annual_volatility(returns,
                      period=DAILY,
                      alpha=2.0,
                      annualization=None,
                      out=None):
    """
    Determines the annual volatility of a portfolio.
    Parameters
    ----------
    returns : pd.Series or np.ndarray
        Periodic returns of the portfolio, noncumulative.
    period : str, optional
        Defines the periodicity of the 'returns' data for purposes of
        annualizing. Value ignored if `annualization` parameter is specified.
        Defaults are::
            'monthly':12
            'weekly': 52
            'daily': 252
    alpha : float, optional
        Scaling relation (Levy stability exponent).
    annualization : int, optional
        Used to suppress default values available in `period` to convert
        returns into annual returns. Value should be the annual frequency of
        `returns`.
    out : array-like, optional
        Array to use as output buffer.
        If not passed, a new array will be created.
    Returns
    -------
    annual_volatility : float
    """
    allocated_output = out is None
    if allocated_output:
        out = np.empty(returns.shape[1:])

    returns_1d = returns.ndim == 1

    if len(returns) < 2:
        out[()] = np.nan
        if returns_1d:
            out = out.item()
        return out

    ann_factor = annualization_factor(period, annualization)
    nanstd(returns, ddof=1, axis=0, out=out)
    out = np.multiply(out, ann_factor ** (1.0 / alpha), out=out)
    if returns_1d:
        out = out.item()
    return out


roll_annual_volatility = _create_unary_vectorized_roll_function(
    annual_volatility,)


def calmar_ratio(returns, period=DAILY, annualization=None):
    """
    Determines the Calmar ratio, or drawdown ratio, of a portfolio.
    Parameters
    ----------
    returns : pd.Series or np.ndarray
        Daily returns of the portfolio, noncumulative.
    period : str, optional
        Defines the periodicity of the 'returns' data for purposes of
        annualizing. Value ignored if `annualization` parameter is specified.
        Defaults are::
            'monthly':12
            'weekly': 52
            'daily': 252
    annualization : int, optional
        Used to suppress default values available in `period` to convert
        returns into annual returns. Value should be the annual frequency of
        `returns`.
    Returns
    -------
    calmar_ratio : float
        Calmar ratio (drawdown ratio) as float. Returns np.nan if there is no
        calmar ratio.
    """

    max_dd = max_drawdown(returns=returns)
    if max_dd < 0:
        temp = annual_return(
            returns=returns,
            period=period,
            annualization=annualization
        ) / abs(max_dd)
    else:
        return np.nan

    if np.isinf(temp):
        return np.nan

    return temp


def omega_ratio(returns, risk_free=0.0, required_return=0.0,
                annualization=APPROX_BDAYS_PER_YEAR):
    """Determines the Omega ratio of a portfolio.
    Parameters
    ----------
    returns : pd.Series or np.ndarray
        Daily returns of the portfolio, noncumulative.
    risk_free : int, float
        Constant risk-free return throughout the period
    required_return : float, optional
        Minimum acceptance return of the investor. Threshold over which to
        consider positive vs negative returns. It will be converted to a
        value appropriate for the period of the returns. E.g. An annual minimum
        acceptable return of 100 will translate to a minimum acceptable
        return of 0.018.
    annualization : int, optional
        Factor used to convert the required_return into a daily
        value. Enter 1 if no time period conversion is necessary.
    Returns
    -------
    omega_ratio : float
    """

    if len(returns) < 2:
        return np.nan

    if annualization == 1:
        return_threshold = required_return
    elif required_return <= -1:
        return np.nan
    else:
        return_threshold = (1 + required_return) ** \
                           (1. / annualization) - 1

    returns_less_thresh = returns - risk_free - return_threshold

    numer = sum(returns_less_thresh[returns_less_thresh > 0.0])
    denom = -1.0 * sum(returns_less_thresh[returns_less_thresh < 0.0])

    if denom > 0.0:
        return numer / denom
    else:
        return np.nan


def sharpe_ratio(returns,
                 risk_free=0,
                 period=DAILY,
                 annualization=None,
                 out=None):
    """
    Determines the Sharpe ratio of a portfolio.
    Parameters
    ----------
    returns : pd.Series or np.ndarray
        Daily returns of the portfolio, noncumulative.
    risk_free : int, float
        Constant daily risk-free return throughout the period.
    period : str, optional
        Defines the periodicity of the 'returns' data for purposes of
        annualizing. Value ignored if `annualization` parameter is specified.
        Defaults are::
            'monthly':12
            'weekly': 52
            'daily': 252
    annualization : int, optional
        Used to suppress default values available in `period` to convert
        returns into annual returns. Value should be the annual frequency of
        `returns`.
    out : array-like, optional
        Array to use as output buffer.
        If not passed, a new array will be created.
    Returns
    -------
    sharpe_ratio : float
        nan if insufficient length of returns or if if adjusted returns are 0.

    """
    allocated_output = out is None
    if allocated_output:
        out = np.empty(returns.shape[1:])

    return_1d = returns.ndim == 1

    if len(returns) < 2:
        out[()] = np.nan
        if return_1d:
            out = out.item()
        return out

    returns_risk_adj = np.asanyarray(_adjust_returns(returns, risk_free))
    ann_factor = annualization_factor(period, annualization)

    np.multiply(
        np.divide(
            nanmean(returns_risk_adj, axis=0),
            nanstd(returns_risk_adj, ddof=1, axis=0),
            out=out,
        ),
        np.sqrt(ann_factor),
        out=out,
    )
    if return_1d:
        out = out.item()

    return out


roll_sharpe_ratio = _create_unary_vectorized_roll_function(sharpe_ratio)


def sortino_ratio(returns,
                  required_return=0,
                  period=DAILY,
                  annualization=None,
                  out=None,
                  _downside_risk=None):
    """
    Determines the Sortino ratio of a portfolio.
    Parameters
    ----------
    returns : pd.Series or np.ndarray or pd.DataFrame
        Daily returns of the portfolio, noncumulative.
    required_return: float / series
        minimum acceptable return
    period : str, optional
        Defines the periodicity of the 'returns' data for purposes of
        annualizing. Value ignored if `annualization` parameter is specified.
        Defaults are::
            'monthly':12
            'weekly': 52
            'daily': 252
    annualization : int, optional
        Used to suppress default values available in `period` to convert
        returns into annual returns. Value should be the annual frequency of
        `returns`.
    _downside_risk : float, optional
        The downside risk of the given inputs, if known. Will be calculated if
        not provided.
    out : array-like, optional
        Array to use as output buffer.
        If not passed, a new array will be created.
    Returns
    -------
    sortino_ratio : float or pd.Series
        depends on input type
        series ==> float
        DataFrame ==> pd.Series

    """
    allocated_output = out is None
    if allocated_output:
        out = np.empty(returns.shape[1:])

    return_1d = returns.ndim == 1

    if len(returns) < 2:
        out[()] = np.nan
        if return_1d:
            out = out.item()
        return out

    adj_returns = np.asanyarray(_adjust_returns(returns, required_return))

    ann_factor = annualization_factor(period, annualization)

    average_annual_return = nanmean(adj_returns, axis=0) * ann_factor
    annualized_downside_risk = (
        _downside_risk
        if _downside_risk is not None else
        downside_risk(returns, required_return, period, annualization)
    )
    np.divide(average_annual_return, annualized_downside_risk, out=out)
    if return_1d:
        out = out.item()
    elif isinstance(returns, pd.DataFrame):
        out = pd.Series(out)

    return out


roll_sortino_ratio = _create_unary_vectorized_roll_function(sortino_ratio)


def downside_risk(returns,
                  required_return=0,
                  period=DAILY,
                  annualization=None,
                  out=None):
    """
    Determines the downside deviation below a threshold
    Parameters
    ----------
    returns : pd.Series or np.ndarray or pd.DataFrame
        Daily returns of the portfolio, noncumulative.
    required_return: float / series
        minimum acceptable return
    period : str, optional
        Defines the periodicity of the 'returns' data for purposes of
        annualizing. Value ignored if `annualization` parameter is specified.
        Defaults are::
            'monthly':12
            'weekly': 52
            'daily': 252
    annualization : int, optional
        Used to suppress default values available in `period` to convert
        returns into annual returns. Value should be the annual frequency of
        `returns`.
    out : array-like, optional
        Array to use as output buffer.
        If not passed, a new array will be created.
    Returns
    -------
    downside_deviation : float or pd.Series
        depends on input type
        series ==> float
        DataFrame ==> pd.Series
    """
    allocated_output = out is None
    if allocated_output:
        out = np.empty(returns.shape[1:])

    returns_1d = returns.ndim == 1

    if len(returns) < 1:
        out[()] = np.nan
        if returns_1d:
            out = out.item()
        return out

    ann_factor = annualization_factor(period, annualization)

    downside_diff = np.clip(
        _adjust_returns(
            np.asanyarray(returns),
            np.asanyarray(required_return),
        ),
        np.NINF,
        0,
    )

    np.square(downside_diff, out=downside_diff)
    nanmean(downside_diff, axis=0, out=out)
    np.sqrt(out, out=out)
    np.multiply(out, np.sqrt(ann_factor), out=out)

    if returns_1d:
        out = out.item()
    elif isinstance(returns, pd.DataFrame):
        out = pd.Series(out, index=returns.columns)
    return out


roll_downsize_risk = _create_unary_vectorized_roll_function(downside_risk)


def excess_sharpe(returns, factor_returns, out=None):
    """
    Determines the Excess Sharpe of a portfolio.
    Parameters
    ----------
    returns : pd.Series or np.ndarray
        Daily returns of the portfolio, noncumulative.
    factor_returns: float / series
        Benchmark return to compare returns against.
    out : array-like, optional
        Array to use as output buffer.
        If not passed, a new array will be created.
    Returns
    -------
    excess_sharpe : float
    Note
    -----
    The excess Sharpe is a simplified Information Ratio that uses
    tracking error rather than "active risk" as the denominator.
    """
    allocated_output = out is None
    if allocated_output:
        out = np.empty(returns.shape[1:])

    returns_1d = returns.ndim == 1

    if len(returns) < 2:
        out[()] = np.nan
        if returns_1d:
            out = out.item()
        return out

    active_return = _adjust_returns(returns, factor_returns)
    tracking_error = np.nan_to_num(nanstd(active_return, ddof=1, axis=0))

    out = np.divide(
        nanmean(active_return, axis=0, out=out),
        tracking_error,
        out=out,
    )
    if returns_1d:
        out = out.item()
    return out


roll_excess_sharpe = _create_binary_vectorized_roll_function(excess_sharpe)


def _to_pandas(ob):
    """Convert an array-like to a pandas object.
    Parameters
    ----------
    ob : array-like
        The object to convert.
    Returns
    -------
    pandas_structure : pd.Series or pd.DataFrame
        The correct structure based on the dimensionality of the data.
    """
    if isinstance(ob, (pd.Series, pd.DataFrame)):
        return ob

    if ob.ndim == 1:
        return pd.Series(ob)
    elif ob.ndim == 2:
        return pd.DataFrame(ob)
    else:
        raise ValueError(
            'cannot convert array of dim > 2 to a pandas structure',
        )


def _aligned_series(*many_series):
    """
    Return a new list of series containing the data in the input series, but
    with their indices aligned. NaNs will be filled in for missing values.
    Parameters
    ----------
    *many_series
        The series to align.
    Returns
    -------
    aligned_series : iterable[array-like]
        A new list of series containing the data in the input series, but
        with their indices aligned. NaNs will be filled in for missing values.
    """
    head = many_series[0]
    tail = many_series[1:]
    n = len(head)
    if (isinstance(head, np.ndarray) and
            all(len(s) == n and isinstance(s, np.ndarray) for s in tail)):
        # optimization: ndarrays of the same length are already aligned
        return many_series

    # dataframe has no ``itervalues``
    return (
        v
        for _, v in iteritems(pd.concat(map(_to_pandas, many_series), axis=1))
    )


def alpha_beta(returns,
               factor_returns,
               risk_free=0.0,
               period=DAILY,
               annualization=None,
               out=None):
    """Calculates annualized alpha and beta.
    Parameters
    ----------
    returns : pd.Series
        Daily returns of the portfolio, noncumulative.
    factor_returns : pd.Series
         Daily noncumulative returns of the factor to which beta is
         computed. Usually a benchmark such as the market.
         - This is in the same style as returns.
    risk_free : int, float, optional
        Constant risk-free return throughout the period. For example, the
        interest rate on a three month us treasury bill.
    period : str, optional
        Defines the periodicity of the 'returns' data for purposes of
        annualizing. Value ignored if `annualization` parameter is specified.
        Defaults are::
            'monthly':12
            'weekly': 52
            'daily': 252
    annualization : int, optional
        Used to suppress default values available in `period` to convert
        returns into annual returns. Value should be the annual frequency of
        `returns`.
    out : array-like, optional
        Array to use as output buffer.
        If not passed, a new array will be created.
    Returns
    -------
    alpha : float
    beta : float
    """
    returns, factor_returns = _aligned_series(returns, factor_returns)

    return alpha_beta_aligned(
        returns,
        factor_returns,
        risk_free=risk_free,
        period=period,
        annualization=annualization,
        out=out,
    )


def roll_alpha_beta(returns, factor_returns, window=10, **kwargs):
    """
    Computes alpha and beta over a rolling window.
    Parameters
    ----------
    lhs : array-like
        The first array to pass to the rolling alpha-beta.
    rhs : array-like
        The second array to pass to the rolling alpha-beta.
    window : int
        Size of the rolling window in terms of the periodicity of the data.
    out : array-like, optional
        Array to use as output buffer.
        If not passed, a new array will be created.
    **kwargs
    """
    returns, factor_returns = _aligned_series(returns, factor_returns)

    return roll_alpha_beta_aligned(
        returns,
        factor_returns,
        window=window,
        **kwargs
    )


def alpha_beta_aligned(returns,
                       factor_returns,
                       risk_free=0.0,
                       period=DAILY,
                       annualization=None,
                       out=None):
    """Calculates annualized alpha and beta.
    If they are pd.Series, expects returns and factor_returns have already
    been aligned on their labels.  If np.ndarray, these arguments should have
    the same shape.
    Parameters
    ----------
    returns : pd.Series or np.ndarray
        Daily returns of the portfolio, noncumulative.
    factor_returns : pd.Series or np.ndarray
         Daily noncumulative returns of the factor to which beta is
         computed. Usually a benchmark such as the market.
         - This is in the same style as returns.
    risk_free : int, float, optional
        Constant risk-free return throughout the period. For example, the
        interest rate on a three month us treasury bill.
    period : str, optional
        Defines the periodicity of the 'returns' data for purposes of
        annualizing. Value ignored if `annualization` parameter is specified.
        Defaults are::
            'monthly':12
            'weekly': 52
            'daily': 252
    annualization : int, optional
        Used to suppress default values available in `period` to convert
        returns into annual returns. Value should be the annual frequency of
        `returns`.
    out : array-like, optional
        Array to use as output buffer.
        If not passed, a new array will be created.
    Returns
    -------
    alpha : float
    beta : float
    """
    if out is None:
        out = np.empty(returns.shape[1:] + (2,), dtype='float64')

    b = beta_aligned(returns, factor_returns, risk_free, out=out[..., 1])
    alpha_aligned(
        returns,
        factor_returns,
        risk_free,
        period,
        annualization,
        out=out[..., 0],
        _beta=b,
    )

    return out


roll_alpha_beta_aligned = _create_binary_vectorized_roll_function(
    alpha_beta_aligned,
)


def alpha(returns,
          factor_returns,
          risk_free=0.0,
          period=DAILY,
          annualization=None,
          out=None,
          _beta=None):
    """Calculates annualized alpha.
    Parameters
    ----------
    returns : pd.Series
        Daily returns of the portfolio, noncumulative.
    factor_returns : pd.Series
        Daily noncumulative returns of the factor to which beta is
        computed. Usually a benchmark such as the market.
        - This is in the same style as returns.
    risk_free : int, float, optional
        Constant risk-free return throughout the period. For example, the
        interest rate on a three month us treasury bill.
    period : str, optional
        Defines the periodicity of the 'returns' data for purposes of
        annualizing. Value ignored if `annualization` parameter is specified.
        Defaults are::
            'monthly':12
            'weekly': 52
            'daily': 252
    annualization : int, optional
        Used to suppress default values available in `period` to convert
        returns into annual returns. Value should be the annual frequency of
        `returns`.
    _beta : float, optional
        The beta for the given inputs, if already known. Will be calculated
        internally if not provided.
    out : array-like, optional
        Array to use as output buffer.
        If not passed, a new array will be created.
    Returns
    -------
    float
        Alpha.
    """
    if not (isinstance(returns, np.ndarray) and
            isinstance(factor_returns, np.ndarray)):
        returns, factor_returns = _aligned_series(returns, factor_returns)

    return alpha_aligned(
        returns,
        factor_returns,
        risk_free=risk_free,
        period=period,
        annualization=annualization,
        out=out,
        _beta=_beta
    )


roll_alpha = _create_binary_vectorized_roll_function(alpha)


def alpha_aligned(returns,
                  factor_returns,
                  risk_free=0.0,
                  period=DAILY,
                  annualization=None,
                  out=None,
                  _beta=None):
    """Calculates annualized alpha.
    If they are pd.Series, expects returns and factor_returns have already
    been aligned on their labels.  If np.ndarray, these arguments should have
    the same shape.
    Parameters
    ----------
    returns : pd.Series or np.ndarray
        Daily returns of the portfolio, noncumulative.
    factor_returns : pd.Series or np.ndarray
        Daily noncumulative returns of the factor to which beta is
        computed. Usually a benchmark such as the market.
        - This is in the same style as returns.
    risk_free : int, float, optional
        Constant risk-free return throughout the period. For example, the
        interest rate on a three month us treasury bill.
    period : str, optional
        Defines the periodicity of the 'returns' data for purposes of
        annualizing. Value ignored if `annualization` parameter is specified.
        Defaults are::
            'monthly':12
            'weekly': 52
            'daily': 252
    annualization : int, optional
        Used to suppress default values available in `period` to convert
        returns into annual returns. Value should be the annual frequency of
        `returns`.
    _beta : float, optional
        The beta for the given inputs, if already known. Will be calculated
        internally if not provided.
    out : array-like, optional
        Array to use as output buffer.
        If not passed, a new array will be created.
    Returns
    -------
    alpha : float
    """
    allocated_output = out is None
    if allocated_output:
        out = np.empty(returns.shape[1:], dtype='float64')

    if len(returns) < 2:
        out[()] = np.nan
        if returns.ndim == 1:
            out = out.item()
        return out

    ann_factor = annualization_factor(period, annualization)

    if _beta is None:
        _beta = beta_aligned(returns, factor_returns, risk_free)

    adj_returns = _adjust_returns(returns, risk_free)
    adj_factor_returns = _adjust_returns(factor_returns, risk_free)
    alpha_series = adj_returns - (_beta * adj_factor_returns)

    out = np.subtract(
        np.power(
            np.add(
                nanmean(alpha_series, axis=0, out=out),
                1,
                out=out
            ),
            ann_factor,
            out=out
        ),
        1,
        out=out
    )

    if allocated_output and isinstance(returns, pd.DataFrame):
        out = pd.Series(out)

    if returns.ndim == 1:
        out = out.item()

    return out


roll_alpha_aligned = _create_binary_vectorized_roll_function(alpha_aligned)


def beta(returns, factor_returns, risk_free=0.0, out=None):
    """Calculates beta.
    Parameters
    ----------
    returns : pd.Series
        Daily returns of the portfolio, noncumulative.
    factor_returns : pd.Series
         Daily noncumulative returns of the factor to which beta is
         computed. Usually a benchmark such as the market.
         - This is in the same style as returns.
    risk_free : int, float, optional
        Constant risk-free return throughout the period. For example, the
        interest rate on a three month us treasury bill.
    out : array-like, optional
        Array to use as output buffer.
        If not passed, a new array will be created.
    Returns
    -------
    beta : float
    """
    if not (isinstance(returns, np.ndarray) and
            isinstance(factor_returns, np.ndarray)):
        returns, factor_returns = _aligned_series(returns, factor_returns)

    return beta_aligned(
        returns,
        factor_returns,
        risk_free=risk_free,
        out=out,
    )


roll_beta = _create_binary_vectorized_roll_function(beta)


def beta_aligned(returns, factor_returns, risk_free=0.0, out=None):
    """Calculates beta.
    If they are pd.Series, expects returns and factor_returns have already
    been aligned on their labels.  If np.ndarray, these arguments should have
    the same shape.
    Parameters
    ----------
    returns : pd.Series or np.ndarray
        Daily returns of the portfolio, noncumulative.
    factor_returns : pd.Series or np.ndarray
         Daily noncumulative returns of the factor to which beta is
         computed. Usually a benchmark such as the market.
         - This is in the same style as returns.
    risk_free : int, float, optional
        Constant risk-free return throughout the period. For example, the
        interest rate on a three month us treasury bill.
    out : array-like, optional
        Array to use as output buffer.
        If not passed, a new array will be created.
    Returns
    -------
    beta : float
        Beta.
    """
    nan = np.nan
    isnan = np.isnan

    returns_1d = returns.ndim == 1
    if returns_1d:
        returns = returns[:, np.newaxis]

    if factor_returns.ndim == 1:
        factor_returns = factor_returns[:, np.newaxis]

    N, M = returns.shape

    if out is None:
        out = np.full(M, nan)
    elif out.ndim == 0:
        out = out[np.newaxis]

    if len(returns) < 1 or len(factor_returns) < 2:
        out[()] = nan
        if returns_1d:
            out = out.item()
        return out

    independent = np.where(
        isnan(returns),
        nan,
        factor_returns,
    )

    ind_residual = independent - nanmean(independent, axis=0)

    covariances = nanmean(ind_residual * returns, axis=0)

    np.square(ind_residual, out=ind_residual)
    independent_variances = nanmean(ind_residual, axis=0)
    independent_variances[independent_variances < 1.0e-30] = np.nan

    np.divide(covariances, independent_variances, out=out)

    if returns_1d:
        out = out.item()

    return out


roll_beta_aligned = _create_binary_vectorized_roll_function(beta_aligned)


def stability_of_timeseries(returns):
    """Determines R-squared of a linear fit to the cumulative
    log returns. Computes an ordinary least squares linear fit,
    and returns R-squared.
    Parameters
    ----------
    returns : pd.Series or np.ndarray
        Daily returns of the portfolio, noncumulative.
    Returns
    -------
    float
        R-squared.
    """
    if len(returns) < 2:
        return np.nan

    returns = np.asanyarray(returns)
    returns = returns[~np.isnan(returns)]

    cum_log_returns = np.log1p(returns).cumsum()
    rhat = stats.linregress(np.arange(len(cum_log_returns)),
                            cum_log_returns)[2]

    return rhat ** 2


def tail_ratio(returns):
    """Determines the ratio between the right (95%) and left tail (5%).
    For example, a ratio of 0.25 means that losses are four times
    as bad as profits.
    Parameters
    ----------
    returns : pd.Series or np.ndarray
        Daily returns of the portfolio, noncumulative.
    Returns
    -------
    tail_ratio : float
    """

    if len(returns) < 1:
        return np.nan

    returns = np.asanyarray(returns)
    returns = returns[~np.isnan(returns)]
    if len(returns) < 1:
        return np.nan

    return np.abs(np.percentile(returns, 95)) / \
           np.abs(np.percentile(returns, 5))


def capture(returns, factor_returns, period=DAILY):
    """Compute capture ratio.
    Parameters
    ----------
    returns : pd.Series or np.ndarray
        Returns of the portfolio, noncumulative.
    factor_returns : pd.Series or np.ndarray
        Noncumulative returns of the factor to which beta is
        computed. Usually a benchmark such as the market.
        - This is in the same style as returns.
    period : str, optional
        Defines the periodicity of the 'returns' data for purposes of
        annualizing. Value ignored if `annualization` parameter is specified.
        Defaults are::
            'monthly':12
            'weekly': 52
            'daily': 252
    Returns
    -------
    capture_ratio : float
    """
    return (annual_return(returns, period=period) /
            annual_return(factor_returns, period=period))


def beta_fragility_heuristic(returns, factor_returns):
    """Estimate fragility to drops in beta.
    Parameters
    ----------
    returns : pd.Series or np.ndarray
        Daily returns of the portfolio, noncumulative.
    factor_returns : pd.Series or np.ndarray
         Daily noncumulative returns of the factor to which beta is
         computed. Usually a benchmark such as the market.
         - This is in the same style as returns.
    Returns
    -------
    float, np.nan
        The beta fragility of the portfolio.
    Note
    ----
    A negative return value indicates potential losses
    could follow volatility in beta.
    The magnitude of the negative value indicates the size of
    the potential loss.
    """
    if len(returns) < 3 or len(factor_returns) < 3:
        return np.nan

    return beta_fragility_heuristic_aligned(
        *_aligned_series(returns, factor_returns))


def beta_fragility_heuristic_aligned(returns, factor_returns):
    """Estimate fragility to drops in beta
    Parameters
    ----------
    returns : pd.Series or np.ndarray
        Daily returns of the portfolio, noncumulative.
    factor_returns : pd.Series or np.ndarray
         Daily noncumulative returns of the factor to which beta is
         computed. Usually a benchmark such as the market.
         - This is in the same style as returns.
    Returns
    -------
    float, np.nan
        The beta fragility of the portfolio.
    Note
    ----
    If they are pd.Series, expects returns and factor_returns have already
    been aligned on their labels.  If np.ndarray, these arguments should have
    the same shape.
    """
    if len(returns) < 3 or len(factor_returns) < 3:
        return np.nan

    returns_series = pd.Series(returns)
    factor_returns_series = pd.Series(factor_returns)
    pairs = pd.concat([returns_series, factor_returns_series], axis=1)
    pairs.columns = ['returns', 'factor_returns']

    pairs = pairs.dropna()
    pairs = pairs.sort_values(by='factor_returns')

    start_index = 0
    mid_index = int(np.around(len(pairs) / 2, 0))
    end_index = len(pairs) - 1

    (start_returns, start_factor_returns) = pairs.iloc[start_index]
    (mid_returns, mid_factor_returns) = pairs.iloc[mid_index]
    (end_returns, end_factor_returns) = pairs.iloc[end_index]

    factor_returns_range = (end_factor_returns - start_factor_returns)
    start_returns_weight = 0.5
    end_returns_weight = 0.5

    if not factor_returns_range == 0:
        start_returns_weight = \
            (mid_factor_returns - start_factor_returns) / \
            factor_returns_range
        end_returns_weight = \
            (end_factor_returns - mid_factor_returns) / \
            factor_returns_range

    heuristic = (start_returns_weight * start_returns) + \
                (end_returns_weight * end_returns) - mid_returns

    return heuristic


def gpd_risk_estimates(returns, var_p=0.01):
    """Estimate VaR and ES using the Generalized Pareto Distribution (GPD)
    Parameters
    ----------
    returns : pd.Series or np.ndarray
        Daily returns of the portfolio, noncumulative.
    var_p : float
        The percentile to use for estimating the VaR and ES
    Returns
    -------
    [threshold, scale_param, shape_param, var_estimate, es_estimate]
        : list[float]
        threshold - the threshold use to cut off exception tail losses
        scale_param - a parameter (often denoted by sigma, capturing the
            scale, related to variance)
        shape_param - a parameter (often denoted by xi, capturing the shape or
            type of the distribution)
        var_estimate - an estimate for the VaR for the given percentile
        es_estimate - an estimate for the ES for the given percentile
    """
    if len(returns) < 3:
        result = np.zeros(5)
        if isinstance(returns, pd.Series):
            result = pd.Series(result)
        return result
    return gpd_risk_estimates_aligned(*_aligned_series(returns, var_p))


def gpd_risk_estimates_aligned(returns, var_p=0.01):
    """Estimate VaR and ES using the Generalized Pareto Distribution (GPD)
    Parameters
    ----------
    returns : pd.Series or np.ndarray
        Daily returns of the portfolio, noncumulative.
    var_p : float
        The percentile to use for estimating the VaR and ES
    Returns
    -------
    [threshold, scale_param, shape_param, var_estimate, es_estimate]
        : list[float]
        threshold - the threshold use to cut off exception tail losses
        scale_param - a parameter (often denoted by sigma, capturing the
            scale, related to variance)
        shape_param - a parameter (often denoted by xi, capturing the shape or
            type of the distribution)
        var_estimate - an estimate for the VaR for the given percentile
        es_estimate - an estimate for the ES for the given percentile
    """
    result = np.zeros(5)
    if not len(returns) < 3:

        DEFAULT_THRESHOLD = 0.2
        MINIMUM_THRESHOLD = 0.000000001
        returns_array = pd.Series(returns).as_matrix()
        flipped_returns = -1 * returns_array
        losses = flipped_returns[flipped_returns > 0]
        threshold = DEFAULT_THRESHOLD
        finished = False
        scale_param = 0
        shape_param = 0
        while not finished and threshold > MINIMUM_THRESHOLD:
            losses_beyond_threshold = \
                losses[losses >= threshold]
            param_result = \
                gpd_loglikelihood_minimizer_aligned(losses_beyond_threshold)
            if (param_result[0] is not False and
                    param_result[1] is not False):
                scale_param = param_result[0]
                shape_param = param_result[1]
                var_estimate = gpd_var_calculator(threshold, scale_param,
                                                  shape_param, var_p,
                                                  len(losses),
                                                  len(losses_beyond_threshold))

                if (shape_param > 0 and var_estimate > 0):
                    finished = True
            if (not finished):
                threshold = threshold / 2
        if (finished):
            es_estimate = gpd_es_calculator(var_estimate, threshold,
                                            scale_param, shape_param)
            result = np.array([threshold, scale_param, shape_param,
                               var_estimate, es_estimate])
    if isinstance(returns, pd.Series):
        result = pd.Series(result)
    return result


def gpd_es_calculator(var_estimate, threshold, scale_param,
                      shape_param):
    result = 0
    if ((1 - shape_param) != 0):
        var_ratio = (var_estimate / (1 - shape_param))
        param_ratio = ((scale_param - (shape_param * threshold)) /
                       (1 - shape_param))
        result = var_ratio + param_ratio
    return result


def gpd_var_calculator(threshold, scale_param, shape_param,
                       probability, total_n, exceedance_n):
    result = 0
    if (exceedance_n > 0 and shape_param > 0):
        param_ratio = scale_param / shape_param
        prob_ratio = (total_n / exceedance_n) * probability
        result = threshold + (param_ratio *
                              (pow(prob_ratio, -shape_param) - 1))
    return result


def gpd_loglikelihood_minimizer_aligned(price_data):
    result = [False, False]
    DEFAULT_SCALE_PARAM = 1
    DEFAULT_SHAPE_PARAM = 1
    if (len(price_data) > 0):
        gpd_loglikelihood_lambda = \
            gpd_loglikelihood_factory(price_data)
        optimization_results = \
            optimize.minimize(gpd_loglikelihood_lambda,
                              [DEFAULT_SCALE_PARAM,
                               DEFAULT_SHAPE_PARAM],
                              method='Nelder-Mead')
        if optimization_results.success:
            resulting_params = optimization_results.x
            if len(resulting_params) == 2:
                result[0] = resulting_params[0]
                result[1] = resulting_params[1]
    return result


def gpd_loglikelihood_factory(price_data):
    return lambda params: gpd_loglikelihood(params, price_data)


def gpd_loglikelihood(params, price_data):
    if (params[1] != 0):
        return -gpd_loglikelihood_scale_and_shape(params[0],
                                                  params[1],
                                                  price_data)
    else:
        return -gpd_loglikelihood_scale_only(params[0], price_data)


def gpd_loglikelihood_scale_and_shape_factory(price_data):
    return lambda params: \
        -gpd_loglikelihood_scale_and_shape(params[0],
                                           params[1],
                                           price_data)


def gpd_loglikelihood_scale_and_shape(scale, shape, price_data):
    n = len(price_data)
    result = -1 * float_info.max
    if (scale != 0):
        param_factor = shape / scale
        if (shape != 0 and param_factor >= 0 and scale >= 0):
            result = ((-n * np.log(scale)) -
                      (((1 / shape) + 1) *
                       (np.log((shape / scale * price_data) + 1)).sum()))
    return result


def gpd_loglikelihood_scale_only_factory(price_data):
    return lambda scale: \
        -gpd_loglikelihood_scale_only(scale, price_data)


def gpd_loglikelihood_scale_only(scale, price_data):
    n = len(price_data)
    data_sum = price_data.sum()
    result = -1 * float_info.max
    if (scale >= 0):
        result = ((-n * np.log(scale)) - (data_sum / scale))
    return result


def up_capture(returns, factor_returns, **kwargs):
    """
    Compute the capture ratio for periods when the benchmark return is positive
    Parameters
    ----------
    returns : pd.Series or np.ndarray
        Returns of the portfolio, noncumulative.
    factor_returns : pd.Series or np.ndarray
        Noncumulative returns of the factor to which beta is
        computed. Usually a benchmark such as the market.
        - This is in the same style as returns.
    period : str, optional
        Defines the periodicity of the 'returns' data for purposes of
        annualizing. Value ignored if `annualization` parameter is specified.
        Defaults are::
            'monthly':12
            'weekly': 52
            'daily': 252
    Returns
    -------
    up_capture : float
    """
    return up(returns, factor_returns, function=capture, **kwargs)


def down_capture(returns, factor_returns, **kwargs):
    """
    Compute the capture ratio for periods when the benchmark return is negative
    Parameters
    ----------
    returns : pd.Series or np.ndarray
        Returns of the portfolio, noncumulative.
    factor_returns : pd.Series or np.ndarray
        Noncumulative returns of the factor to which beta is
        computed. Usually a benchmark such as the market.
        - This is in the same style as returns.
    period : str, optional
        Defines the periodicity of the 'returns' data for purposes of
        annualizing. Value ignored if `annualization` parameter is specified.
        Defaults are::
            'monthly':12
            'weekly': 52
            'daily': 252
    Returns
    -------
    down_capture : float

    """
    return down(returns, factor_returns, function=capture, **kwargs)


def up_down_capture(returns, factor_returns, **kwargs):
    """
    Computes the ratio of up_capture to down_capture.
    Parameters
    ----------
    returns : pd.Series or np.ndarray
        Returns of the portfolio, noncumulative.
    factor_returns : pd.Series or np.ndarray
        Noncumulative returns of the factor to which beta is
        computed. Usually a benchmark such as the market.
        - This is in the same style as returns.
    period : str, optional
        Defines the periodicity of the 'returns' data for purposes of
        annualizing. Value ignored if `annualization` parameter is specified.
        Defaults are::
            'monthly':12
            'weekly': 52
            'daily': 252
    Returns
    -------
    up_down_capture : float
        the updown capture ratio
    """
    return (up_capture(returns, factor_returns, **kwargs) /
            down_capture(returns, factor_returns, **kwargs))


def up_alpha_beta(returns, factor_returns, **kwargs):
    """
    Computes alpha and beta for periods when the benchmark return is positive.
    Parameters
    ----------
    see documentation for `alpha_beta`.
    Returns
    -------
    float
        Alpha.
    float
        Beta.
    """
    return up(returns, factor_returns, function=alpha_beta_aligned, **kwargs)


def down_alpha_beta(returns, factor_returns, **kwargs):
    """
    Computes alpha and beta for periods when the benchmark return is negative.
    Parameters
    ----------
    see documentation for `alpha_beta`.
    Returns
    -------
    alpha : float
    beta : float
    """
    return down(returns, factor_returns, function=alpha_beta_aligned, **kwargs)


def roll_up_capture(returns, factor_returns, window=10, **kwargs):
    """
    Computes the up capture measure over a rolling window.
    (pass all args, kwargs required)
    Parameters
    ----------
    returns : pd.Series or np.ndarray
        Daily returns of the portfolio, noncumulative.
    factor_returns : pd.Series or np.ndarray
        Noncumulative returns of the factor to which beta is
        computed. Usually a benchmark such as the market.
        - This is in the same style as returns.
    window : int, required
        Size of the rolling window in terms of the periodicity of the data.
        - eg window = 60, periodicity=DAILY, represents a rolling 60 day window
    """
    return roll(returns, factor_returns, window=window, function=up_capture,
                **kwargs)


def roll_down_capture(returns, factor_returns, window=10, **kwargs):
    """
    Computes the down capture measure over a rolling window.
    (pass all args, kwargs required)
    Parameters
    ----------
    returns : pd.Series or np.ndarray
        Daily returns of the portfolio, noncumulative.
    factor_returns : pd.Series or np.ndarray
        Noncumulative returns of the factor to which beta is
        computed. Usually a benchmark such as the market.
        - This is in the same style as returns.
    window : int, required
        Size of the rolling window in terms of the periodicity of the data.
        - eg window = 60, periodicity=DAILY, represents a rolling 60 day window
    """
    return roll(returns, factor_returns, window=window, function=down_capture,
                **kwargs)


def roll_up_down_capture(returns, factor_returns, window=10, **kwargs):
    """
    Computes the up/down capture measure over a rolling window.
    (pass all args, kwargs required)
    Parameters
    ----------
    returns : pd.Series or np.ndarray
        Daily returns of the portfolio, noncumulative.
    factor_returns : pd.Series or np.ndarray
        Noncumulative returns of the factor to which beta is
        computed. Usually a benchmark such as the market.
        - This is in the same style as returns.
    window : int, required
        Size of the rolling window in terms of the periodicity of the data.
        - eg window = 60, periodicity=DAILY, represents a rolling 60 day window
    """
    return roll(returns, factor_returns, window=window,
                function=up_down_capture, **kwargs)


def value_at_risk(returns, cutoff=0.05):
    """
    Value at risk (VaR) of a returns stream.
    Parameters
    ----------
    returns : pandas.Series or 1-D numpy.array
        Non-cumulative daily returns.
    cutoff : float, optional
        Decimal representing the percentage cutoff for the bottom percentile of
        returns. Defaults to 0.05.
    Returns
    -------
    VaR : float
        The VaR value.
    """
    return np.percentile(returns, 100 * cutoff)


def conditional_value_at_risk(returns, cutoff=0.05):
    """
    Conditional value at risk (CVaR) of a returns stream.
    CVaR measures the expected single-day returns of an asset on that asset's
    worst performing days, where "worst-performing" is defined as falling below
    ``cutoff`` as a percentile of all daily returns.
    Parameters
    ----------
    returns : pandas.Series or 1-D numpy.array
        Non-cumulative daily returns.
    cutoff : float, optional
        Decimal representing the percentage cutoff for the bottom percentile of
        returns. Defaults to 0.05.
    Returns
    -------
    CVaR : float
        The CVaR value.
    """
    cutoff_index = int((len(returns) - 1) * cutoff)
    return np.mean(np.partition(returns, cutoff_index)[:cutoff_index + 1])


def win_rate(returns):
    """
    Determines win ratio of a portfolio.
    Parameters
    ----------
    returns : pd.Series or np.ndarray
        Daily returns of the portfolio, noncumulative.
    Returns
    -------
    hit_rate : float
    """
    if not isinstance(returns, np.ndarray):
        returns = returns.to_numpy()
    hit_rate = np.add.accumulate(returns >= 0) / np.add.accumulate((returns >= 0) | (returns <= 0))
    return hit_rate


def loss_rate(returns):
    """
    Determines loss ratio of a portfolio.
    Parameters
    ----------
    returns : pd.Series or np.ndarray
        Daily returns of the portfolio, noncumulative.
    Returns
    -------
    miss_rate : float
    """
    if not isinstance(returns, np.ndarray):
        returns = returns.to_numpy()
    miss_rate = np.add.accumulate(returns <= 0) / np.add.accumulate((returns >= 0) | (returns <= 0))
    return miss_rate


def calc_df_drawdown(df, on='pnl_dt'):
    """
    Calculates Rolf's drawdown measure.
    Parameters
    ----------
    df : pd.DataFrame
        Timeseries of portfolio P&L, noncumulative.
    on : str
        DataFrame column on which to calculate the metric.
    Returns
    -------
    miss_rate : float
    """
    try:
        df.sort_values(by=['date'], ascending=True, inplace=True)
    except KeyError:
        logger.info('Date not in dataframe')

    prev_high = 0
    df['cumulative'] = df[on].cumsum()
    for i, r in df.iterrows():
        prev_high = max(prev_high, r['cumulative'])
        dd = r['cumulative'] - prev_high
        df.loc[i, 'drawdown'] = dd if dd < 0 else 0
    return df
