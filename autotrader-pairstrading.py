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
import time
import math

from typing import List

from ready_trader_go import BaseAutoTrader, Instrument, Lifespan, MAXIMUM_ASK, MINIMUM_BID, Side

"""
conda activate Pyready10L
cd /Users/absolutex/Library/CloudStorage/OneDrive-个人/market/TradingSys/Pyready10L/
python rtg.py run autotrader.py
"""

### hyper-parameter
LOT_SIZE = 50
SIGMOID_C = math.ceil(math.log(LOT_SIZE-1)/-0.9)
POSITION_LIMIT = 100
TICK_SIZE_IN_CENTS = 100
MIN_BID_NEAREST_TICK = (MINIMUM_BID + TICK_SIZE_IN_CENTS) // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS
MAX_ASK_NEAREST_TICK = MAXIMUM_ASK // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS
TIME_NUMBER = 5  # How many periods we look back

OrderDistanceFromBest = 3 # 默认下单的档位，1则在ask1/bid1下单，2则在ask2/bid2下单
ClearPositionPercentage = 0.5 # 超过这个数字乘以最高仓位，我们开始着手准备清仓
trade_memory_num = 40 # 我们记录的交易次数上限

"""
MINIMUM_BID = 1
MAXIMUM_ASK = 2147483647
MinBidNearestBid = 0   always 0
MaxAskNearestTick = 2147483647 // TickSizeInCents**2
"""

"""
/*----- Readme -----*/
Author: AbsoluteX
代码设计请遵循以下原理 目的是确保我们的抢单顺利
1. 优先执行下单等命令
2. 再执行一些记录类 更新类代码
该代码已经更新 将所有的 logger 语句放在了函数最后 且永远在最后
"""

