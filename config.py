import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
    API_BASE_URL = os.getenv('API_BASE_URL')
    API_USERNAME = os.getenv('API_USERNAME')
    API_PASSWORD = os.getenv('API_PASSWORD')
    TIMEZONE = os.getenv('TIMEZONE', 'Asia/Kuala_Lumpur')
    TIME_RANGE_HOURS = int(os.getenv('TIME_RANGE_HOURS', '1'))
    DEBUG = os.getenv('DEBUG', 'false').lower() == 'true'
    API_TIMEOUT = int(os.getenv('API_TIMEOUT', '30'))
    API_RETRY_ATTEMPTS = int(os.getenv('API_RETRY_ATTEMPTS', '3'))
    
    @classmethod
    def validate(cls):
        """Validate required configuration"""
        required_vars = ['TELEGRAM_TOKEN', 'API_BASE_URL', 'API_USERNAME', 'API_PASSWORD']
        missing = [var for var in required_vars if not getattr(cls, var)]
        
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
        
        print("✅ Configuration loaded successfully")
        print(f"   API URL: {cls.API_BASE_URL}")
        print(f"   Timezone: {cls.TIMEZONE}")
        print(f"   Authentication: Digest")