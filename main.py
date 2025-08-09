import asyncio
import sqlite3
import time
import random
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass
from datetime import datetime, timedelta
import io

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# ==================== НАСТРОЙКИ ====================
API_TOKEN = '8027247395:AAHTL7r14EZ_S8D8muT0EyMXuaJuSjXRZcI'
bot = Bot(token=API_TOKEN)
dp = Dispatcher()
START_BALANCE = 10000.0
PRICE_UPDATE_INTERVAL = 120  # 2 минуты
DIVIDEND_INTERVAL = 300     # 5 минут

# ==================== МОДЕЛИ ДАННЫХ ====================
@dataclass
class StockInfo:
    ticker: str
    name: str
    base_price: float
    volatility: float
    dividend_yield: float  # Годовая дивидендная доходность (%)

@dataclass
class PortfolioItem:
    ticker: str
    quantity: int
    avg_buy_price: Optional[float]

@dataclass
class Transaction:
    ticker: str
    quantity: int
    price: float
    timestamp: str

@dataclass
class UserInfo:
    user_id: int
    username: str
    balance: float

@dataclass
class Alert:
    id: int
    user_id: int
    ticker: str
    condition: str  # ">" или "<"
    target_price: float
    triggered: bool

@dataclass
class DividendPayment:
    id: int
    user_id: int
    ticker: str
    quantity: int
    amount: float
    payment_date: str

