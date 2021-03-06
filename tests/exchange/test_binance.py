from datetime import datetime, timezone
from random import randint
from unittest.mock import MagicMock, PropertyMock

import ccxt
import pytest

from freqtrade.enums import Collateral, TradingMode
from freqtrade.exceptions import DependencyException, InvalidOrderException, OperationalException
from tests.conftest import get_mock_coro, get_patched_exchange, log_has_re
from tests.exchange.test_exchange import ccxt_exceptionhandlers


@pytest.mark.parametrize('limitratio,expected,side', [
    (None, 220 * 0.99, "sell"),
    (0.99, 220 * 0.99, "sell"),
    (0.98, 220 * 0.98, "sell"),
    (None, 220 * 1.01, "buy"),
    (0.99, 220 * 1.01, "buy"),
    (0.98, 220 * 1.02, "buy"),
])
def test_stoploss_order_binance(
    default_conf,
    mocker,
    limitratio,
    expected,
    side
):
    api_mock = MagicMock()
    order_id = 'test_prod_buy_{}'.format(randint(0, 10 ** 6))
    order_type = 'stop_loss_limit'

    api_mock.create_order = MagicMock(return_value={
        'id': order_id,
        'info': {
            'foo': 'bar'
        }
    })
    default_conf['dry_run'] = False
    mocker.patch('freqtrade.exchange.Exchange.amount_to_precision', lambda s, x, y: y)
    mocker.patch('freqtrade.exchange.Exchange.price_to_precision', lambda s, x, y: y)

    exchange = get_patched_exchange(mocker, default_conf, api_mock, 'binance')

    with pytest.raises(OperationalException):
        order = exchange.stoploss(
            pair='ETH/BTC',
            amount=1,
            stop_price=190,
            side=side,
            order_types={'stoploss_on_exchange_limit_ratio': 1.05},
            leverage=1.0
        )

    api_mock.create_order.reset_mock()
    order_types = {} if limitratio is None else {'stoploss_on_exchange_limit_ratio': limitratio}
    order = exchange.stoploss(
        pair='ETH/BTC',
        amount=1,
        stop_price=220,
        order_types=order_types,
        side=side,
        leverage=1.0
    )

    assert 'id' in order
    assert 'info' in order
    assert order['id'] == order_id
    assert api_mock.create_order.call_args_list[0][1]['symbol'] == 'ETH/BTC'
    assert api_mock.create_order.call_args_list[0][1]['type'] == order_type
    assert api_mock.create_order.call_args_list[0][1]['side'] == side
    assert api_mock.create_order.call_args_list[0][1]['amount'] == 1
    # Price should be 1% below stopprice
    assert api_mock.create_order.call_args_list[0][1]['price'] == expected
    assert api_mock.create_order.call_args_list[0][1]['params'] == {'stopPrice': 220}

    # test exception handling
    with pytest.raises(DependencyException):
        api_mock.create_order = MagicMock(side_effect=ccxt.InsufficientFunds("0 balance"))
        exchange = get_patched_exchange(mocker, default_conf, api_mock, 'binance')
        exchange.stoploss(
            pair='ETH/BTC',
            amount=1,
            stop_price=220,
            order_types={},
            side=side,
            leverage=1.0)

    with pytest.raises(InvalidOrderException):
        api_mock.create_order = MagicMock(
            side_effect=ccxt.InvalidOrder("binance Order would trigger immediately."))
        exchange = get_patched_exchange(mocker, default_conf, api_mock, 'binance')
        exchange.stoploss(
            pair='ETH/BTC',
            amount=1,
            stop_price=220,
            order_types={},
            side=side,
            leverage=1.0
        )

    ccxt_exceptionhandlers(mocker, default_conf, api_mock, "binance",
                           "stoploss", "create_order", retries=1,
                           pair='ETH/BTC', amount=1, stop_price=220, order_types={},
                           side=side, leverage=1.0)


def test_stoploss_order_dry_run_binance(default_conf, mocker):
    api_mock = MagicMock()
    order_type = 'stop_loss_limit'
    default_conf['dry_run'] = True
    mocker.patch('freqtrade.exchange.Exchange.amount_to_precision', lambda s, x, y: y)
    mocker.patch('freqtrade.exchange.Exchange.price_to_precision', lambda s, x, y: y)

    exchange = get_patched_exchange(mocker, default_conf, api_mock, 'binance')

    with pytest.raises(OperationalException):
        order = exchange.stoploss(
            pair='ETH/BTC',
            amount=1,
            stop_price=190,
            side="sell",
            order_types={'stoploss_on_exchange_limit_ratio': 1.05},
            leverage=1.0
        )

    api_mock.create_order.reset_mock()

    order = exchange.stoploss(
        pair='ETH/BTC',
        amount=1,
        stop_price=220,
        order_types={},
        side="sell",
        leverage=1.0
    )

    assert 'id' in order
    assert 'info' in order
    assert 'type' in order

    assert order['type'] == order_type
    assert order['price'] == 220
    assert order['amount'] == 1


