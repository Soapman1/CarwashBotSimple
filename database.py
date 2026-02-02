import os
from datetime import datetime, timedelta
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, BigInteger
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

# Получаем URL из переменных окружения (Render подставит автоматически)
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL не установлен! Добавь переменную окружения в Render.")

# Для Render: заменяем postgres:// на postgresql:// (SQLAlchemy требует именно так)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Создаем движок базы данных (NullPool для Render, чтобы не было проблем с соединениями)
engine = create_engine(DATABASE_URL, poolclass=NullPool)
Base = declarative_base()
Session = sessionmaker(bind=engine)

class User(Base):
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True)
    login = Column(String(50), unique=True, nullable=False)
    password = Column(String(100), nullable=False)  # Пароль plain text (как сгенерировал бот)
    carwash_name = Column(String(200))
    owner_name = Column(String(200))
    subscription_end = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)

# Создаем таблицы при импорте (если их нет)
def init_db():
    Base.metadata.create_all(engine)
    print("✅ Таблицы базы данных проверены/созданы")

def create_user(telegram_id, login, password, carwash_name, owner_name):
    """Создание пользователя через бота"""
    session = Session()
    try:
        # Проверяем, нет ли уже такого логина
        existing = session.query(User).filter_by(login=login).first()
        if existing:
            return None, "Логин уже занят"
        
        user = User(
            telegram_id=telegram_id,
            login=login,
            password=password,
            carwash_name=carwash_name,
            owner_name=owner_name,
            subscription_end=None,  # Подписка не активна при регистрации
            is_active=True
        )
        session.add(user)
        session.commit()
        
        return {
            "id": user.id,
            "login": user.login,
            "password": user.password
        }, None
        
    except Exception as e:
        session.rollback()
        print(f"Ошибка создания пользователя: {e}")
        return None, str(e)
    finally:
        session.close()

def get_user_by_telegram(telegram_id):
    """Получить пользователя по Telegram ID"""
    session = Session()
    try:
        return session.query(User).filter_by(telegram_id=telegram_id).first()
    finally:
        session.close()

def update_subscription(telegram_id, months):
    """Продление подписки (тестовый режим - бесплатно)"""
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=telegram_id).first()
        if not user:
            return None
        
        now = datetime.now()
        
        # Если подписка еще активна - продлеваем от даты окончания
        if user.subscription_end and user.subscription_end > now:
            user.subscription_end += timedelta(days=30*months)
        else:
            # Иначе от текущей даты
            user.subscription_end = now + timedelta(days=30*months)
        
        session.commit()
        return user.subscription_end
        
    except Exception as e:
        session.rollback()
        print(f"Ошибка обновления подписки: {e}")
        return None
    finally:
        session.close()

def cancel_subscription(telegram_id):
    """Отмена подписки"""
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=telegram_id).first()
        if user:
            user.subscription_end = None
            user.is_active = False
            session.commit()
            return True
        return False
    except Exception as e:
        session.rollback()
        print(f"Ошибка отмены подписки: {e}")
        return False
    finally:
        session.close()

def get_user_info(telegram_id):
    """Получить инфо для отображения"""
    user = get_user_by_telegram(telegram_id)
    if not user:
        return None
    
    now = datetime.now()
    sub_end = user.subscription_end
    
    status_text = "❌ Нет подписки"
    days_left = 0
    
    if sub_end:
        if sub_end > now:
            days_left = (sub_end - now).days
            status_text = f"✅ Активна (до {sub_end.strftime('%d.%m.%Y')}, осталось {days_left} дн.)"
        else:
            status_text = f"❌ Истекла ({sub_end.strftime('%d.%m.%Y')})"
    
    return {
        "login": user.login,
        "password": user.password,
        "carwash_name": user.carwash_name,
        "owner_name": user.owner_name,
        "status": status_text,
        "has_active_sub": sub_end and sub_end > now
    }

def create_user_admin(login, password, carwash_name, owner_name, days):
    """Создание пользователя админом (без telegram_id, сразу с подпиской)"""
    from datetime import timedelta
    
    session = Session()
    try:
        # Проверяем логин
        existing = session.query(User).filter_by(login=login).first()
        if existing:
            return None, "Логин уже занят"
        
        # Создаем с подпиской
        end_date = datetime.now() + timedelta(days=days)
        
        user = User(
            telegram_id=None,  # Нет привязки к Telegram
            login=login,
            password=password,
            carwash_name=carwash_name,
            owner_name=owner_name,
            subscription_end=end_date,
            is_active=True
        )
        
        session.add(user)
        session.commit()
        
        return {
            "id": user.id,
            "login": user.login,
            "password": user.password
        }, None
        
    except Exception as e:
        session.rollback()
        print(f"Ошибка создания пользователя админом: {e}")
        return None, str(e)
    finally:
        session.close()