# ==================== БАЗА ДАННЫХ ====================
class Database:
    def __init__(self, db_path: str = 'trader.db'):
        self.db_path = db_path
        self.init_db()
    
    def get_connection(self):
        return sqlite3.connect(self.db_path)
    
    def init_db(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Создание таблиц
            cursor.executescript('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    balance REAL DEFAULT 10000.0
                );
                
                CREATE TABLE IF NOT EXISTS portfolios (
                    user_id INTEGER,
                    ticker TEXT,
                    quantity INTEGER DEFAULT 0,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                );
                
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    ticker TEXT,
                    quantity INTEGER,
                    price REAL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                );
                
                CREATE TABLE IF NOT EXISTS notified_users (
                    user_id INTEGER PRIMARY KEY,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                );
                
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    ticker TEXT,
                    condition TEXT, -- ">" или "<"
                    target_price REAL,
                    triggered BOOLEAN DEFAULT FALSE,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                );
                
                CREATE TABLE IF NOT EXISTS price_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT,
                    open_price REAL,
                    high_price REAL,
                    low_price REAL,
                    close_price REAL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE TABLE IF NOT EXISTS dividend_payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    ticker TEXT,
                    quantity INTEGER,
                    amount REAL,
                    payment_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                );
                
                CREATE INDEX IF NOT EXISTS idx_transactions_user_ticker 
                ON transactions(user_id, ticker);
                
                CREATE INDEX IF NOT EXISTS idx_transactions_user 
                ON transactions(user_id);
                
                CREATE INDEX IF NOT EXISTS idx_portfolios_user 
                ON portfolios(user_id);
                
                CREATE INDEX IF NOT EXISTS idx_alerts_user 
                ON alerts(user_id);
                
                CREATE INDEX IF NOT EXISTS idx_price_history_ticker 
                ON price_history(ticker);
                
                CREATE INDEX IF NOT EXISTS idx_price_history_timestamp 
                ON price_history(timestamp);
                
                CREATE INDEX IF NOT EXISTS idx_dividend_payments_user 
                ON dividend_payments(user_id);
                
                CREATE INDEX IF NOT EXISTS idx_dividend_payments_ticker 
                ON dividend_payments(ticker);
            ''')
            
            # Убедимся, что все существующие пользователи в notified_users
            cursor.execute('''
                INSERT OR IGNORE INTO notified_users (user_id)
                SELECT user_id FROM users;
            ''')
    
    def create_user(self, user_id: int, username: str) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    'INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)',
                    (user_id, username)
                )
                cursor.execute(
                    'INSERT OR IGNORE INTO notified_users (user_id) VALUES (?)',
                    (user_id,)
                )
                return cursor.rowcount > 0
            except sqlite3.Error:
                return False
    
    def get_user_balance(self, user_id: int) -> float:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
            result = cursor.fetchone()
            return result[0] if result else 0.0
    
    def update_balance(self, user_id: int, amount: float) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    'UPDATE users SET balance = balance + ? WHERE user_id = ?',
                    (amount, user_id)
                )
                return cursor.rowcount > 0
            except sqlite3.Error:
                return False
    
    def update_stock(self, user_id: int, ticker: str, quantity: int) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    'INSERT OR IGNORE INTO portfolios (user_id, ticker) VALUES (?, ?)',
                    (user_id, ticker)
                )
                cursor.execute('''
                    UPDATE portfolios 
                    SET quantity = quantity + ? 
                    WHERE user_id = ? AND ticker = ?
                ''', (quantity, user_id, ticker))
                return True
            except sqlite3.Error:
                return False
    
    def get_user_portfolio(self, user_id: int) -> Dict[str, int]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT ticker, quantity FROM portfolios WHERE user_id = ? AND quantity > 0',
                (user_id,)
            )
            return dict(cursor.fetchall())
    
    def get_user_stock_quantity(self, user_id: int, ticker: str) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT quantity FROM portfolios WHERE user_id = ? AND ticker = ?',
                (user_id, ticker)
            )
            result = cursor.fetchone()
            return result[0] if result else 0
    
    def log_transaction(self, user_id: int, ticker: str, quantity: int, price: float) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute('''
                    INSERT INTO transactions (user_id, ticker, quantity, price)
                    VALUES (?, ?, ?, ?)
                ''', (user_id, ticker, quantity, price))
                return True
            except sqlite3.Error:
                return False
    
    def get_average_buy_price(self, user_id: int, ticker: str) -> Optional[float]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT quantity, price 
                FROM transactions 
                WHERE user_id = ? AND ticker = ? AND quantity > 0
            ''', (user_id, ticker))
            
            transactions = cursor.fetchall()
            if not transactions:
                return None
            
            total_quantity = sum(qty for qty, _ in transactions)
            total_cost = sum(qty * price for qty, price in transactions)
            
            return round(total_cost / total_quantity, 4) if total_quantity > 0 else None
    
    def get_user_transaction_history(self, user_id: int, limit: int = 15) -> List[Transaction]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT ticker, quantity, price, timestamp 
                FROM transactions 
                WHERE user_id = ? 
                ORDER BY timestamp DESC 
                LIMIT ?
            ''', (user_id, limit))
            
            return [
                Transaction(ticker, quantity, price, timestamp)
                for ticker, quantity, price, timestamp in cursor.fetchall()
            ]
    
    def get_all_users(self) -> List[UserInfo]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT user_id, username, balance FROM users')
            return [
                UserInfo(user_id, username, balance)
                for user_id, username, balance in cursor.fetchall()
            ]
    
    def get_notified_users(self) -> List[int]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT user_id FROM notified_users')
            return [row[0] for row in cursor.fetchall()]
            
    def add_alert(self, user_id: int, ticker: str, condition: str, target_price: float) -> bool:
        """Добавляет новый триггер"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute('''
                    INSERT INTO alerts (user_id, ticker, condition, target_price)
                    VALUES (?, ?, ?, ?)
                ''', (user_id, ticker.upper(), condition, target_price))
                conn.commit()
                return True
            except sqlite3.Error as e:
                print(f"Ошибка добавления триггера: {e}")
                return False

    def get_active_alerts(self) -> List[Alert]:
        """Получает все активные (не сработавшие) триггеры"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id, user_id, ticker, condition, target_price FROM alerts WHERE triggered = FALSE')
            rows = cursor.fetchall()
            return [
                Alert(
                    id=row[0],
                    user_id=row[1],
                    ticker=row[2],
                    condition=row[3],
                    target_price=row[4],
                    triggered=False
                )
                for row in rows
            ]

    def mark_alert_as_triggered(self, alert_id: int) -> bool:
        """Помечает триггер как сработавший"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute('UPDATE alerts SET triggered = TRUE WHERE id = ?', (alert_id,))
                conn.commit()
                return True
            except sqlite3.Error as e:
                print(f"Ошибка обновления триггера: {e}")
                return False
                
    def save_price_history(self, ticker: str, open_price: float, high_price: float, low_price: float, close_price: float) -> bool:
        """Сохраняет историю свечей"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute('''
                    INSERT INTO price_history (ticker, open_price, high_price, low_price, close_price)
                    VALUES (?, ?, ?, ?, ?)
                ''', (ticker.upper(), open_price, high_price, low_price, close_price))
                conn.commit()
                return True
            except sqlite3.Error as e:
                print(f"Ошибка сохранения истории свечей: {e}")
                return False
                
    def get_candlestick_data(self, ticker: str, days: int = 7) -> List[Tuple]:
        """Получает историю свечей за указанное количество дней"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT open_price, high_price, low_price, close_price, timestamp
                FROM price_history
                WHERE ticker = ? AND timestamp >= datetime('now', '-{} days')
                ORDER BY timestamp ASC
            '''.format(days), (ticker.upper(),))
            
            return cursor.fetchall()
            
    def pay_dividends(self, user_id: int, ticker: str, quantity: int, amount: float) -> bool:
        """Выплачивает дивиденды пользователю"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute('''
                    INSERT INTO dividend_payments (user_id, ticker, quantity, amount)
                    VALUES (?, ?, ?, ?)
                ''', (user_id, ticker.upper(), quantity, amount))
                
                # Обновляем баланс пользователя
                cursor.execute('''
                    UPDATE users SET balance = balance + ? WHERE user_id = ?
                ''', (amount, user_id))
                
                conn.commit()
                return True
            except sqlite3.Error as e:
                print(f"Ошибка выплаты дивидендов: {e}")
                return False
                
    def get_user_dividend_history(self, user_id: int, limit: int = 10) -> List[DividendPayment]:
        """Получает историю выплат дивидендов пользователя"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT ticker, quantity, amount, payment_date
                FROM dividend_payments
                WHERE user_id = ?
                ORDER BY payment_date DESC
                LIMIT ?
            ''', (user_id, limit))
            
            return [
                DividendPayment(0, user_id, ticker, quantity, amount, payment_date)
                for ticker, quantity, amount, payment_date in cursor.fetchall()
            ]
            
    def get_total_dividends_received(self, user_id: int) -> float:
        """Получает общую сумму полученных дивидендов"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT SUM(amount)
                FROM dividend_payments
                WHERE user_id = ?
            ''', (user_id,))
            
            result = cursor.fetchone()
            return result[0] if result[0] else 0.0

