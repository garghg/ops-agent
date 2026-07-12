import socket
import os

CONSUMER_NAME = f"{socket.gethostname()}-{os.getpid()}"