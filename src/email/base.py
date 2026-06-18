"""Base email provider interface."""

from abc import ABC, abstractmethod


class EmailProvider(ABC):
    """Abstract email provider."""

    @abstractmethod
    async def send(self, to: str, subject: str, html: str, text: str) -> bool:
        """
        Send an email.
        
        Returns: True if successful, False otherwise.
        """
        pass
