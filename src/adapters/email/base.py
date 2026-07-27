from abc import ABC, abstractmethod


class SenderAdapter(ABC):
    @abstractmethod
    def send(self, recipient: str, subject: str, body_html: str) -> bool:
        ...
        
