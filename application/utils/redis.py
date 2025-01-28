import redis
from application.config.config import Config

redis_client = redis.StrictRedis(
    host=Config.REDIS_URI,
    port=Config.REDIS_PORT,
    password=Config.REDIS_PASSWORD,
    decode_responses=True,
)


def get_redis_client():
    return redis_client