@pytest.mark.parametrize('sl1,sl2,sl3,side', [
    (1501, 1499, 1501, "sell"),
    (1499, 1501, 1499, "buy")
])
def test_stoploss_adjust_binance(mocker, default_conf, sl1, sl2, sl3, side):
    exchange = get_patched_exchange(mocker, default_conf, id='binance')
    order = {
        'type': 'stop_loss_limit',
        'price': 1500,
        'info': {'stopPrice': 1500},
    }
    assert exchange.stoploss_adjust(sl1, order, side=side)
    assert not exchange.stoploss_adjust(sl2, order, side=side)
    # Test with invalid order case
    order['type'] = 'stop_loss'
    assert not exchange.stoploss_adjust(sl3, order, side=side)


@pytest.mark.parametrize('pair,stake_amount,max_lev', [
    ("BNB/BUSD", 0.0, 40.0),
    ("BNB/USDT", 100.0, 100.0),
    ("BTC/USDT", 170.30, 250.0),
    ("BNB/BUSD", 99999.9, 10.0),
    ("BNB/USDT", 750000, 6.666666666666667),
    ("BTC/USDT", 150000000.1, 2.0),
])
def test_get_max_leverage_binance(default_conf, mocker, pair, stake_amount, max_lev):
    exchange = get_patched_exchange(mocker, default_conf, id="binance")
    exchange._leverage_brackets = {
        'BNB/BUSD': [
            [0.0, 0.025, 0.0],  # lev = 40.0
            [100000.0, 0.05, 2500.0],  # lev = 20.0
            [500000.0, 0.1, 27500.0],  # lev = 10.0
            [1000000.0, 0.15, 77500.0],  # lev = 6.666666666666667
            [2000000.0, 0.25, 277500.0],  # lev = 4.0
            [5000000.0, 0.5, 1527500.0],  # lev = 2.0
        ],
        'BNB/USDT': [
            [0.0, 0.0065, 0.0],   # lev = 153.84615384615384
            [10000.0, 0.01, 35.0],   # lev = 100.0
            [50000.0, 0.02, 535.0],   # lev = 50.0
            [250000.0, 0.05, 8035.0],   # lev = 20.0
            [1000000.0, 0.1, 58035.0],   # lev = 10.0
            [2000000.0, 0.125, 108035.0],   # lev = 8.0
            [5000000.0, 0.15, 233035.0],   # lev = 6.666666666666667
            [10000000.0, 0.25, 1233035.0],   # lev = 4.0
        ],
        'BTC/USDT': [
            [0.0, 0.004, 0.0],   # lev = 250.0
            [50000.0, 0.005, 50.0],   # lev = 200.0
            [250000.0, 0.01, 1300.0],   # lev = 100.0
            [1000000.0, 0.025, 16300.0],   # lev = 40.0
            [5000000.0, 0.05, 141300.0],   # lev = 20.0
            [20000000.0, 0.1, 1141300.0],   # lev = 10.0
            [50000000.0, 0.125, 2391300.0],   # lev = 8.0
            [100000000.0, 0.15, 4891300.0],   # lev = 6.666666666666667
            [200000000.0, 0.25, 24891300.0],   # lev = 4.0
            [300000000.0, 0.5, 99891300.0],   # lev = 2.0
        ]
    }
    assert exchange.get_max_leverage(pair, stake_amount) == max_lev