### temp

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
        self.bid_num, self.ask_num = 0, 0
     
        """/*----- self-defined parameters -----*/"""
        ### author: AbsoluteX
        #中间变量可以忽略, Intermediate variables, can ignore
        InstrumentOrderbook = {'seq':None,'bidprc':[],'bidqty':[],'askprc':[],'askqty':[]} 
        # predict_orderbook, is to update predict order book data when trade occur, but the prediction maybe wrong since we cannot get cancel data.
        # predict_orderbook中的元素可能出现为空的情况, elements in predict_orderbook maybe None or empty
        self.orderbook = {Instrument.FUTURE:InstrumentOrderbook, Instrument.ETF:InstrumentOrderbook}
        self.predict_orderbook = {Instrument.FUTURE:InstrumentOrderbook, Instrument.ETF:InstrumentOrderbook}
        # 交易数据，第一个数字代表vwap，第二个数字代表vol，是list的list
        self.trade_history = {Instrument.FUTURE:list(), Instrument.ETF:list()}
        # 是否开启logger的指令
        self.if_logger = False #False代表我们关闭logger

        ### author: Yip
        self.etf_rec, self.future_rec = [0]*TIME_NUMBER, [0]*TIME_NUMBER
        self.spread_std, self.spread_mean, self.spread_count = 0, 0, 0
        self.spread = 0

        ### author: JL
        self.last_order = {
            "Block strategy": {"ask_id":0,"ask_price":0,"bid_id":0,"bid_price":0},
            "ETF strategy": {"ask_id":0,"ask_price":0,"bid_id":0,"bid_price":0}
        }
        ### useless ignore
        self.recv_time = 0

    """
    /*----- utils func -----*/
    some utils func
    """
    def update_variance(self, old_variance, old_mean, n, new_data):   # Author: Yip
        return ((old_variance * n) + ((new_data - old_mean) ** 2)) / (n + 1)
    
    def update_mean(self, old_mean, n, new_data):     # Author: Yip
        return (old_mean * n + new_data) / (n + 1)
    
    def get_int_price(self, price):     # Author: Yip
        return int(price//100*100)

    def sigmoid(self, x):     # Author: Yip
        """input position, return its sigmoid result"""
        return 1/(1+math.exp(-SIGMOID_C*x))

    def on_error_message(self, client_order_id: int, error_message: bytes):
        """Called when the exchange detects an error.
        
        If the error pertains to a particular order, then the client_order_id
        will identify that order, otherwise the client_order_id will be zero.
        如果错误与特定订单有关，则 client_order_id 将标识该订单，否则 client_order_id 将为零。
        """
        if client_order_id != 0 and (client_order_id in self.bids or client_order_id in self.asks):
            self.on_order_status_message(client_order_id, 0, 0, 0)

        # put logger in the last line for time optimization
        if self.if_logger: self.logger.warning("error with order %d: %s",
                                               client_order_id, error_message.decode())

    def on_hedge_filled_message(self, client_order_id: int, price: int, volume: int):
        """Called when one of your hedge orders is filled.
        The price is the average price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        当您的一个对冲订单被执行时调用。价格是订单（部分）成交的平均价格，可能优于订单的限价。 交易量是以该价格成交的手数
        """
        if self.if_logger: self.logger.info("received hedge filled for order %d with average price %d and volume %d",
                                            client_order_id, price, volume)

    def update_order_book(self, instrument: int, sequence_number: int, ask_prices: List[int],
                                     ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]):
        """Author: AbsoluteX
        Update orderbook data, when orderbook data arrive, Called by func on_order_book_update_message .
        当新的订单数据到达 更新订单数据 无法自动调用
        同时更新我们的预测订单薄数据 防止预测数据于真实数据产生过大的gap
        """
        self.orderbook[instrument] = {'seq':sequence_number,'bidprc':bid_prices,'bidqty':bid_volumes,'askprc':ask_prices,'askqty':ask_volumes}
        self.predict_orderbook[instrument] = {'seq':sequence_number,'bidprc':bid_prices,'bidqty':bid_volumes,'askprc':ask_prices,'askqty':ask_volumes}
    
    def cal_weight_avg(self,target):        # Author: Yip
        """
        用orderbook数据 计算下单量加权的价格数据
        使用的是ask1和bid1
        会进行冗余检验
        """
        ask, bid = self.orderbook[target]['askprc'][0], self.orderbook[target]['bidprc'][0]
        ask_vol, bid_vol = self.orderbook[target]['askqty'][0], self.orderbook[target]['bidqty'][0]
        vwap = (ask*ask_vol + bid*bid_vol) / (ask_vol+bid_vol+0.1)
        # roubustness check
        if (vwap>ask)or(vwap<bid): vwap = (ask+bid)/2
        return vwap

    def update_spread_statistics(self, spread):  
        """
        Author: Yip
        更新需要的所有spread信息
        便于判断我们的threshold
        """
        self.spread_std = self.update_variance(self.spread_std**2, self.spread_mean, self.spread_count, spread) ** 0.5  # First update std then mean
        self.spread_mean = self.update_mean(self.spread_mean, self.spread_count, spread)
        self.spread_up, self.spread_down = self.spread_mean + 2*self.spread_std, self.spread_mean - 2*self.spread_std

    def update_past_record(self, instrument):
        """
        更新记录信息
        只有在spread_count >= TIME_NUMBER 程序才会开始记录spread
        """
        ### update simple record
        self.etf_rec.pop(0)  # remove one data and append one data
        self.etf_rec.append(self.cal_weight_avg(Instrument.ETF))
        self.future_rec.pop(0)
        self.future_rec.append(self.cal_weight_avg(Instrument.FUTURE))
        self.spread_count += 1  # record total number of data recorded.
        ### update info of spread
        ### change by the arrive info of instrument
        ### 会根据到达信息所属的品类改变计算spread的方式，为了方便对齐
        if self.spread_count >= 4:  # Ignore the initial time, avoid abnormal data record
            ### 这里用的是常数constant，因为我们第四次肯定是正常信息，这里并不需要调参
            if instrument==Instrument.FUTURE: self.spread = self.etf_rec[-1] - self.future_rec[-2]
            else: self.spread = self.etf_rec[-1] - self.future_rec[-1]
            self.update_spread_statistics(self.spread)
    
    def get_order_num(self):
        """
        Author: Yip
        Adjust order number based on current position
        """
        self.bid_num = int(self.sigmoid(self.position/100)*LOT_SIZE)
        self.ask_num = int((1-self.sigmoid(self.position/100))*LOT_SIZE)
        #print("POSITION!!", self.position, self.bid_num, self.ask_num)

    def ETF_Future_spread_strategy(self, instrument: int, sequence_number: int, ask_prices: List[int],
                                     ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]):
        """ Author: Yip AbsoluteX
        我们的 Futures-ETF 配对交易策略主体
        """
        #刚开始会出现preict初始化不成功的情况，我们直接跳过这一次数据推送。
        if self.predict_orderbook[Instrument.ETF]['bidprc']==[]: return 0, 0
        self.update_past_record(instrument)  # Update spread record and its statistics

        # Initialize, 如果最终价格等于0我们不会下单
        new_ask_price = 0
        new_bid_price = 0
        # 判断ETF与Futures的价差
        ### 这里主要的作用事决定下单的price
        if (self.spread_count >= 2*TIME_NUMBER) and (self.spread > self.spread_up):  # ETF more expensive, short it, more likely to sell
            if self.orderbook[Instrument.ETF]['bidprc'][0] != 0:
                new_ask_price = self.orderbook[Instrument.ETF]['bidprc'][0] + TICK_SIZE_IN_CENTS 
            if self.orderbook[Instrument.FUTURE]['bidprc'][0] != 0:
                new_bid_price = self.orderbook[Instrument.FUTURE]['bidprc'][0] + TICK_SIZE_IN_CENTS
        
        elif (self.spread_count >= 2*TIME_NUMBER) and (self.spread < self.spread_down):  # ETF cheaper, long it, more likely to buy
            if self.orderbook[Instrument.FUTURE]['askprc'][0] != 0:
                new_ask_price = self.orderbook[Instrument.FUTURE]['askprc'][0] - TICK_SIZE_IN_CENTS 
            if self.orderbook[Instrument.ETF]['askprc'][0] != 0:
                new_bid_price = self.orderbook[Instrument.ETF]['askprc'][0] - TICK_SIZE_IN_CENTS 

        else:  # No obvious price gap /data not enough
            if self.orderbook[Instrument.FUTURE]['bidprc'][0] != 0:
                new_bid_price = self.orderbook[Instrument.FUTURE]['bidprc'][OrderDistanceFromBest - 1]
                new_bid_price = min(new_bid_price, self.predict_orderbook[Instrument.ETF]['bidprc'][0])
            if self.orderbook[Instrument.FUTURE]['askprc'][0] != 0:
                new_ask_price = self.orderbook[Instrument.FUTURE]['askprc'][OrderDistanceFromBest - 1]
                new_ask_price = max(new_ask_price, self.predict_orderbook[Instrument.ETF]['askprc'][0])
        
        return new_ask_price, new_bid_price
        
    def send_order(self,strategy, new_ask_price, new_bid_price, ask_num, bid_num):
        """
        Author: JL
        Descript: 弃用self.ask_id,self.ask_price,self.bid_id,self.bid_price, 启用self.last_order字典，记录两个策略的下单情况
        为两个策略各自开了一个字典分别记录如上四个变量
        """
        # 先取消ask&bid单
        ### 取消前会检查下单价格是不是和存在单价格一样
        if self.last_order[strategy]['bid_id'] != 0 and new_bid_price not in (self.last_order[strategy]['bid_price'], 0):
            self.send_cancel_order(self.last_order[strategy]['bid_id'])
            self.last_order[strategy]['bid_id'] = 0
            
        if self.last_order[strategy]['ask_id'] != 0 and new_ask_price not in (self.last_order[strategy]['ask_price'], 0):
            self.send_cancel_order(self.last_order[strategy]['ask_id'])
            self.last_order[strategy]['ask_id'] = 0
        
        if self.last_order[strategy]['bid_id'] == 0 and new_bid_price != 0 and self.position < POSITION_LIMIT:
            self.last_order[strategy]['bid_id'] = next(self.order_ids)
            self.last_order[strategy]['bid_price'] = new_bid_price
            self.send_insert_order(self.last_order[strategy]['bid_id'], Side.BUY, new_bid_price, bid_num, Lifespan.GOOD_FOR_DAY)
            self.bids.add(self.last_order[strategy]['bid_id'])
        
        if self.last_order[strategy]['ask_id'] == 0 and new_ask_price != 0 and self.position > -POSITION_LIMIT:
            self.last_order[strategy]['ask_id'] = next(self.order_ids)
            self.last_order[strategy]['ask_price'] = new_ask_price
            self.send_insert_order(self.last_order[strategy]['ask_id'], Side.SELL, new_ask_price, ask_num, Lifespan.GOOD_FOR_DAY)
            self.asks.add(self.last_order[strategy]['ask_id'])
    
    def huge_order(self, data, bar=0.9):
        """
        Author: Yip
        Description: Detect whether an order in the ask order is extremely huge so that can be regarded as a price wall
        """
        if data == []:
            return False
        return max(data) > bar*sum(data)  # 判断最大的数值是否是其他数值的10倍
    
    def wall_price_strategy(self):
        """
        Anuthor: Yip
        Description: Determine whether there exists a valid wall price. If so, we put orders according to the wall price.
        """
        if self.huge_order(self.orderbook[Instrument.ETF]['askqty']) and self.huge_order(self.orderbook[Instrument.ETF]['bidqty']):
            huge_ask_index = self.orderbook[Instrument.ETF]['askqty'].index(max(self.orderbook[Instrument.ETF]['askqty']))
            huge_bid_index = self.orderbook[Instrument.ETF]['bidqty'].index(max(self.orderbook[Instrument.ETF]['bidqty']))
            new_ask_price = self.orderbook[Instrument.ETF]['askprc'][huge_ask_index] - TICK_SIZE_IN_CENTS if huge_ask_index == 0 else 0
            new_bid_price = self.orderbook[Instrument.ETF]['bidprc'][huge_bid_index] + TICK_SIZE_IN_CENTS if huge_bid_index == 0 else 0
            return new_ask_price, new_bid_price
        return 0, 0

    def on_order_book_update_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                                     ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]):
        """Called periodically to report the status of an order book.
        The sequence number can be used to detect missed or out-of-order
        messages. The five best available ask (i.e. sell) and bid (i.e. buy)
        prices are reported along with the volume available at each of those
        price levels.
        定期调用报告订单薄的状态
        """
        #print('time from last recv:',str((time.time()-self.recv_time)*1000)[:5],'ms')
        self.recv_time = time.time()
        #if instrument == Instrument.FUTURE: return
        self.update_order_book(instrument, sequence_number, ask_prices, ask_volumes, bid_prices, bid_volumes)
        #if instrument == Instrument.FUTURE:
        if instrument == Instrument.ETF:
            self.block = False
            self.get_order_num()  # Update the order number we will put, according to current position
            ask_prc1, bid_prc1 = self.wall_price_strategy()
            if ask_prc1 or bid_prc1:
                # self.send_order(ask_prc1, bid_prc1, math.ceil(self.ask_num/2), math.ceil(self.bid_num/2))
                self.send_order('Block strategy',ask_prc1, bid_prc1, self.ask_num, self.bid_num)
                #print("Block strategy:", ask_prc1, bid_prc1, self.ask_num, self.bid_num)
            else:
                ask_prc2, bid_prc2 = self.ETF_Future_spread_strategy(instrument, sequence_number, ask_prices, ask_volumes, bid_prices, bid_volumes)
                self.send_order('ETF strategy',ask_prc2, bid_prc2, self.ask_num, self.bid_num)
                #print("ETF strategy:", ask_prc2, bid_prc2, self.ask_num, self.bid_num)

        if self.if_logger: self.logger.info("received order book for instrument %d with sequence number %d", 
                                            instrument,sequence_number)
        

    def on_order_filled_message(self, client_order_id: int, price: int, volume: int):
        """Called when one of your orders is filled, partially or fully.
        The price is the price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        当您的一个订单被部分或全部成交时调用。
        """
        if client_order_id in self.bids:
            self.position += volume
            self.send_hedge_order(next(self.order_ids), Side.ASK, MIN_BID_NEAREST_TICK, volume)
        elif client_order_id in self.asks:
            self.position -= volume
            self.send_hedge_order(next(self.order_ids), Side.BID, MAX_ASK_NEAREST_TICK, volume)
        
        # put logger in the last line for time optimization
        if self.if_logger: self.logger.info("received order filled for order %d with price %d and volume %d",
                                            client_order_id, price, volume)
    
    def on_order_status_message(self, client_order_id: int, fill_volume: int, remaining_volume: int,
                                fees: int):
        """Called when the status of one of your orders changes.
        The fill_volume is the number of lots already traded, remaining_volume
        is the number of lots yet to be traded and fees is the total fees for
        this order. Remember that you pay fees for being a market taker, but
        you receive fees for being a market maker, so fees can be negative.
        If an order is cancelled its remaining volume will be zero.
        当您的一个订单的状态发生变化时调用。
        """
        if remaining_volume == 0:
            if client_order_id == self.bid_id:
                self.bid_id = 0
            elif client_order_id == self.ask_id:
                self.ask_id = 0

            # It could be either a bid or an ask
            self.bids.discard(client_order_id)
            self.asks.discard(client_order_id)
        
        # put logger in the last line for time optimization
        if self.if_logger: self.logger.info("received order status for order %d with fill volume %d remaining %d and fees %d",
                                            client_order_id, fill_volume, remaining_volume, fees)

    def predict_order_book_in_trade(self, instrument: int, sequence_number: int, ask_prices: List[int],
                                    ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]):
        """ Predict orderbook, called in func on_trade_ticks_message. 
        用来预测orderbook 在函数on_trade_ticks_message中自动调用
        我本可以在逻辑上让这个函数更严谨，但是这没有意义，可能需要花掉太多时间
        """
        if sum(ask_prices)>0: self.predict_orderbook[instrument]['askprc'] = [max(ask_prices)]
        if sum(bid_prices)>0: self.predict_orderbook[instrument]['bidprc'] = [min(x for x in bid_prices if x>0)]

    def cal_trade_each(self,prc,vol):
        """ author: AbsoluteX
        用来计算vwap和vol
        """
        quan = sum(vol)
        vwap=0
        for i in range(len(prc)): vwap += (prc[i]*vol[i])
        return vwap/quan, quan
    
    def update_trade_history(self, instrument: int, sequence_number: int, ask_prices: List[int],
                                    ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]):
        """
        用来记录成交信息 
        每当最新的交易信息到达时 我们会调用该函数
        
        会自动删除 trade_memory_num 次数之外的交易信息
        """
        vwap,quan = self.cal_trade_each(ask_prices+bid_prices,ask_volumes+bid_volumes)
        self.trade_history[instrument].append([vwap,quan])
        self.trade_history[instrument] = self.trade_history[instrument][-trade_memory_num:]
        
    def on_trade_ticks_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                               ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]):
        """Called periodically when there is trading activity on the market.
        The five best ask (i.e. sell) and bid (i.e. buy) prices at which there
        has been trading activity are reported along with the aggregated volume
        traded at each of those price levels.
        If there are less than five prices on a side, then zeros will appear at
        the end of both the prices and volumes arrays.
        当市场上有交易活动时定期调用。
        """
        # 如果我们跟随着trade数据去更新订单 会不会操作超过上限？尤其是competitve env的时候 或者碰上傻逼的时候
        # print("Tick!", instrument, sequence_number, ask_prices, ask_volumes, bid_prices, bid_volumes)
        self.predict_order_book_in_trade(instrument, sequence_number, ask_prices,ask_volumes, bid_prices, bid_volumes)
        self.update_trade_history(instrument, sequence_number, ask_prices,ask_volumes, bid_prices, bid_volumes)

        # put logger in the last line for time optimization
        if self.if_logger: self.logger.info("received trade ticks for instrument %d with sequence number %d",
                                            instrument, sequence_number)