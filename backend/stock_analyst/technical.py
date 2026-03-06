import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import logging

from .config import MARKET_BENCHMARK, RISK_FREE_RATE
from .market_data import get_price_history
from .qlib_engine import (
    bollinger_bands,
    build_snapshot,
    macd,
    moving_average,
    rsi,
    support_resistance,
    volatility_summary,
    volume_summary,
)

logger = logging.getLogger(__name__)

class TechnicalAnalyzer:
    """Technical analysis using price data and indicators"""
    
    def __init__(self):
        self.periods = {
            'short': 20,
            'medium': 50,
            'long': 200
        }
    
    def get_price_data(self, symbol: str, period: str = "1y") -> pd.DataFrame:
        """Fetch historical price data"""
        try:
            data = get_price_history(symbol, period=period, asset_type="stock")
            if data.empty:
                raise ValueError(f"No data found for {symbol}")
            return data
        except Exception as e:
            logger.error(f"Error fetching data for {symbol}: {e}")
            raise
    
    def calculate_moving_averages(self, data: pd.DataFrame) -> Dict[str, float]:
        """Calculate various moving averages"""
        mas = {}
        for name, period in self.periods.items():
            mas[f'MA_{period}'] = moving_average(data['Close'], period)
        
        return mas
    
    def calculate_rsi(self, data: pd.DataFrame, period: int = 14) -> float:
        """Calculate Relative Strength Index"""
        return rsi(data["Close"], period=period)
    
    def calculate_macd(self, data: pd.DataFrame) -> Dict[str, float]:
        """Calculate MACD indicator"""
        return macd(data["Close"])
    
    def calculate_bollinger_bands(self, data: pd.DataFrame, period: int = 20) -> Dict[str, float]:
        """Calculate Bollinger Bands"""
        return bollinger_bands(data["Close"], period=period)
    
    def calculate_volume_indicators(self, data: pd.DataFrame) -> Dict[str, float]:
        """Calculate volume-based indicators"""
        return volume_summary(data)
    
    def calculate_support_resistance(self, data: pd.DataFrame, window: int = 20) -> Dict[str, float]:
        """Identify support and resistance levels"""
        return support_resistance(data, window=window)
    
    def calculate_volatility(self, data: pd.DataFrame, period: int = 20) -> Dict[str, float]:
        """Calculate volatility metrics"""
        return volatility_summary(data, period=period)
    
    def _calculate_atr(self, data: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate Average True Range"""
        high_low = data['High'] - data['Low']
        high_close = np.abs(data['High'] - data['Close'].shift())
        low_close = np.abs(data['Low'] - data['Close'].shift())
        
        true_range = np.maximum(high_low, np.maximum(high_close, low_close))
        atr = true_range.rolling(window=period).mean()
        
        return atr
    
    def get_technical_summary(self, symbol: str) -> Dict:
        """Get comprehensive technical analysis summary"""
        try:
            data = self.get_price_data(symbol)
            
            # Calculate all indicators
            moving_averages = self.calculate_moving_averages(data)
            rsi = self.calculate_rsi(data)
            macd = self.calculate_macd(data)
            bollinger = self.calculate_bollinger_bands(data)
            volume = self.calculate_volume_indicators(data)
            support_resistance = self.calculate_support_resistance(data)
            volatility = self.calculate_volatility(data)
            
            # Generate signals
            current_price = data['Close'].iloc[-1]
            
            # Trend signals
            trend_signal = "NEUTRAL"
            if current_price > moving_averages['MA_50'] > moving_averages['MA_200']:
                trend_signal = "BULLISH"
            elif current_price < moving_averages['MA_50'] < moving_averages['MA_200']:
                trend_signal = "BEARISH"
            
            # Momentum signals
            momentum_signal = "NEUTRAL"
            if rsi < 30:
                momentum_signal = "OVERSOLD"
            elif rsi > 70:
                momentum_signal = "OVERBOUGHT"
            
            # Volume signal
            volume_signal = "NORMAL"
            if volume['volume_ratio'] > 1.5:
                volume_signal = "HIGH"
            elif volume['volume_ratio'] < 0.7:
                volume_signal = "LOW"
            
            return {
                'symbol': symbol,
                'current_price': current_price,
                'moving_averages': moving_averages,
                'rsi': rsi,
                'macd': macd,
                'bollinger_bands': bollinger,
                'volume': volume,
                'support_resistance': support_resistance,
                'volatility': volatility,
                'signals': {
                    'trend': trend_signal,
                    'momentum': momentum_signal,
                    'volume': volume_signal
                },
                'last_updated': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error in technical analysis for {symbol}: {e}")
            return {'error': str(e), 'symbol': symbol}