def test_fill_leverage_brackets_binance(default_conf, mocker):
    api_mock = MagicMock()
    api_mock.load_leverage_brackets = MagicMock(return_value={
        'ADA/BUSD': [[0.0, 0.025],
                     [100000.0, 0.05],
                     [500000.0, 0.1],
                     [1000000.0, 0.15],
                     [2000000.0, 0.25],
                     [5000000.0, 0.5]],
        'BTC/USDT': [[0.0, 0.004],
                     [50000.0, 0.005],
                     [250000.0, 0.01],
                     [1000000.0, 0.025],
                     [5000000.0, 0.05],
                     [20000000.0, 0.1],
                     [50000000.0, 0.125],
                     [100000000.0, 0.15],
                     [200000000.0, 0.25],
                     [300000000.0, 0.5]],
        "ZEC/USDT": [[0.0, 0.01],
                     [5000.0, 0.025],
                     [25000.0, 0.05],
                     [100000.0, 0.1],
                     [250000.0, 0.125],
                     [1000000.0, 0.5]],

    })
    default_conf['dry_run'] = False
    default_conf['trading_mode'] = TradingMode.FUTURES
    default_conf['collateral'] = Collateral.ISOLATED
    exchange = get_patched_exchange(mocker, default_conf, api_mock, id="binance")
    exchange.fill_leverage_brackets()

    assert exchange._leverage_brackets == {
        'ADA/BUSD': [[0.0, 0.025, 0.0],
                     [100000.0, 0.05, 2500.0],
                     [500000.0, 0.1, 27500.0],
                     [1000000.0, 0.15, 77499.99999999999],
                     [2000000.0, 0.25, 277500.0],
                     [5000000.0, 0.5, 1527500.0]],
        'BTC/USDT': [[0.0, 0.004, 0.0],
                     [50000.0, 0.005, 50.0],
                     [250000.0, 0.01, 1300.0],
                     [1000000.0, 0.025, 16300.000000000002],
                     [5000000.0, 0.05, 141300.0],
                     [20000000.0, 0.1, 1141300.0],
                     [50000000.0, 0.125, 2391300.0],
                     [100000000.0, 0.15, 4891300.0],
                     [200000000.0, 0.25, 24891300.0],
                     [300000000.0, 0.5, 99891300.0]],
        "ZEC/USDT": [[0.0, 0.01, 0.0],
                     [5000.0, 0.025, 75.0],
                     [25000.0, 0.05, 700.0],
                     [100000.0, 0.1, 5700.0],
                     [250000.0, 0.125,  11949.999999999998],
                     [1000000.0, 0.5, 386950.0]]
    }

    api_mock = MagicMock()
    api_mock.load_leverage_brackets = MagicMock()
    type(api_mock).has = PropertyMock(return_value={'loadLeverageBrackets': True})

    ccxt_exceptionhandlers(
        mocker,
        default_conf,
        api_mock,
        "binance",
        "fill_leverage_brackets",
        "load_leverage_brackets"
    )


def test_fill_leverage_brackets_binance_dryrun(default_conf, mocker):
    api_mock = MagicMock()
    default_conf['trading_mode'] = TradingMode.FUTURES
    default_conf['collateral'] = Collateral.ISOLATED
    exchange = get_patched_exchange(mocker, default_conf, api_mock, id="binance")
    exchange.fill_leverage_brackets()

    leverage_brackets = {
        "1000SHIB/USDT": [
            [0.0, 0.01, 0.0],
            [5000.0, 0.025, 75.0],
            [25000.0, 0.05, 700.0],
            [100000.0, 0.1, 5700.0],
            [250000.0, 0.125, 11949.999999999998],
            [1000000.0, 0.5, 386950.0],
        ],
        "1INCH/USDT": [
            [0.0, 0.012, 0.0],
            [5000.0, 0.025, 65.0],
            [25000.0, 0.05, 690.0],
            [100000.0, 0.1, 5690.0],
            [250000.0, 0.125, 11939.999999999998],
            [1000000.0, 0.5, 386940.0],
        ],
        "AAVE/USDT": [
            [0.0, 0.01, 0.0],
            [50000.0, 0.02, 500.0],
            [250000.0, 0.05, 8000.000000000001],
            [1000000.0, 0.1, 58000.0],
            [2000000.0, 0.125, 107999.99999999999],
            [5000000.0, 0.1665, 315500.00000000006],
            [10000000.0, 0.25, 1150500.0],
        ],
        "ADA/BUSD": [
            [0.0, 0.025, 0.0],
            [100000.0, 0.05, 2500.0],
            [500000.0, 0.1, 27500.0],
            [1000000.0, 0.15, 77499.99999999999],
            [2000000.0, 0.25, 277500.0],
            [5000000.0, 0.5, 1527500.0],
        ]
    }

    for key, value in leverage_brackets.items():
        assert exchange._leverage_brackets[key] == value


