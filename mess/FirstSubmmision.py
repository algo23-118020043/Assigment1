# Copyright 2021 Optiver Asia Pacific Pty. Ltd.
#
# This file is part of Ready Trader Go.
#
#     Ready Trader Go is free software: you can redistribute it and/or
#     modify it under the terms of the GNU Affero General Public License
#     as published by the Free Software Foundation, either version 3 of
#     the License, or (at your option) any later version.
#
#     Ready Trader Go is distributed in the hope that it will be useful,
#     but WITHOUT ANY WARRANTY; without even the implied warranty of
#     MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#     GNU Affero General Public License for more details.
#
#     You should have received a copy of the GNU Affero General Public
#     License along with Ready Trader Go.  If not, see
#     <https://www.gnu.org/licenses/>.
import asyncio
import itertools

from typing import List

from ready_trader_go import BaseAutoTrader, Instrument, Lifespan, MAXIMUM_ASK, MINIMUM_BID, Side

# 当前单trader回测$10510
LOT_SIZE = 10
POSITION_LIMIT = 100
TICK_SIZE_IN_CENTS = 100
MIN_BID_NEAREST_TICK = (MINIMUM_BID + TICK_SIZE_IN_CENTS) // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS
MAX_ASK_NEAREST_TICK = MAXIMUM_ASK // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS

OrderDistanceFromBest = 4
ClearPositionPercentage = 0.6
class AutoTrader(BaseAutoTrader):
    """Example Auto-trader.

    When it starts this auto-trader places ten-lot bid and ask orders at the
    current best-bid and best-ask prices respectively. Thereafter, if it has
    a long position (it has bought more lots than it has sold) it reduces its
    bid and ask prices. Conversely, if it has a short position (it has sold
    more lots than it has bought) then it increases its bid and ask prices.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, team_name: str, secret: str):
        """Initialise a new instance of the AutoTrader class."""
        super().__init__(loop, team_name, secret)
        self.order_ids = itertools.count(1)
        self.bids = set()
        self.asks = set()
        self.ask_id = self.ask_price = self.bid_id = self.bid_price = self.position = 0 

        InstrumentOrderbook = {'seq':None,'bidprc':[],'bidqty':[],'askprc':[],'askqty':[]} #中间变量可以忽略, Intermediate variables, can ignore
        # orderbook data, see data structure in data_structure.md
        # predict_orderbook, is to update predict order book data when trade occur, but the prediction maybe wrong since we cannot get cancel data.
        # predict_orderbook中的元素可能出现为空的情况, elements in predict_orderbook maybe None or empty
        self.orderbook = {Instrument.FUTURE:InstrumentOrderbook, Instrument.ETF:InstrumentOrderbook}
        self.predict_orderbook = {Instrument.FUTURE:InstrumentOrderbook, Instrument.ETF:InstrumentOrderbook}

    def on_error_message(self, client_order_id: int, error_message: bytes):
        """Called when the exchange detects an error.

        If the error pertains to a particular order, then the client_order_id
        will identify that order, otherwise the client_order_id will be zero.
        """
        self.logger.warning("error with order %d: %s", client_order_id, error_message.decode())
        if client_order_id != 0 and (client_order_id in self.bids or client_order_id in self.asks):
            self.on_order_status_message(client_order_id, 0, 0, 0)

    def on_hedge_filled_message(self, client_order_id: int, price: int, volume: int):
        """Called when one of your hedge orders is filled.

        The price is the average price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        """
        self.logger.info("received hedge filled for order %d with average price %d and volume %d", client_order_id,
                         price, volume)

    def update_order_book(self, instrument: int, sequence_number: int, ask_prices: List[int],
                                     ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]):
        """Update orderbook data, when orderbook data arrive, Called by func on_order_book_update_message .

        当新的订单数据到达 更新订单数据 无法自动调用
        同时更新我们的预测订单薄数据 防止预测数据于真实数据产生过大的gap
        """
        self.orderbook[instrument] = {'seq':sequence_number,'bidprc':bid_prices,'bidqty':bid_volumes,'askprc':ask_prices,'askqty':ask_volumes}
        self.predict_orderbook[instrument] = {'seq':sequence_number,'bidprc':bid_prices,'bidqty':bid_volumes,'askprc':ask_prices,'askqty':ask_volumes}
 
    def on_order_book_update_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                                     ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]):
        """Called periodically to report the status of an order book.

        The sequence number can be used to detect missed or out-of-order
        messages. The five best available ask (i.e. sell) and bid (i.e. buy)
        prices are reported along with the volume available at each of those
        price levels.
        """
        self.logger.info("received order book for instrument %d with sequence number %d", instrument,
                         sequence_number)
        self.update_order_book(instrument, sequence_number, ask_prices, ask_volumes, bid_prices, bid_volumes)

        # bid/ask下单参数
        more_bid=1 # 当为 1 时，bid只下10单,当为 2 时，再new_bid_price下20单
        more_ask=1 # 当为 1 时，bid只下10单,当为 2 时，再new_ask_price下20单


        if instrument == Instrument.FUTURE:
            #刚开始会出现preict初始化不成功的情况，我们直接跳过这一次数据推送。
            if self.predict_orderbook[Instrument.ETF]['bidprc']==[]: return

        # # 确定数据推送时间，监测挂单次数，监测active vol,防止违规
        # # 阶梯顺序下单，看单边顺序
            if abs(self.position) >= ClearPositionPercentage * POSITION_LIMIT:
                price_adjustment = - ((self.position) // 10) * TICK_SIZE_IN_CENTS #long，为负数
            else:
                price_adjustment = 0
            
            if bid_prices[OrderDistanceFromBest - 1] >= self.predict_orderbook[Instrument.ETF]['bidprc'][0]:
                #如果future 和 ETF价格发生偏移，ETF价格低于future, 此时多买入ETF现货
                more_bid = 2
                if self.position > 50:
                    more_bid = 1
                new_bid_price = self.predict_orderbook[Instrument.ETF]['bidprc'][0] + price_adjustment if bid_prices[0] != 0 else 0
            else:
                new_bid_price = bid_prices[OrderDistanceFromBest - 1] + price_adjustment if bid_prices[0] != 0 else 0

            if ask_prices[OrderDistanceFromBest - 1] <= self.predict_orderbook[Instrument.ETF]['askprc'][0]:
                #如果future 和 ETF价格发生偏移，ETF价格高于future, 此时多卖出ETF现货
                more_ask = 2
                if self.position < -50:
                    more_ask = 1
                new_ask_price = self.predict_orderbook[Instrument.ETF]['askprc'][0] + price_adjustment if bid_prices[0] != 0 else 0
            else:
                new_ask_price = ask_prices[OrderDistanceFromBest - 1] + price_adjustment if bid_prices[0] != 0 else 0
            
            # 如果上一个tick有挂单，且最新要挂的订单价格和旧订单不同，取消之前的订单
            if self.bid_id != 0 and new_bid_price not in (self.bid_price, 0):
                self.send_cancel_order(self.bid_id)
                self.bid_id = 0
            if self.ask_id != 0 and new_ask_price not in (self.ask_price, 0):
                self.send_cancel_order(self.ask_id)
                self.ask_id = 0
            
            # 旧订单已被取消，新订单价格有效（不为0），仓位未超过最大仓位，则按照新价格下订单
            if self.bid_id == 0 and new_bid_price != 0 and self.position < POSITION_LIMIT:
                self.bid_id = next(self.order_ids)
                self.bid_price = new_bid_price
                self.send_insert_order(self.bid_id, Side.BUY, new_bid_price, LOT_SIZE * more_bid, Lifespan.GOOD_FOR_DAY)
                self.bids.add(self.bid_id)

            if self.ask_id == 0 and new_ask_price != 0 and self.position > -POSITION_LIMIT:
                self.ask_id = next(self.order_ids)
                self.ask_price = new_ask_price
                self.send_insert_order(self.ask_id, Side.SELL, new_ask_price, LOT_SIZE * more_ask, Lifespan.GOOD_FOR_DAY)
                self.asks.add(self.ask_id)

    def on_order_filled_message(self, client_order_id: int, price: int, volume: int):
        """Called when one of your orders is filled, partially or fully.

        The price is the price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        """
        self.logger.info("received order filled for order %d with price %d and volume %d", client_order_id,
                         price, volume)
        if client_order_id in self.bids:
            self.position += volume
            self.send_hedge_order(next(self.order_ids), Side.ASK, MIN_BID_NEAREST_TICK, volume)
        elif client_order_id in self.asks:
            self.position -= volume
            self.send_hedge_order(next(self.order_ids), Side.BID, MAX_ASK_NEAREST_TICK, volume)

    def on_order_status_message(self, client_order_id: int, fill_volume: int, remaining_volume: int,
                                fees: int):
        """Called when the status of one of your orders changes.

        The fill_volume is the number of lots already traded, remaining_volume
        is the number of lots yet to be traded and fees is the total fees for
        this order. Remember that you pay fees for being a market taker, but
        you receive fees for being a market maker, so fees can be negative.

        If an order is cancelled its remaining volume will be zero.
        """
        self.logger.info("received order status for order %d with fill volume %d remaining %d and fees %d",
                         client_order_id, fill_volume, remaining_volume, fees)
        if remaining_volume == 0:
            if client_order_id == self.bid_id:
                self.bid_id = 0
            elif client_order_id == self.ask_id:
                self.ask_id = 0

            # It could be either a bid or an ask
            self.bids.discard(client_order_id)
            self.asks.discard(client_order_id)

    def predict_order_book_in_trade(self, instrument: int, sequence_number: int, ask_prices: List[int],
                               ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]):
        """ Predict orderbook, called in func on_trade_ticks_message. 
        用来预测orderbook 在函数on_trade_ticks_message中自动调用
        我本可以在逻辑上让这个函数更严谨，但是这没有意义，可能需要花掉太多时间
        """
        if sum(ask_prices)>0: self.predict_orderbook[instrument]['askprc'] = [max(ask_prices)]
        if sum(bid_prices)>0: self.predict_orderbook[instrument]['bidprc'] = [min(x for x in bid_prices if x>0)]
  

    def on_trade_ticks_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                               ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]):
        """Called periodically when there is trading activity on the market.

        The five best ask (i.e. sell) and bid (i.e. buy) prices at which there
        has been trading activity are reported along with the aggregated volume
        traded at each of those price levels.

        If there are less than five prices on a side, then zeros will appear at
        the end of both the prices and volumes arrays.
        """
        self.logger.info("received trade ticks for instrument %d with sequence number %d", instrument,
                         sequence_number)
        self.predict_order_book_in_trade(instrument, sequence_number, ask_prices,ask_volumes, bid_prices, bid_volumes)