# ==================== ФИКСИРОВАННЫЕ ДАННЫЕ С МОСБИРЖИ ====================
MOEX_STOCKS: Dict[str, StockInfo] = {
    ticker: StockInfo(ticker, data['name'], data['base_price'], data['volatility'], data['dividend_yield'])
    for ticker, data in {
        'AFLT': {'name': 'Аэрофлот', 'base_price': 61.27, 'volatility': 0.03, 'dividend_yield': 8.5},
        'AFKS': {'name': 'Система АО', 'base_price': 16.202, 'volatility': 0.03, 'dividend_yield': 12.0},
        'ALRS': {'name': 'АЛРОСА АО', 'base_price': 47.22, 'volatility': 0.03, 'dividend_yield': 7.2},
        'BANE': {'name': 'Башнефть АО', 'base_price': 1748.50, 'volatility': 0.03, 'dividend_yield': 15.8},
        'DVEC': {'name': 'Дальэнергосбыт', 'base_price': 2.115, 'volatility': 0.03, 'dividend_yield': 11.3},
        'ELMT': {'name': 'Элемент', 'base_price': 0.14105, 'volatility': 0.03, 'dividend_yield': 0.0},
        'ENPG': {'name': 'ЭН+ГРУП АО', 'base_price': 473.1, 'volatility': 0.03, 'dividend_yield': 6.8},
        'FEES': {'name': 'ФосАгро АО', 'base_price': 0.0698, 'volatility': 0.03, 'dividend_yield': 9.1},
        'FIVE': {'name': 'Пятёрочка АО', 'base_price': 1750.00, 'volatility': 0.03, 'dividend_yield': 7.5},
        'FIXR': {'name': 'Фикс Прайс', 'base_price': 1246.6, 'volatility': 0.03, 'dividend_yield': 0.0},
        'FLOT': {'name': 'Совкомфлот', 'base_price': 84.46, 'volatility': 0.03, 'dividend_yield': 8.2},
        'GAZA': {'name': 'ГАЗ АО', 'base_price': 619.0, 'volatility': 0.03, 'dividend_yield': 6.9},
        'GAZP': {'name': 'ГАЗПРОМ АО', 'base_price': 134.24, 'volatility': 0.03, 'dividend_yield': 18.7},
        'HYDR': {'name': 'РусГидро АО', 'base_price': 0.88, 'volatility': 0.03, 'dividend_yield': 14.2},
        'IRAO': {'name': 'Интер РАО ЕЭС АО', 'base_price': 3.75, 'volatility': 0.03, 'dividend_yield': 13.5},
        'KLVZ': {'name': 'Кристалл', 'base_price': 3.608, 'volatility': 0.03, 'dividend_yield': 0.0},
        'KOGK': {'name': 'Когалымнефтегаз', 'base_price': 37800.0, 'volatility': 0.03, 'dividend_yield': 16.4},
        'LENT': {'name': 'Лента АО', 'base_price': 1701.50, 'volatility': 0.03, 'dividend_yield': 0.0},
        'LKOH': {'name': 'ЛУКОЙЛ АО', 'base_price': 6202.5, 'volatility': 0.03, 'dividend_yield': 9.8},
        'MAGN': {'name': 'Магнитогорский МК АО', 'base_price': 312.00, 'volatility': 0.03, 'dividend_yield': 11.7},
        'MGNT': {'name': 'Магнит АО', 'base_price': 6800.00, 'volatility': 0.03, 'dividend_yield': 8.9},
        'MOEX': {'name': 'Московская Биржа АО', 'base_price': 180.46, 'volatility': 0.03, 'dividend_yield': 7.1},
        'MRKK': {'name': 'Группа Мир', 'base_price': 16.64, 'volatility': 0.03, 'dividend_yield': 0.0},
        'MTLR': {'name': 'Мечел АО', 'base_price': 58.70, 'volatility': 0.03, 'dividend_yield': 0.0},
        'MTSS': {'name': 'МТС-АО', 'base_price': 217.05, 'volatility': 0.03, 'dividend_yield': 10.3},
        'NLMK': {'name': 'НЛМК АО', 'base_price': 116.9, 'volatility': 0.03, 'dividend_yield': 12.4},
        'NVTK': {'name': 'Новатэк АО', 'base_price': 1138.00, 'volatility': 0.03, 'dividend_yield': 13.6},
        'OZON': {'name': 'Ozon', 'base_price': 4435.0, 'volatility': 0.03, 'dividend_yield': 0.0},
        'PAZA': {'name': 'ПавлАвт АО', 'base_price': 9580.0, 'volatility': 0.03, 'dividend_yield': 0.0},
        'PHOR': {'name': 'ФосАгро АО', 'base_price': 6805.0, 'volatility': 0.03, 'dividend_yield': 9.1},
        'PIKK': {'name': 'ПИК АО', 'base_price': 636.90, 'volatility': 0.03, 'dividend_yield': 0.0},
        'PLZL': {'name': 'Полюс АО', 'base_price': 12500.00, 'volatility': 0.03, 'dividend_yield': 0.0},
        'POLY': {'name': 'Полиметалл АО', 'base_price': 12500.00, 'volatility': 0.03, 'dividend_yield': 0.0},
        'PRMD': {'name': 'ПРОМОМЕД', 'base_price': 418.0, 'volatility': 0.03, 'dividend_yield': 0.0},
        'QIWI': {'name': 'QIWI', 'base_price': 238.4, 'volatility': 0.03, 'dividend_yield': 0.0},
        'RASP': {'name': 'Распадская', 'base_price': 223.2, 'volatility': 0.03, 'dividend_yield': 0.0},
        'RGSS': {'name': 'РГС СК АО', 'base_price': 0.2344, 'volatility': 0.03, 'dividend_yield': 10.8},
        'ROSN': {'name': 'Роснефть', 'base_price': 445.05, 'volatility': 0.03, 'dividend_yield': 17.2},
        'RUAL': {'name': 'РУСАЛ ОК МКПАО АО', 'base_price': 32.71, 'volatility': 0.03, 'dividend_yield': 0.0},
        'RUSI': {'name': 'ИКРУСС-ИНВ', 'base_price': 65.2, 'volatility': 0.03, 'dividend_yield': 0.0},
        'SARE': {'name': 'СаратЭн-АО', 'base_price': 0.444, 'volatility': 0.03, 'dividend_yield': 0.0},
        'SELG': {'name': 'Селигдар АО', 'base_price': 48.3, 'volatility': 0.03, 'dividend_yield': 0.0},
        'SFIN': {'name': 'ЭсЭфАй АО', 'base_price': 1246.6, 'volatility': 0.03, 'dividend_yield': 0.0},
        'SGZH': {'name': 'Сегежа', 'base_price': 1.539, 'volatility': 0.03, 'dividend_yield': 0.0},
        'SIBN': {'name': 'Газпрнефть', 'base_price': 529.3, 'volatility': 0.03, 'dividend_yield': 11.9},
        'SNGS': {'name': 'Сургутнефтегаз АО', 'base_price': 22.785, 'volatility': 0.03, 'dividend_yield': 19.3},
        'TATN': {'name': 'Татнефть АО', 'base_price': 320.40, 'volatility': 0.03, 'dividend_yield': 14.7},
        'TCSG': {'name': 'Тинькофф Банк АО', 'base_price': 2750.00, 'volatility': 0.03, 'dividend_yield': 0.0},
        'TGKA': {'name': 'ТГК-1 АО', 'base_price': 0.006468, 'volatility': 0.03, 'dividend_yield': 0.0},
        'UPRO': {'name': 'Юнипро', 'base_price': 1.669, 'volatility': 0.03, 'dividend_yield': 12.8},
        'VKCO': {'name': 'VK АО', 'base_price': 4435.0, 'volatility': 0.03, 'dividend_yield': 0.0},
        'VTBR': {'name': 'Банк ВТБ АО', 'base_price': 0.07972, 'volatility': 0.03, 'dividend_yield': 16.1},
        'WUSH': {'name': 'ВУШ Холдинг', 'base_price': 141.83, 'volatility': 0.03, 'dividend_yield': 0.0},
        'YNDX': {'name': 'Yandex clA', 'base_price': 2450.00, 'volatility': 0.03, 'dividend_yield': 0.0},
        'SBER': {'name': 'Сбербанк АО', 'base_price': 313.43, 'volatility': 0.03, 'dividend_yield': 13.2},
    }.items()
}