def test__set_leverage_binance(mocker, default_conf):

    api_mock = MagicMock()
    api_mock.set_leverage = MagicMock()
    type(api_mock).has = PropertyMock(return_value={'setLeverage': True})
    default_conf['dry_run'] = False
    exchange = get_patched_exchange(mocker, default_conf, id="binance")
    exchange._set_leverage(3.0, trading_mode=TradingMode.MARGIN)

    ccxt_exceptionhandlers(
        mocker,
        default_conf,
        api_mock,
        "binance",
        "_set_leverage",
        "set_leverage",
        pair="XRP/USDT",
        leverage=5.0,
        trading_mode=TradingMode.FUTURES
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('candle_type', ['mark', ''])
async def test__async_get_historic_ohlcv_binance(default_conf, mocker, caplog, candle_type):
    ohlcv = [
        [
            int((datetime.now(timezone.utc).timestamp() - 1000) * 1000),
            1,  # open
            2,  # high
            3,  # low
            4,  # close
            5,  # volume (in quote currency)
        ]
    ]

    exchange = get_patched_exchange(mocker, default_conf, id='binance')
    # Monkey-patch async function
    exchange._api_async.fetch_ohlcv = get_mock_coro(ohlcv)

    pair = 'ETH/BTC'
    respair, restf, restype, res = await exchange._async_get_historic_ohlcv(
        pair, "5m", 1500000000000, is_new_pair=False, candle_type=candle_type)
    assert respair == pair
    assert restf == '5m'
    assert restype == candle_type
    # Call with very old timestamp - causes tons of requests
    assert exchange._api_async.fetch_ohlcv.call_count > 400
    # assert res == ohlcv
    exchange._api_async.fetch_ohlcv.reset_mock()
    _, _, _, res = await exchange._async_get_historic_ohlcv(
        pair, "5m", 1500000000000, is_new_pair=True, candle_type=candle_type)

    # Called twice - one "init" call - and one to get the actual data.
    assert exchange._api_async.fetch_ohlcv.call_count == 2
    assert res == ohlcv
    assert log_has_re(r"Candle-data for ETH/BTC available starting with .*", caplog)


@pytest.mark.parametrize("trading_mode,collateral,config", [
    ("spot", "", {}),
    ("margin", "cross", {"options": {"defaultType": "margin"}}),
    ("futures", "isolated", {"options": {"defaultType": "future"}}),
])
def test__ccxt_config(default_conf, mocker, trading_mode, collateral, config):
    default_conf['trading_mode'] = trading_mode
    default_conf['collateral'] = collateral
    exchange = get_patched_exchange(mocker, default_conf, id="binance")
    assert exchange._ccxt_config == config


@pytest.mark.parametrize('pair,nominal_value,mm_ratio,amt', [
    ("BNB/BUSD", 0.0, 0.025, 0),
    ("BNB/USDT", 100.0, 0.0065, 0),
    ("BTC/USDT", 170.30, 0.004, 0),
    ("BNB/BUSD", 999999.9, 0.1, 27500.0),
    ("BNB/USDT", 5000000.0, 0.15, 233035.0),
    ("BTC/USDT", 300000000.1, 0.5, 99891300.0),
])
def test_get_maintenance_ratio_and_amt_binance(
    default_conf,
    mocker,
    pair,
    nominal_value,
    mm_ratio,
    amt,
):
    exchange = get_patched_exchange(mocker, default_conf, id="binance")
    exchange._leverage_brackets = {
        'BNB/BUSD': [[0.0, 0.025, 0.0],
                     [100000.0, 0.05, 2500.0],
                     [500000.0, 0.1, 27500.0],
                     [1000000.0, 0.15, 77500.0],
                     [2000000.0, 0.25, 277500.0],
                     [5000000.0, 0.5, 1527500.0]],
        'BNB/USDT': [[0.0, 0.0065, 0.0],
                     [10000.0, 0.01, 35.0],
                     [50000.0, 0.02, 535.0],
                     [250000.0, 0.05, 8035.0],
                     [1000000.0, 0.1, 58035.0],
                     [2000000.0, 0.125, 108035.0],
                     [5000000.0, 0.15, 233035.0],
                     [10000000.0, 0.25, 1233035.0]],
        'BTC/USDT': [[0.0, 0.004, 0.0],
                     [50000.0, 0.005, 50.0],
                     [250000.0, 0.01, 1300.0],
                     [1000000.0, 0.025, 16300.0],
                     [5000000.0, 0.05, 141300.0],
                     [20000000.0, 0.1, 1141300.0],
                     [50000000.0, 0.125, 2391300.0],
                     [100000000.0, 0.15, 4891300.0],
                     [200000000.0, 0.25, 24891300.0],
                     [300000000.0, 0.5, 99891300.0]
                     ]
    }
    (result_ratio, result_amt) = exchange.get_maintenance_ratio_and_amt(pair, nominal_value)
    assert (round(result_ratio, 8), round(result_amt, 8)) == (mm_ratio, amt)
