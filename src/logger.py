import os
import logging
import logging.handlers

class LoggerFactory:
    @staticmethod
    def create_logger(formatter, handlers):
        logger = logging.getLogger('chatgpt_logger')
        logger.setLevel(logging.INFO)
        for handler in handlers:
            handler.setLevel(logging.DEBUG)
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        return logger

rotate_handler = logging.handlers.RotatingFileHandler("output.log", maxBytes=1000000, backupCount=5)

class ConsoleHandler(logging.StreamHandler):
    pass


formatter = "%(asctime)s [%(levelname)s] %(message)s"
# file_handler = FileHandler('./logs')
console_handler = ConsoleHandler()
logger = LoggerFactory.create_logger(logging.Formatter(formatter), [rotate_handler, console_handler])