# ==================== МЕНЕДЖЕР ЦЕН ====================
class PriceManager:
    def __init__(self, db: Database):
        self.db = db
        self.current_prices: Dict[str, float] = {}
        self.last_update: float = 0
        self._initialize_prices()
        # Принудительное первое обновление
        self.update_prices()
    
    def _initialize_prices(self):
        """Инициализация начальных цен с учетом волатильности"""
        print("🔄 Инициализируем стартовые цены с учетом волатильности...")
        for ticker, stock in MOEX_STOCKS.items():
            # Генерируем начальное отклонение
            change_percent = random.uniform(-stock.volatility, stock.volatility)
            initial_price = stock.base_price * (1 + change_percent)
            self.current_prices[ticker] = round(initial_price, 4)
        self.last_update = time.time()
        print(f"✅ Стартовые цены инициализированы для {len(self.current_prices)} акций")
    
    def update_prices(self) -> bool:
        """Обновление цен с учетом волатильности"""
        current_time = time.time()
        if current_time - self.last_update < PRICE_UPDATE_INTERVAL:
            return False
        
        print("🔄 Обновляем симулированные цены...")
        for ticker, stock in MOEX_STOCKS.items():
            # Генерируем случайное изменение цены
            change_percent = random.uniform(-stock.volatility, stock.volatility)
            new_price = stock.base_price * (1 + change_percent)
            
            # Сохраняем историю свечей
            if ticker in self.current_prices:
                # Если есть предыдущая цена, используем её как цену открытия
                open_price = self.current_prices[ticker]
                high_price = max(open_price, new_price)
                low_price = min(open_price, new_price)
                close_price = new_price
            else:
                # Если нет предыдущей цены, используем base_price
                open_price = stock.base_price
                high_price = new_price
                low_price = new_price
                close_price = new_price
            
            # Сохраняем в историю
            self.db.save_price_history(ticker, open_price, high_price, low_price, close_price)
            
            # Обновляем текущие цены
            self.current_prices[ticker] = close_price
        
        self.last_update = current_time
        print(f"✅ Цены обновлены для {len(self.current_prices)} акций")
        return True
    
    def get_price(self, ticker: str) -> Optional[float]:
        """Получение текущей цены акции"""
        return self.current_prices.get(ticker.upper())
    
    def get_stock_info(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Получение полной информации об акции"""
        price = self.get_price(ticker)
        if price is None:
            return None
        
        stock = MOEX_STOCKS.get(ticker.upper())
        if not stock:
            return None
        
        return {
            'name': stock.name,
            'price': price,
            'dividend_yield': stock.dividend_yield
        }
    
    def get_time_until_next_update(self) -> str:
        """Возвращает строку с оставшимся временем до следующего обновления"""
        current_time = time.time()
        time_passed = current_time - self.last_update
        time_left = max(0, PRICE_UPDATE_INTERVAL - time_passed)
        
        minutes = int(time_left // 60)
        seconds = int(time_left % 60)
        return f"{minutes} мин {seconds} сек"

# ==================== ФОРМАТТЕР ====================
class Formatter:
    @staticmethod
    def price(value: Optional[float]) -> str:
        """Форматирует цену с динамическим количеством знаков"""
        if value is None:
            return "N/A"
        return f"{value:.4f}" if abs(value) < 1 else f"{value:.2f}"
    
    @staticmethod
    def value(value: Optional[float]) -> str:
        """Форматирует денежное значение"""
        if value is None:
            return "N/A"
        return f"{value:.0f}"
    
    @staticmethod
    def percent(value: Optional[float]) -> str:
        """Форматирует процент"""
        if value is None:
            return "N/A"
        return f"{value:+.1f}"
        
    @staticmethod
    def dividend_yield(value: Optional[float]) -> str:
        """Форматирует дивидендную доходность"""
        if value is None or value == 0:
            return "Нет"
        return f"{value:.1f}%"

# ==================== СЕРВИСНЫЙ СЛОЙ ====================
class TradingService:
    def __init__(self, db: Database, price_manager: PriceManager):
        self.db = db
        self.price_manager = price_manager
    
    def get_user_portfolio_items(self, user_id: int) -> List[PortfolioItem]:
        """Получение расширенной информации о портфеле пользователя"""
        portfolio_data = self.db.get_user_portfolio(user_id)
        items = []
        
        for ticker, quantity in portfolio_data.items():
            if quantity > 0:
                avg_price = self.db.get_average_buy_price(user_id, ticker)
                items.append(PortfolioItem(ticker, quantity, avg_price))
        
        return items
    
    def calculate_portfolio_profit(self, items: List[PortfolioItem]) -> Tuple[float, float]:
        """Рассчитывает прибыль от акций в портфеле"""
        total_value = 0.0
        total_investment = 0.0
        
        for item in items:
            current_price = self.price_manager.get_price(item.ticker)
            if current_price is not None:
                total_value += current_price * item.quantity
                if item.avg_buy_price is not None:
                    total_investment += item.avg_buy_price * item.quantity
        
        profit = total_value - total_investment
        profit_percent = (profit / total_investment * 100) if total_investment > 0 else 0.0
        
        return profit, profit_percent

# ==================== ГЛОБАЛЬНЫЕ ОБЪЕКТЫ ====================
db = Database()
price_manager = PriceManager(db)
trading_service = TradingService(db, price_manager)
formatter = Formatter()

# ==================== КОМАНДЫ БОТА ====================
@dp.message(Command("start"))
async def start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.full_name or message.from_user.username or str(user_id)
    db.create_user(user_id, username)
    
    await message.answer("""🎉 Добро пожаловать в Виртуальный Трейдер РФ!
💰 Твой стартовый капитал: 10000₽

📈 Доступные команды:
/portfolio - Мой портфель (с ценами покупки и % изменения)
/buy [тикер] [кол-во] - Купить акции
/sell [тикер] [кол-во] - Продать акции
/price [тикер] - Цена акции + график
/list - Список доступных акций
/history - История сделок
/dividends - История дивидендов
/top - Рейтинг игроков
/alert [тикер] [> или <] [цена] - Установить триггер
/help - Помощь""")

@dp.message(Command("help"))
async def help_command(message: types.Message):
    await message.answer("""🤖 Команды бота:
/start - Начать игру
/portfolio - Показать портфель (с ценами покупки и % изменения)
/buy [тикер] [кол-во] - Купить акции
/sell [тикер] [кол-во] - Продать акции
/price [тикер] - Узнать цену акции + график
/list - Список всех доступных акций
/history - История сделок
/dividends - История дивидендов
/top - Топ игроков
/alert [тикер] [> или <] [цена] - Установить триггер

📈 Примеры:
/buy SBER 10 - купить 10 акций Сбербанка
/price GAZP - узнать цену Газпрома + график
/sell LKOH 5 - продать 5 акций Лукойла
/alert SBER > 320 - уведомить, когда SBER будет выше 320₽""")

@dp.message(Command("list"))
async def list_stocks(message: types.Message):
    # Обновляем цены если нужно
    price_manager.update_prices()
    
    sorted_tickers = sorted(MOEX_STOCKS.keys())[:30]
    
    response = "📊 Доступные акции:\n\n"
    for ticker in sorted_tickers:
        stock = MOEX_STOCKS[ticker]
        current_price = price_manager.get_price(ticker)
        price_str = formatter.price(current_price)
        dividend_str = formatter.dividend_yield(stock.dividend_yield)
        response += f"• {ticker}: {price_str}₽ ({stock.name})\n"
        response += f"  💵 Дивиденды: {dividend_str}\n\n"
    
    if len(MOEX_STOCKS) > 30:
        response += f"\n... и ещё {len(MOEX_STOCKS) - 30} акций"
        response += "\nВведи /price [тикер] для конкретной акции"
    
    time_left = price_manager.get_time_until_next_update()
    response += f"\n⏱️ Следующее обновление цен через: {time_left}"
    
    await message.answer(response)

@dp.message(Command("portfolio"))
async def portfolio(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.full_name or str(user_id)
    db.create_user(user_id, username)
    
    # Обновляем цены если нужно
    price_manager.update_prices()
    
    balance = db.get_user_balance(user_id)
    portfolio_items = trading_service.get_user_portfolio_items(user_id)
    
    response = f"📊 Твой портфель:\n💰 Баланс: {formatter.value(balance)}₽\n\n"
    
    if portfolio_items:
        total_dividend_income = db.get_total_dividends_received(user_id)
        response += f"💰 Всего дивидендов получено: {formatter.value(total_dividend_income)}₽\n\n"
        
        for item in portfolio_items:
            stock_info = price_manager.get_stock_info(item.ticker)
            if stock_info:
                current_price = stock_info['price']
                value = current_price * item.quantity
                
                # Вычисляем процент изменения
                percent_change = "N/A"
                if item.avg_buy_price is not None and item.avg_buy_price > 0:
                    percent_change = ((current_price - item.avg_buy_price) / item.avg_buy_price) * 100
                    percent_change = formatter.percent(percent_change)
                
                response += f"• {item.ticker}: {item.quantity} шт ({percent_change}%)\n"
                response += f"  💵 Цена входа в акцию: {formatter.price(item.avg_buy_price)}₽\n"
                response += f"  📈 Текущая цена акции: {formatter.price(current_price)}₽ = {formatter.value(value)}₽ ({stock_info['name']})\n"
                response += f"  💰 Дивиденды: {formatter.dividend_yield(stock_info['dividend_yield'])}\n\n"
            else:
                response += f"• {item.ticker}: {item.quantity} шт (цена недоступна)\n"
        
        # Рассчитываем общую прибыль
        profit, profit_percent = trading_service.calculate_portfolio_profit(portfolio_items)
        total_value = balance + sum(
            (price_manager.get_price(item.ticker) or 0) * item.quantity 
            for item in portfolio_items
        )
        
        response += f"📈 Общая стоимость: {formatter.value(total_value)}₽"
        response += f"\n📊 Прибыль от акций: {formatter.value(profit)}₽ ({formatter.percent(profit_percent)}%)"
    else:
        response += "Пока нет акций 📉"
        profit = balance - START_BALANCE
        profit_percent = (profit / START_BALANCE) * 100 if START_BALANCE > 0 else 0
        response += f"\n📊 Прибыль: {formatter.value(profit)}₽ ({formatter.percent(profit_percent)}%)"
    
    time_left = price_manager.get_time_until_next_update()
    response += f"\n\n⏱️ Следующее обновление цен через: {time_left}"
    
    await message.answer(response)

@dp.message(Command("price"))
async def price(message: types.Message):
    try:
        ticker = message.text.split()[1].upper()
        # Обновляем цены если нужно
        price_manager.update_prices()
        
        stock_info = price_manager.get_stock_info(ticker)
        if stock_info:
            price_str = formatter.price(stock_info['price'])
            dividend_str = formatter.dividend_yield(stock_info['dividend_yield'])
            
            # Отправляем текст с информацией
            text_response = (
                f"📈 {ticker}\n"
                f"📝 {stock_info['name']}\n"
                f"💰 Текущая цена: {price_str}₽\n"
                f"💵 Дивиденды: {dividend_str} годовых"
            )
            await message.answer(text_response)
            
            # Отправляем график
            await send_chart(message, ticker)
        else:
            await message.answer("❌ Акция не найдена")
    except IndexError:
        await message.answer("Используй: /price [тикер]\nПример: /price SBER")
    except Exception as e:
        await message.answer("❌ Ошибка получения цены")

async def send_chart(message: types.Message, ticker: str):
    """Отправляет график цены акции за последние 7 дней"""
    try:
        # Получаем историю свечей из БД
        candlestick_data = db.get_candlestick_data(ticker, 7)
        
        if not candlestick_data:
            await message.answer("❌ Нет данных для построения графика")
            return

        # Подготавливаем данные для графика
        dates = []
        opens = []
        highs = []
        lows = []
        closes = []
        
        for open_price, high_price, low_price, close_price, timestamp in candlestick_data:
            try:
                # Преобразуем timestamp в datetime
                dt = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                # Если не получилось, используем текущее время
                dt = datetime.now()
            
            dates.append(dt)
            opens.append(open_price)
            highs.append(high_price)
            lows.append(low_price)
            closes.append(close_price)

        # --- Создаем график ---
        plt.style.use('dark_background')  # Темная тема для красоты
        fig, ax = plt.subplots(figsize=(10, 6))
        
        # Строим свечи
        width = 0.6  # Ширина свечи
        for i in range(len(dates)):
            # Тени (wick)
            ax.plot([mdates.date2num(dates[i]), mdates.date2num(dates[i])], 
                   [lows[i], highs[i]], color='white', linewidth=1)
            
            # Тело свечи
            if closes[i] >= opens[i]:
                # Бычья свеча (зеленая)
                ax.bar(mdates.date2num(dates[i]), closes[i]-opens[i], 
                      bottom=opens[i], width=width, color='green', alpha=0.7)
            else:
                # Медвежья свеча (красная)
                ax.bar(mdates.date2num(dates[i]), opens[i]-closes[i], 
                      bottom=closes[i], width=width, color='red', alpha=0.7)
        
        # Форматируем ось X
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%d.%m'))  # ДД.ММ
        ax.xaxis.set_major_locator(mdates.DayLocator(interval=1))  # Каждый день
        plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
        
        # Добавляем сетку
        ax.grid(True, alpha=0.2)
        
        # Подписи
        stock_name = MOEX_STOCKS[ticker].name
        plt.title(f'{ticker} - {stock_name}\nПоследние 7 дней', fontsize=14)
        ax.set_ylabel('Цена (₽)', fontsize=12)
        ax.set_xlabel('Дата', fontsize=12)
        
        # Убираем лишние отступы
        plt.tight_layout()
        
        # --- Сохраняем график в байтовый поток ---
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
        buf.seek(0)
        plt.close(fig)  # Важно: закрываем figure, чтобы не было утечек памяти
        
        # --- Отправляем фото ---
        input_file = types.BufferedInputFile(buf.getvalue(), filename=f"{ticker}_chart.png")
        await message.answer_photo(input_file, caption=f"📊 График цены {ticker} за последние 7 дней")
        
    except Exception as e:
        print(f"Ошибка в команде /price (график): {e}")
        await message.answer("❌ Ошибка при создании графика")

@dp.message(Command("buy"))
async def buy(message: types.Message):
    try:
        parts = message.text.split()
        if len(parts) < 3:
            await message.answer("Используй: /buy [тикер] [кол-во]\nПример: /buy SBER 10")
            return
            
        ticker = parts[1].upper()
        quantity = int(parts[2])
        
        if quantity <= 0:
            await message.answer("❌ Количество должно быть больше 0")
            return
        
        # Обновляем цены если нужно
        price_manager.update_prices()
        
        stock_info = price_manager.get_stock_info(ticker)
        if not stock_info:
            await message.answer("❌ Акция не найдена")
            return
            
        price = stock_info['price']
        cost = price * quantity
        user_id = message.from_user.id
        username = message.from_user.full_name or str(user_id)
        db.create_user(user_id, username)
        balance = db.get_user_balance(user_id)
        
        if balance >= cost:
            if db.update_balance(user_id, -cost) and db.update_stock(user_id, ticker, quantity):
                db.log_transaction(user_id, ticker, quantity, price)
                dividend_str = formatter.dividend_yield(stock_info['dividend_yield'])
                await message.answer(
                    f"✅ Куплено {quantity} акций {ticker} ({stock_info['name']}) по {formatter.price(price)}₽\n"
                    f"💰 Потрачено: {formatter.value(cost)}₽\n"
                    f"💵 Дивиденды: {dividend_str} годовых"
                )
            else:
                await message.answer("❌ Ошибка при оформлении сделки")
        else:
            needed = cost - balance
            await message.answer(f"❌ Недостаточно средств!\nНужно ещё: {formatter.value(needed)}₽")
    except ValueError:
        await message.answer("❌ Неверное количество! Используй число.\nПример: /buy SBER 10")
    except Exception:
        await message.answer("❌ Ошибка! Используй: /buy [тикер] [кол-во]")

@dp.message(Command("sell"))
async def sell(message: types.Message):
    try:
        parts = message.text.split()
        if len(parts) < 3:
            await message.answer("Используй: /sell [тикер] [кол-во]\nПример: /sell SBER 5")
            return
            
        ticker = parts[1].upper()
        quantity = int(parts[2])
        
        if quantity <= 0:
            await message.answer("❌ Количество должно быть больше 0")
            return
        
        # Обновляем цены если нужно
        price_manager.update_prices()
        
        stock_info = price_manager.get_stock_info(ticker)
        if not stock_info:
            await message.answer("❌ Акция не найдена")
            return
            
        price = stock_info['price']
        user_id = message.from_user.id
        username = message.from_user.full_name or str(user_id)
        db.create_user(user_id, username)
        user_stock = db.get_user_stock_quantity(user_id, ticker)
        
        if user_stock >= quantity:
            revenue = price * quantity
            if db.update_balance(user_id, revenue) and db.update_stock(user_id, ticker, -quantity):
                db.log_transaction(user_id, ticker, -quantity, price)
                await message.answer(
                    f"✅ Продано {quantity} акций {ticker} ({stock_info['name']}) по {formatter.price(price)}₽\n"
                    f"💰 Получено: {formatter.value(revenue)}₽"
                )
            else:
                await message.answer("❌ Ошибка при оформлении сделки")
        else:
            await message.answer(f"❌ Недостаточно акций!\nУ тебя: {user_stock} шт")
    except ValueError:
        await message.answer("❌ Неверное количество! Используй число.\nПример: /sell SBER 5")
    except Exception:
        await message.answer("❌ Ошибка! Используй: /sell [тикер] [кол-во]")

@dp.message(Command("history"))
async def history(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.full_name or str(user_id)
    db.create_user(user_id, username)
    
    transactions = db.get_user_transaction_history(user_id, 15)
    
    if not transactions:
        await message.answer("📜 У тебя пока нет сделок.")
        return
        
    response = "📜 Твоя история сделок (последние 15):\n\n"
    for tx in transactions:
        try:
            time_str = tx.timestamp[:-3]  # Убираем секунды
        except:
            time_str = tx.timestamp
            
        action = "Покупка" if tx.quantity > 0 else "Продажа"
        abs_quantity = abs(tx.quantity)
        
        response += f"• {time_str}\n"
        response += f"  {action} {abs_quantity} шт {tx.ticker} по {formatter.price(tx.price)}₽\n\n"
    
    await message.answer(response)

@dp.message(Command("dividends"))
async def dividends(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.full_name or str(user_id)
    db.create_user(user_id, username)
    
    dividend_history = db.get_user_dividend_history(user_id, 15)
    total_dividends = db.get_total_dividends_received(user_id)
    
    if not dividend_history:
        await message.answer("📭 У тебя пока нет выплат дивидендов.")
        return
    
    response = f"📬 Твоя история дивидендов:\n"
    response += f"💰 Всего получено: {formatter.value(total_dividends)}₽\n\n"
    
    for div in dividend_history:
        try:
            time_str = div.payment_date[:-3]  # Убираем секунды
        except:
            time_str = div.payment_date
            
        response += f"• {time_str}\n"
        response += f"  {div.ticker}: {div.quantity} шт = {formatter.value(div.amount)}₽\n\n"
    
    await message.answer(response)

@dp.message(Command("top"))
async def top(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.full_name or str(user_id)
    # Гарантируем, что пользователь есть в БД
    db.create_user(user_id, username) 
    
    users = db.get_all_users()
    if not users:
        await message.answer("📊 Пока нет игроков")
        return
    
    users_with_profit = []
    users_with_loss = []
    
    # Для каждого пользователя считаем прибыль от акций
    for user in users:
        portfolio_items = trading_service.get_user_portfolio_items(user.user_id)
        profit, profit_percent = trading_service.calculate_portfolio_profit(portfolio_items)
        
        user_data = {
            'username': user.username,
            'profit': profit,
            'profit_percent': profit_percent
        }
        
        if profit > 0:
            users_with_profit.append(user_data)
        elif profit < 0:
            users_with_loss.append(user_data)
    
    # Сортируем
    users_with_profit.sort(key=lambda x: x['profit'], reverse=True)
    users_with_loss.sort(key=lambda x: x['profit'])
    
    # Формируем ответ
    response = "🏆 Рейтинги игроков (прибыль от акций):\n\n"
    
    # Топ по прибыли
    response += "<b>Топ по прибыли:</b>\n"
    if users_with_profit:
        for i, user in enumerate(users_with_profit[:5], 1):
            response += f"{i}. {user['username']} - {formatter.value(user['profit'])}₽ ({formatter.percent(user['profit_percent'])}%)\n"
    else:
        response += "Пока никто не заработал\n"
    
    response += "\n"
    
    # Топ по убытку
    response += "<b>Топ по убытку:</b>\n"
    if users_with_loss:
        for i, user in enumerate(users_with_loss[:5], 1):
            response += f"{i}. {user['username']} - {formatter.value(abs(user['profit']))}₽ ({formatter.percent(user['profit_percent'])}%)\n"
    else:
        response += "Пока никто не потерял\n"
    
    await message.answer(response, parse_mode='HTML')

@dp.message(Command("alert"))
async def alert(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.full_name or str(user_id)
    db.create_user(user_id, username)

    try:
        # Разбираем команду: /alert SBER > 320
        parts = message.text.split()
        if len(parts) != 4:
            await message.answer("❌ Используй: /alert [тикер] [> или <] [цена]\nПример: /alert SBER > 320")
            return

        ticker = parts[1].upper()
        condition = parts[2]
        target_price = float(parts[3])

        if condition not in [">", "<"]:
            await message.answer("❌ Условие должно быть > или <")
            return

        # Проверяем, существует ли такая акция
        if ticker not in MOEX_STOCKS:
            await message.answer(f"❌ Акция {ticker} не найдена")
            return

        # Добавляем триггер
        if db.add_alert(user_id, ticker, condition, target_price):
            condition_str = "выше" if condition == ">" else "ниже"
            await message.answer(
                f"✅ Триггер установлен!\n"
                f"Уведомлю, когда {ticker} будет {condition_str} {formatter.price(target_price)}₽"
            )
        else:
            await message.answer("❌ Ошибка при установке триггера")
    except ValueError:
        await message.answer("❌ Цена должна быть числом\nПример: /alert SBER > 320")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

# ==================== ФОНОВЫЕ ЗАДАЧИ ====================
async def check_alerts():
    """Фоновая задача для проверки триггеров"""
    while True:
        try:
            # Получаем все активные триггеры
            alerts = db.get_active_alerts()
            if not alerts:
                await asyncio.sleep(30) # Если триггеров нет, спим дольше
                continue

            # Проверяем каждый триггер
            for alert in alerts:
                current_price = price_manager.get_price(alert.ticker)
                if current_price is None:
                    continue

                # Проверяем условие
                triggered = False
                if alert.condition == ">" and current_price > alert.target_price:
                    triggered = True
                elif alert.condition == "<" and current_price < alert.target_price:
                    triggered = True

                # Если сработал - отправляем уведомление
                if triggered:
                    try:
                        condition_str = "стал выше" if alert.condition == ">" else "стал ниже"
                        msg = (
                            f"🔔 Триггер сработал!\n"
                            f"{alert.ticker} {condition_str} {formatter.price(alert.target_price)}₽\n"
                            f"Текущая цена: {formatter.price(current_price)}₽"
                        )
                        await bot.send_message(alert.user_id, msg)
                        print(f"✉️ Уведомление по триггеру отправлено пользователю {alert.user_id}")
                        
                        # Помечаем триггер как сработавший
                        db.mark_alert_as_triggered(alert.id)
                    except Exception as e:
                        print(f"⚠️ Не удалось отправить уведомление по триггеру {alert.id}: {e}")
            
            await asyncio.sleep(30) # Проверяем каждые 30 секунд
        except Exception as e:
            print(f"⚠️ Ошибка в задаче проверки триггеров: {e}")
            await asyncio.sleep(30)

async def periodic_price_updater():
    """Фоновая задача для автоматического обновления цен"""
    while True:
        try:
            if price_manager.update_prices():
                # Отправляем уведомления всем пользователям
                user_ids = db.get_notified_users()
                if user_ids:
                    notification_text = "🔔 Цены на акции обновились!\nПроверь свой портфель /portfolio"
                    for user_id in user_ids:
                        try:
                            await bot.send_message(user_id, notification_text)
                            print(f"✉️ Уведомление отправлено пользователю {user_id}")
                        except Exception as e:
                            print(f"⚠️ Не удалось отправить уведомление пользователю {user_id}: {e}")
        except Exception as e:
            print(f"⚠️ Ошибка в фоновой задаче обновления цен: {e}")
        
        await asyncio.sleep(PRICE_UPDATE_INTERVAL)

async def dividend_payer():
    """Фоновая задача для выплаты дивидендов"""
    while True:
        try:
            print("💰 Начинаем выплату дивидендов...")
            
            # Получаем всех пользователей
            users = db.get_all_users()
            
            for user in users:
                # Получаем портфель пользователя
                portfolio = db.get_user_portfolio(user.user_id)
                
                # Для каждой акции в портфеле
                for ticker, quantity in portfolio.items():
                    if quantity > 0:
                        stock = MOEX_STOCKS.get(ticker.upper())
                        if stock and stock.dividend_yield > 0:
                            # Рассчитываем дивиденды за период (упрощенно)
                            # Дивидендная доходность / 12 месяцев / (60/PRICE_UPDATE_INTERVAL) периодов в месяц
                            periods_per_month = 60 / PRICE_UPDATE_INTERVAL
                            periods_per_year = periods_per_month * 12
                            dividend_rate = stock.dividend_yield / 100 / periods_per_year
                            
                            # Сумма дивидендов
                            stock_price = price_manager.get_price(ticker)
                            if stock_price is not None:
                                dividend_amount = stock_price * quantity * dividend_rate
                                
                                if dividend_amount > 0.01:  # Минимальная сумма для выплаты
                                    # Выплачиваем дивиденды
                                    if db.pay_dividends(user.user_id, ticker, quantity, dividend_amount):
                                        try:
                                            await bot.send_message(
                                                user.user_id, 
                                                f"💰 Дивиденды по {ticker} ({stock.name})\n"
                                                f"  • {quantity} шт × {formatter.price(stock_price)}₽ × {stock.dividend_yield}% = {formatter.value(dividend_amount)}₽"
                                            )
                                            print(f"✉️ Дивиденды выплачены пользователю {user.user_id} за {ticker}: {dividend_amount}₽")
                                        except Exception as e:
                                            print(f"⚠️ Не удалось отправить уведомление о дивидендах пользователю {user.user_id}: {e}")
            
            print("✅ Выплата дивидендов завершена")
        except Exception as e:
            print(f"⚠️ Ошибка в задаче выплаты дивидендов: {e}")
        
        await asyncio.sleep(DIVIDEND_INTERVAL)

# ==================== ЗАПУСК ====================
async def main():
    print("🚀 Бот запущен! Используем фиксированные данные с МосБиржи + симуляция")
    print(f"✅ Загружено {len(MOEX_STOCKS)} акций")
    print(f"⏱️ Цены будут обновляться раз в {PRICE_UPDATE_INTERVAL // 60} минут(ы)")
    print(f"⏱️ Дивиденды будут выплачиваться раз в {DIVIDEND_INTERVAL // 60} минут(ы)")
    
    # Запускаем фоновые задачи
    asyncio.create_task(periodic_price_updater())
    asyncio.create_task(check_alerts())
    asyncio.create_task(dividend_payer())
    
    # Запускаем бота
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())